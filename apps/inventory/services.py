"""
Regras de estoque. ÚNICO lugar que altera saldos.

- Toda alteração gera um `StockMovement` (histórico) dentro de uma transação atômica.
- Produto e saldo do depósito são bloqueados com SELECT … FOR UPDATE (no PostgreSQL) para evitar
  condições de corrida; constraints no banco impedem saldos negativos.
- Estoque disponível = saldo físico − quantidade reservada em pedidos não concluídos.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.companies.models import Warehouse
from apps.core.money import ZERO, to_decimal
from apps.products.models import Product

from .models import StockBalance, StockMovement

K = StockMovement.Kind
INBOUND = {K.INITIAL, K.IN, K.PURCHASE, K.ADJUST_IN, K.RETURN, K.SALE_CANCEL, K.TRANSFER_IN}
OUTBOUND = {K.OUT, K.SALE, K.ADJUST_OUT, K.LOSS, K.TRANSFER_OUT}
MANUAL_KINDS = [K.IN, K.OUT, K.ADJUST_IN, K.ADJUST_OUT, K.RETURN, K.LOSS]


class StockError(Exception):
    """Operação de estoque inválida (mensagem amigável para o usuário)."""


def _qty(value) -> Decimal:
    qty = to_decimal(value, None)
    if qty is None or qty <= 0:
        raise StockError("A quantidade deve ser maior que zero.")
    return qty.quantize(Decimal("0.001"))


def _lock_product(product: Product) -> Product:
    return Product.objects.select_for_update().get(pk=product.pk, company_id=product.company_id)


def _lock_balance(product: Product, warehouse: Warehouse) -> StockBalance:
    if warehouse.company_id != product.company_id:
        raise StockError("Depósito inválido.")
    balance, _ = StockBalance.objects.get_or_create(product=product, warehouse=warehouse)
    return StockBalance.objects.select_for_update().get(pk=balance.pk)


@transaction.atomic
def move(product: Product, kind: str, quantity, *, user=None, warehouse: Warehouse | None = None,
         unit_cost=None, note: str = "", sales_order=None, purchase_order=None,
         transfer_group=None, consume_reserved: bool = False) -> StockMovement:
    """
    Entrada ou saída física (altera o saldo).
    `consume_reserved=True` (venda paga de um pedido que estava reservado) baixa o saldo e a reserva juntos.
    """
    qty = _qty(quantity)
    if kind not in INBOUND | OUTBOUND:
        raise StockError("Tipo de movimentação inválido.")
    locked = _lock_product(product)
    warehouse = warehouse or Warehouse.default_for(locked.company)
    balance = _lock_balance(locked, warehouse)
    delta = qty if kind in INBOUND else -qty
    reserved_delta = ZERO
    if consume_reserved:
        reserved_delta = -min(qty, locked.stock_reserved)
        locked.stock_reserved += reserved_delta

    if delta < 0:
        if balance.on_hand + delta < 0:
            raise StockError(f"Saldo insuficiente de {locked.sku} no depósito {warehouse}: "
                             f"disponível {balance.on_hand.normalize():f}, solicitado {qty.normalize():f}.")
        # Saídas não podem consumir unidades reservadas para outros pedidos.
        if kind != K.TRANSFER_OUT and (locked.stock_on_hand + delta) - locked.stock_reserved < 0:
            raise StockError(f"A saída consumiria unidades reservadas em pedidos de {locked.sku}. "
                             f"Disponível: {locked.available_stock.normalize():f}.")

    balance.on_hand += delta
    balance.save(update_fields=["on_hand"])
    locked.stock_on_hand += delta
    locked.last_movement_at = timezone.now()
    locked.save(update_fields=["stock_on_hand", "stock_reserved", "last_movement_at", "updated_at"])

    movement = StockMovement.objects.create(
        company=locked.company, product=locked, warehouse=warehouse, kind=kind, quantity=qty,
        on_hand_delta=delta, reserved_delta=reserved_delta, balance_after=locked.stock_on_hand, reserved_after=locked.stock_reserved,
        unit_cost=locked.total_unit_cost if unit_cost is None else to_decimal(unit_cost),
        note=note[:255], user=user, sales_order=sales_order, purchase_order=purchase_order,
        transfer_group=transfer_group,
    )
    product.stock_on_hand, product.stock_reserved = locked.stock_on_hand, locked.stock_reserved
    return movement


@transaction.atomic
def adjust_to(product: Product, counted, *, user=None, warehouse: Warehouse | None = None,
              note: str = "") -> StockMovement | None:
    """Ajuste de inventário: informa a contagem física e registra a diferença como ajuste (+/−)."""
    counted = to_decimal(counted, None)
    if counted is None or counted < 0:
        raise StockError("A contagem deve ser zero ou positiva.")
    locked = _lock_product(product)
    warehouse = warehouse or Warehouse.default_for(locked.company)
    balance = _lock_balance(locked, warehouse)
    diff = counted - balance.on_hand
    if diff == 0:
        return None
    kind = K.ADJUST_IN if diff > 0 else K.ADJUST_OUT
    note = note or f"Inventário: contagem {counted.normalize():f}"
    if diff < 0 and locked.available_stock + diff < 0:
        raise StockError("O ajuste deixaria o saldo abaixo das quantidades reservadas em pedidos. "
                         "Cancele ou conclua os pedidos antes.")
    return move(locked, kind, abs(diff), user=user, warehouse=warehouse, note=note)


@transaction.atomic
def transfer(product: Product, quantity, origin: Warehouse, destination: Warehouse, *, user=None,
             note: str = "") -> tuple[StockMovement, StockMovement]:
    if not product.company.multi_warehouse_enabled:
        raise StockError("Transferências exigem o recurso de múltiplos depósitos habilitado.")
    if origin.pk == destination.pk:
        raise StockError("Origem e destino devem ser diferentes.")
    if not destination.is_active:
        raise StockError("O depósito de destino está inativo.")
    group = uuid.uuid4()
    out = move(product, K.TRANSFER_OUT, quantity, user=user, warehouse=origin, note=note or f"Para {destination}",
               transfer_group=group)
    inn = move(product, K.TRANSFER_IN, quantity, user=user, warehouse=destination,
               note=note or f"De {origin}", transfer_group=group)
    return out, inn


@transaction.atomic
def reserve(product: Product, quantity, *, user=None, sales_order=None, note: str = "") -> StockMovement:
    qty = _qty(quantity)
    locked = _lock_product(product)
    if locked.available_stock < qty:
        raise StockError(f"Estoque disponível insuficiente para {locked.sku}: "
                         f"disponível {locked.available_stock.normalize():f}, pedido {qty.normalize():f}.")
    locked.stock_reserved += qty
    locked.save(update_fields=["stock_reserved", "updated_at"])
    product.stock_reserved = locked.stock_reserved
    return StockMovement.objects.create(
        company=locked.company, product=locked, kind=K.RESERVE, quantity=qty, reserved_delta=qty,
        balance_after=locked.stock_on_hand, reserved_after=locked.stock_reserved, user=user,
        sales_order=sales_order, note=note[:255],
    )


@transaction.atomic
def release(product: Product, quantity, *, user=None, sales_order=None, note: str = "") -> StockMovement:
    qty = _qty(quantity)
    locked = _lock_product(product)
    qty = min(qty, locked.stock_reserved)
    if qty <= 0:
        raise StockError("Não há reserva a liberar.")
    locked.stock_reserved -= qty
    locked.save(update_fields=["stock_reserved", "updated_at"])
    product.stock_reserved = locked.stock_reserved
    return StockMovement.objects.create(
        company=locked.company, product=locked, kind=K.RELEASE, quantity=qty, reserved_delta=-qty,
        balance_after=locked.stock_on_hand, reserved_after=locked.stock_reserved, user=user,
        sales_order=sales_order, note=note[:255],
    )


def warehouses_for(company) -> list[Warehouse]:
    qs = Warehouse.objects.filter(company=company, is_active=True)
    if not company.multi_warehouse_enabled:
        qs = qs.filter(is_default=True)
    return list(qs)


def inventory_value(company) -> Decimal:
    total = ZERO
    for product in Product.objects.for_company(company).filter(stock_on_hand__gt=0):
        total += product.stock_value
    return total
