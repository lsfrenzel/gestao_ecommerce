"""
Ponto de pedido.

Ponto de pedido = Consumo médio diário × Prazo de reposição (dias) + Estoque de segurança

- Consumo médio diário: calculado pelo histórico de saídas por venda (vendas − devoluções/estornos)
  na janela configurada da empresa, ou o valor manual informado no produto.
- Quantidade sugerida de reposição: o suficiente para voltar ao estoque máximo (se definido) ou, sem
  máximo, para cobrir o ponto de pedido mais um ciclo de reposição — descontando o estoque disponível e
  o que já está a caminho em pedidos de compra abertos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.money import ZERO, to_decimal


def reorder_point(daily_consumption, lead_time_days, safety_stock) -> Decimal:
    daily = to_decimal(daily_consumption, ZERO)
    lead = to_decimal(lead_time_days, ZERO)
    safety = to_decimal(safety_stock, ZERO)
    if daily < 0 or lead < 0 or safety < 0:
        raise ValueError("Consumo, prazo e estoque de segurança não podem ser negativos.")
    return daily * lead + safety


def suggested_quantity(*, daily_consumption, lead_time_days, safety_stock, available, incoming=ZERO,
                       max_stock=None) -> Decimal:
    rop = reorder_point(daily_consumption, lead_time_days, safety_stock)
    position = to_decimal(available, ZERO) + to_decimal(incoming, ZERO)
    if position > rop:
        return ZERO
    max_stock = to_decimal(max_stock, None)
    target = max_stock if max_stock and max_stock > rop else rop + to_decimal(daily_consumption) * to_decimal(lead_time_days)
    qty = target - position
    return max(ZERO, qty.to_integral_value(rounding=ROUND_CEILING))


@dataclass
class ReorderRow:
    product: object
    daily_consumption: Decimal
    consumption_source: str  # "histórico" | "manual"
    lead_time_days: int
    safety_stock: Decimal
    available: Decimal
    reserved: Decimal
    incoming: Decimal
    reorder_point: Decimal
    suggested_qty: Decimal
    days_of_cover: Decimal | None

    @property
    def needs_reorder(self) -> bool:
        return self.available + self.incoming <= self.reorder_point and (self.reorder_point > 0 or self.available <= 0)

    @property
    def near_reorder(self) -> bool:
        """Dentro de 20% acima do ponto de pedido."""
        return not self.needs_reorder and self.reorder_point > 0 and \
            self.available + self.incoming <= self.reorder_point * Decimal("1.2")


def consumption_by_product(company, window_days: int | None = None) -> dict[int, Decimal]:
    from .models import StockMovement

    K = StockMovement.Kind
    days = window_days or company.consumption_window_days
    since = timezone.now() - timedelta(days=days)
    rows = (StockMovement.objects.for_company(company).filter(created_at__gte=since)
            .values("product_id")
            .annotate(out=Sum("quantity", filter=Q(kind=K.SALE)),
                      back=Sum("quantity", filter=Q(kind__in=[K.RETURN, K.SALE_CANCEL]))))
    result = {}
    for row in rows:
        net = (row["out"] or ZERO) - (row["back"] or ZERO)
        result[row["product_id"]] = max(ZERO, net) / Decimal(days)
    return result


def incoming_by_product(company) -> dict[int, Decimal]:
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem

    rows = (PurchaseOrderItem.objects
            .filter(order__company=company, order__status__in=PurchaseOrder.OPEN_STATUSES)
            .values("product_id").annotate(ordered=Sum("quantity"), received=Sum("received_quantity")))
    return {r["product_id"]: max(ZERO, (r["ordered"] or ZERO) - (r["received"] or ZERO)) for r in rows}


def build_rows(company, products=None) -> list[ReorderRow]:
    from apps.products.models import Product

    products = products if products is not None else Product.objects.for_company(company).filter(is_active=True)
    consumption = consumption_by_product(company)
    incoming = incoming_by_product(company)
    rows = []
    for p in products:
        if p.manual_daily_consumption is not None:
            daily, source = p.manual_daily_consumption, "manual"
        else:
            daily, source = consumption.get(p.id, ZERO), "histórico"
        available = p.available_stock
        inc = incoming.get(p.id, ZERO)
        rop = reorder_point(daily, p.lead_time_days, p.safety_stock)
        rows.append(ReorderRow(
            product=p, daily_consumption=daily.quantize(Decimal("0.0001")), consumption_source=source,
            lead_time_days=p.lead_time_days, safety_stock=p.safety_stock, available=available,
            reserved=p.stock_reserved, incoming=inc, reorder_point=rop.quantize(Decimal("0.01")),
            suggested_qty=suggested_quantity(daily_consumption=daily, lead_time_days=p.lead_time_days,
                                             safety_stock=p.safety_stock, available=available, incoming=inc,
                                             max_stock=p.max_stock),
            days_of_cover=(available / daily).quantize(Decimal("0.1")) if daily > 0 else None,
        ))
    return rows
