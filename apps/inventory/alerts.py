"""Alertas automáticos calculados a partir dos dados atuais (sempre consistentes, sem tarefas agendadas)."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone


def stock_alerts(company) -> dict[str, list]:
    from apps.products.models import Product

    from .reorder import build_rows

    products = list(Product.objects.for_company(company).filter(is_active=True).select_related("category"))
    idle_since = timezone.now() - timedelta(days=company.idle_stock_days)
    out = [p for p in products if p.stock_on_hand <= 0]
    low = [p for p in products if p.stock_status == "low"]
    excess = [p for p in products if p.max_stock is not None and p.stock_on_hand > p.max_stock]
    idle = [p for p in products if p.stock_on_hand > 0 and (p.last_movement_at is None
                                                            or p.last_movement_at < idle_since)]
    rows = build_rows(company, products)
    reorder = [r for r in rows if r.needs_reorder and r.product.stock_on_hand > 0]
    near = [r for r in rows if r.near_reorder]
    return {"out": out, "low": low, "excess": excess, "idle": idle, "reorder": reorder, "near": near}


def alert_counts(company) -> dict[str, int]:
    """Versão leve (sem consumo médio) para o sino de notificações do topo."""
    from apps.finance.models import FinancialEntry
    from apps.products.models import Product

    base = Product.objects.for_company(company).filter(is_active=True)
    idle_since = timezone.now() - timedelta(days=company.idle_stock_days)
    counts = {
        "out": base.filter(stock_on_hand__lte=0).count(),
        # mesma regra de Product.stock_status == "low"
        "low": base.filter(stock_on_hand__gt=0).filter(
            Q(stock_on_hand__lte=F("stock_reserved"))
            | Q(min_stock__gt=0, stock_on_hand__lte=F("stock_reserved") + F("min_stock"))).count(),
        "excess": base.filter(max_stock__isnull=False, stock_on_hand__gt=F("max_stock")).count(),
        "idle": base.filter(stock_on_hand__gt=0).filter(Q(last_movement_at__isnull=True)
                                                        | Q(last_movement_at__lt=idle_since)).count(),
        "overdue": FinancialEntry.objects.for_company(company)
                   .filter(status=FinancialEntry.Status.OPEN, due_date__lt=timezone.localdate()).count(),
    }
    counts["total"] = counts["out"] + counts["low"] + counts["overdue"]
    return counts
