"""
Fluxo do pedido de venda e seus efeitos no estoque e no financeiro (sempre em transação atômica).

Regras de estoque:
- Pendente / Aguardando pagamento → RESERVA (se a empresa usa reserva em pendentes).
- Pago (ou qualquer status posterior) → BAIXA do estoque (consome a reserva, se houver).
- Cancelado → libera a reserva; se já havia baixa, ESTORNA as unidades ao estoque.
- Devolvido → devolução de cliente (entrada) se `restock=True`.

Regras financeiras:
- Receita só é registrada quando o pedido é pago (pendentes nunca contam como receita recebida).
- Ao cancelar/devolver um pedido já pago, é registrado o estorno ao cliente.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.money import ZERO, money, to_decimal
from apps.inventory import services as stock
from apps.inventory.models import StockMovement
from apps.products.models import Product

from .models import SalesOrder, SalesOrderItem

S = SalesOrder.Status
K = StockMovement.Kind


class SalesError(Exception):
    pass


def recalc_totals(order: SalesOrder) -> None:
    items = list(order.items.all())
    subtotal = sum((i.total for i in items), ZERO)
    order.subtotal = money(subtotal)
    order.total = money(subtotal - order.discount + order.shipping_charged)
    order.cogs = money(sum((i.cost_total for i in items), ZERO))
    if order.total < 0:
        raise SalesError("O desconto não pode ser maior que o valor do pedido.")


def default_taxes(order: SalesOrder) -> Decimal:
    rate = order.company.default_tax_rate or ZERO
    return money((order.subtotal - order.discount) * rate / Decimal("100"))


@transaction.atomic
def save_order(order: SalesOrder, items: list[dict], *, user=None, request=None,
               auto_taxes: bool = False) -> SalesOrder:
    """
    Cria ou atualiza um pedido com seus itens. `items`: [{product, quantity, unit_price, discount}].
    Itens só podem ser alterados enquanto o pedido estiver pendente/aguardando pagamento.
    """
    if not items:
        raise SalesError("Inclua ao menos um produto no pedido.")
    company = order.company
    creating = order.pk is None
    target_status = order.status

    if creating:
        order.number = company.take_sequence("next_order_number")
        order.created_by = user
        order.status = S.PENDING
        order.save()
    else:
        current = SalesOrder.objects.select_for_update().get(pk=order.pk, company=company)
        if not current.is_editable:
            raise SalesError("Itens só podem ser alterados em pedidos pendentes ou aguardando pagamento.")
        _release_reservations(current, user)
        order.stock_state = SalesOrder.StockState.NONE
        order.status = current.status
        order.items.all().delete()
        order.save()

    seen = set()
    for data in items:
        product = data["product"]
        if product.company_id != company.id:
            raise SalesError("Produto inválido.")
        if product.pk in seen:
            raise SalesError(f"O produto {product.sku} foi informado mais de uma vez.")
        seen.add(product.pk)
        qty = to_decimal(data["quantity"], ZERO)
        price = to_decimal(data["unit_price"], ZERO)
        discount = to_decimal(data.get("discount"), ZERO)
        if qty <= 0 or price < 0 or discount < 0:
            raise SalesError("Quantidade deve ser positiva e valores não podem ser negativos.")
        if discount > qty * price:
            raise SalesError(f"O desconto do item {product.sku} é maior que o valor do item.")
        SalesOrderItem.objects.create(order=order, product=product, quantity=qty, unit_price=price,
                                      discount=discount, unit_cost=product.total_unit_cost)
    recalc_totals(order)
    if auto_taxes:
        order.taxes = default_taxes(order)
    order.save()

    if order.status in SalesOrder.OPEN_STATUSES and company.reserve_on_pending:
        _reserve(order, user)
    if target_status != order.status:
        change_status(order, target_status, user=user, request=request)
    log_action("sale.create" if creating else "sale.update", request=request, actor=user, company=company,
               target=order, details={"total": str(order.total), "status": order.status})
    return order


def _reserve(order: SalesOrder, user) -> None:
    if order.stock_state != SalesOrder.StockState.NONE:
        return
    for item in order.items.select_related("product"):
        try:
            stock.reserve(item.product, item.quantity, user=user, sales_order=order,
                          note=f"Pedido #{order.number}")
        except stock.StockError as exc:
            raise SalesError(str(exc)) from exc
    order.stock_state = SalesOrder.StockState.RESERVED
    order.save(update_fields=["stock_state", "updated_at"])


def _release_reservations(order: SalesOrder, user) -> None:
    if order.stock_state != SalesOrder.StockState.RESERVED:
        return
    for item in order.items.select_related("product"):
        product = Product.objects.get(pk=item.product_id)
        if product.stock_reserved > 0:
            stock.release(product, item.quantity, user=user, sales_order=order,
                          note=f"Pedido #{order.number}")
    order.stock_state = SalesOrder.StockState.NONE
    order.save(update_fields=["stock_state", "updated_at"])


def _deduct(order: SalesOrder, user) -> None:
    if order.stock_state == SalesOrder.StockState.DEDUCTED:
        return
    consume = order.stock_state == SalesOrder.StockState.RESERVED
    for item in order.items.select_related("product"):
        try:
            stock.move(item.product, K.SALE, item.quantity, user=user, sales_order=order,
                       unit_cost=item.unit_cost, consume_reserved=consume, note=f"Pedido #{order.number}")
        except stock.StockError as exc:
            raise SalesError(str(exc)) from exc
    order.stock_state = SalesOrder.StockState.DEDUCTED
    order.save(update_fields=["stock_state", "updated_at"])


def _restore(order: SalesOrder, user, kind: str) -> None:
    if order.stock_state != SalesOrder.StockState.DEDUCTED:
        return
    for item in order.items.select_related("product"):
        stock.move(item.product, kind, item.quantity, user=user, sales_order=order, unit_cost=item.unit_cost,
                   note=f"Pedido #{order.number}")
    order.stock_state = SalesOrder.StockState.RESTORED
    order.save(update_fields=["stock_state", "updated_at"])


@transaction.atomic
def change_status(order: SalesOrder, new_status: str, *, user=None, request=None, restock: bool = True,
                  note: str = "") -> SalesOrder:
    from apps.finance import services as finance

    locked = SalesOrder.objects.select_for_update().get(pk=order.pk, company=order.company)
    old = locked.status
    if new_status == old:
        return locked
    if new_status not in SalesOrder.TRANSITIONS.get(old, []):
        raise SalesError(f"Não é possível alterar o pedido de “{S(old).label}” para “{S(new_status).label}”.")

    was_paid = old in SalesOrder.REVENUE_STATUSES
    if new_status in SalesOrder.OPEN_STATUSES:
        if order.company.reserve_on_pending:
            _reserve(locked, user)
    elif new_status in SalesOrder.REVENUE_STATUSES:
        _deduct(locked, user)
        if not was_paid:
            locked.paid_at = timezone.now()
            finance.register_sale_payment(locked, user=user)
    elif new_status == S.CANCELLED:
        if locked.stock_state == SalesOrder.StockState.RESERVED:
            _release_reservations(locked, user)
        elif locked.stock_state == SalesOrder.StockState.DEDUCTED:
            _restore(locked, user, K.SALE_CANCEL)
        if was_paid:
            finance.register_sale_refund(locked, user=user, reason="Cancelamento")
    elif new_status == S.RETURNED:
        if restock:
            _restore(locked, user, K.RETURN)
        if was_paid:
            finance.register_sale_refund(locked, user=user, reason="Devolução")

    locked.status = new_status
    if note:
        locked.notes = (locked.notes + "\n" if locked.notes else "") + \
            f"[{timezone.localtime():%d/%m/%Y %H:%M}] {note}"
    locked.save()
    log_action("sale.status", request=request, actor=user, company=locked.company, target=locked,
               details={"de": old, "para": new_status, "repor_estoque": restock})
    order.refresh_from_db()
    return order
