"""Fluxo de compras: emissão, recebimento (parcial/total) com entrada controlada no estoque."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.money import ZERO, money, to_decimal
from apps.inventory import services as stock
from apps.inventory.models import StockMovement
from apps.products.models import PriceHistory, Product

from .models import PurchaseOrder, PurchaseOrderItem


class PurchaseError(Exception):
    pass


def new_order_number(company) -> int:
    return company.take_sequence("next_purchase_number")


@transaction.atomic
def mark_ordered(order: PurchaseOrder, request=None) -> None:
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise PurchaseError("Somente rascunhos podem ser enviados.")
    if not order.items.exists():
        raise PurchaseError("Inclua ao menos um produto antes de enviar o pedido.")
    order.status = PurchaseOrder.Status.ORDERED
    order.save(update_fields=["status", "updated_at"])
    log_action("purchase.ordered", request=request, company=order.company, target=order)


@transaction.atomic
def cancel(order: PurchaseOrder, request=None) -> None:
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status in (PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CANCELLED):
        raise PurchaseError("Este pedido não pode mais ser cancelado.")
    if order.items.filter(received_quantity__gt=0).exists():
        raise PurchaseError("O pedido já teve recebimentos. Os itens recebidos permanecem no estoque; "
                            "registre uma saída/devolução ao fornecedor se necessário.")
    order.status = PurchaseOrder.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    log_action("purchase.cancel", request=request, company=order.company, target=order)


def _weighted(old_qty: Decimal, old_value: Decimal, new_qty: Decimal, new_value: Decimal) -> Decimal:
    total = old_qty + new_qty
    if total <= 0:
        return new_value
    return (old_qty * old_value + new_qty * new_value) / total


@transaction.atomic
def receive(order: PurchaseOrder, quantities: dict[int, Decimal], *, user=None, warehouse=None,
            request=None, update_costs: bool = True) -> list[StockMovement]:
    """
    Recebe quantidades por item. Cada unidade recebida entra no estoque com custo de entrada
    (custo unitário + rateio proporcional do frete e das outras despesas pelo valor dos itens).
    O custo do produto é atualizado pelo custo médio ponderado.
    """
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if not order.can_receive:
        raise PurchaseError("O pedido precisa estar enviado (ou parcialmente recebido) para receber itens.")
    items = {i.pk: i for i in order.items.select_related("product")}
    items_total = sum((i.total for i in items.values()), ZERO)
    extra_rate = (order.freight + order.other_costs) / items_total if items_total > 0 else ZERO

    movements, received_value = [], ZERO
    for item_id, raw_qty in quantities.items():
        qty = to_decimal(raw_qty, ZERO)
        if qty <= 0:
            continue
        item = items.get(int(item_id))
        if item is None:
            raise PurchaseError("Item inválido para este pedido.")
        if qty > item.pending_quantity:
            raise PurchaseError(f"Quantidade recebida de {item.product.sku} maior que a pendente "
                                f"({item.pending_quantity.normalize():f}).")
        allocated_extra = item.unit_cost * extra_rate
        product = Product.objects.select_for_update().get(pk=item.product_id)
        if update_costs:
            old_cost = product.total_unit_cost
            on_hand = product.stock_on_hand
            product.acquisition_cost = _weighted(on_hand, product.acquisition_cost, qty, item.unit_cost)
            product.purchase_freight = _weighted(on_hand, product.purchase_freight, qty, allocated_extra)
            product.other_costs = _weighted(on_hand, product.other_costs, qty, ZERO)
            product.acquisition_cost = product.acquisition_cost.quantize(Decimal("0.0001"))
            product.purchase_freight = product.purchase_freight.quantize(Decimal("0.0001"))
            product.other_costs = product.other_costs.quantize(Decimal("0.0001"))
            product.save(update_fields=["acquisition_cost", "purchase_freight", "other_costs", "updated_at"])
            if product.total_unit_cost != old_cost:
                PriceHistory.objects.create(product=product, changed_by=user, old_cost=old_cost,
                                            new_cost=product.total_unit_cost,
                                            old_sale_price=product.sale_price, new_sale_price=product.sale_price,
                                            reason=f"Custo médio — recebimento da compra #{order.number}")
        movements.append(stock.move(product, StockMovement.Kind.PURCHASE, qty, user=user, warehouse=warehouse,
                                    unit_cost=item.unit_cost + allocated_extra, purchase_order=order,
                                    note=f"Compra #{order.number} — {order.supplier}"))
        item.received_quantity += qty
        item.save(update_fields=["received_quantity"])
        received_value += qty * (item.unit_cost + allocated_extra)

    if not movements:
        raise PurchaseError("Informe ao menos uma quantidade a receber.")

    all_received = all(i.pending_quantity <= 0 for i in order.items.all())
    order.status = PurchaseOrder.Status.RECEIVED if all_received else PurchaseOrder.Status.PARTIAL
    if all_received:
        order.received_at = timezone.now()
    order.save(update_fields=["status", "received_at", "updated_at"])

    from apps.finance.services import payable_for_purchase

    payable_for_purchase(order, money(received_value), user=user)
    log_action("purchase.receive", request=request, actor=user, company=order.company, target=order,
               details={"itens": {str(k): str(v) for k, v in quantities.items()}, "status": order.status})
    return movements


def history_for_supplier(supplier):
    return PurchaseOrder.objects.filter(company=supplier.company, supplier=supplier).prefetch_related("items")


__all__ = ["PurchaseError", "receive", "mark_ordered", "cancel", "new_order_number", "PurchaseOrderItem"]
