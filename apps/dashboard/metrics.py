"""Indicadores do dashboard calculados a partir dos dados reais da empresa."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth

from apps.core.money import HUNDRED, ZERO, money, safe_div
from apps.core.periods import Period, month_starts
from apps.finance import services as finance
from apps.finance.models import FinancialEntry, MonthlyGoal, Nature
from apps.products.models import Product
from apps.sales.models import SalesOrder, SalesOrderItem

MONEY = DecimalField(max_digits=18, decimal_places=4)


def revenue_orders(company, start: date, end: date):
    return SalesOrder.objects.for_company(company).filter(
        status__in=SalesOrder.REVENUE_STATUSES, order_date__gte=start, order_date__lte=end)


def sales_kpis(company, start: date, end: date) -> dict:
    orders = revenue_orders(company, start, end)
    agg = orders.aggregate(revenue=Sum("total"), cogs=Sum("cogs"), taxes=Sum("taxes"),
                           commission=Sum("commission"), fees=Sum("fees"), shipping_cost=Sum("shipping_cost"),
                           discount=Sum("discount"))
    agg = {k: money(v or ZERO) for k, v in agg.items()}
    count = orders.count()
    entries = finance.entries_summary(company, start, end)
    order_variable = agg["taxes"] + agg["commission"] + agg["fees"] + agg["shipping_cost"]
    variable = order_variable + entries["variable"]
    revenue = agg["revenue"]
    gross_profit = revenue - agg["cogs"]
    cm = revenue - agg["cogs"] - variable
    fixed = entries["fixed"]
    return {
        "revenue": revenue,
        "orders": count,
        "avg_ticket": safe_div(revenue, count),
        "cogs": agg["cogs"],
        "gross_profit": gross_profit,
        "gross_margin_pct": _pct(gross_profit, revenue),
        "variable_expenses": variable,
        "order_variable_expenses": order_variable,
        "contribution_margin": cm,
        "contribution_margin_pct": _pct(cm, revenue),
        "fixed_expenses": fixed,
        "operating_result": cm - fixed,
        "other_revenue": entries["other_revenue"],
        "pending_orders": SalesOrder.objects.for_company(company).filter(
            status__in=SalesOrder.OPEN_STATUSES, order_date__gte=start, order_date__lte=end).count(),
    }


def _pct(part, whole):
    r = safe_div(part, whole)
    return None if r is None else r * HUNDRED


def stock_kpis(company) -> dict:
    products = Product.objects.for_company(company).filter(is_active=True)
    value = ZERO
    status = {"ok": 0, "low": 0, "out": 0, "excess": 0}
    for p in products.only("stock_on_hand", "stock_reserved", "min_stock", "max_stock", "acquisition_cost",
                           "purchase_freight", "other_costs"):
        value += p.stock_value
        status[p.stock_status] += 1
    return {"value": value, "status": status}


def revenue_series(company, period: Period) -> dict:
    orders = revenue_orders(company, period.start, period.end)
    if period.days <= 62:
        rows = orders.values("order_date").annotate(t=Sum("total"))
        by_day = {r["order_date"]: r["t"] for r in rows}
        labels, values = [], []
        cursor = period.start
        while cursor <= period.end:
            labels.append(cursor.strftime("%d/%m"))
            values.append(str(by_day.get(cursor) or ZERO))
            cursor += timedelta(days=1)
        return {"labels": labels, "values": values}
    rows = orders.annotate(m=TruncMonth("order_date")).values("m").annotate(t=Sum("total"))
    by_month = {(r["m"].year, r["m"].month): r["t"] for r in rows}
    months = month_starts(period.start, period.end)
    return {"labels": [m.strftime("%m/%Y") for m in months],
            "values": [str(by_month.get((m.year, m.month)) or ZERO) for m in months]}


def product_ranking(company, start: date, end: date, limit: int = 10, order_by: str = "-revenue",
                    category=None, channel=None) -> list[dict]:
    items = SalesOrderItem.objects.filter(order__company=company, order__status__in=SalesOrder.REVENUE_STATUSES,
                                          order__order_date__gte=start, order__order_date__lte=end)
    if category:
        items = items.filter(product__category=category)
    if channel:
        items = items.filter(order__channel=channel)
    line_total = ExpressionWrapper(F("quantity") * F("unit_price") - F("discount"), output_field=MONEY)
    line_cost = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=MONEY)
    rows = (items.values("product_id", "product__name", "product__sku")
            .annotate(qty=Sum("quantity"), revenue=Sum(line_total), cost=Sum(line_cost))
            .order_by(order_by, "product__name"))
    if limit:
        rows = rows[:limit]
    out = []
    for r in rows:
        revenue, cost = r["revenue"] or ZERO, r["cost"] or ZERO
        out.append({"product_id": r["product_id"], "name": r["product__name"], "sku": r["product__sku"],
                    "qty": r["qty"] or ZERO, "revenue": revenue, "cost": cost, "gross_profit": revenue - cost,
                    "margin_pct": _pct(revenue - cost, revenue)})
    return out


def monthly_evolution(company, months: int = 6, today: date | None = None) -> dict:
    """Receitas × despesas e margem de contribuição por mês (últimos N meses)."""
    from django.utils import timezone

    today = today or timezone.localdate()
    first = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    month_list = month_starts(first, today)[-months:]
    orders = revenue_orders(company, month_list[0], today)
    rows = (orders.annotate(m=TruncMonth("order_date")).values("m")
            .annotate(revenue=Sum("total"), cogs=Sum("cogs"),
                      var=Sum(F("taxes") + F("commission") + F("fees") + F("shipping_cost"))))
    by_month = {(r["m"].year, r["m"].month): r for r in rows}
    entries = (FinancialEntry.objects.for_company(company).filter(
        type=FinancialEntry.Type.PAYABLE, sales_order__isnull=True, competence_date__gte=month_list[0],
        competence_date__lte=today).exclude(status=FinancialEntry.Status.CANCELLED)
        .annotate(m=TruncMonth("competence_date")).values("m")
        .annotate(fixed=Sum("amount", filter=Q(category__nature=Nature.FIXED)),
                  var=Sum("amount", filter=Q(category__nature=Nature.VARIABLE))))
    exp_by_month = {(r["m"].year, r["m"].month): r for r in entries}
    goals = {(g.month.year, g.month.month): g for g in MonthlyGoal.objects.for_company(company)
             .filter(month__gte=month_list[0])}
    labels, revenue, expenses, cm_pct, result, goal_rev = [], [], [], [], [], []
    for m in month_list:
        key = (m.year, m.month)
        o = by_month.get(key, {})
        e = exp_by_month.get(key, {})
        rev = o.get("revenue") or ZERO
        var = (o.get("var") or ZERO) + (e.get("var") or ZERO)
        fixed = e.get("fixed") or ZERO
        cogs = o.get("cogs") or ZERO
        cm = rev - cogs - var
        labels.append(m.strftime("%m/%Y"))
        revenue.append(str(rev))
        expenses.append(str(cogs + var + fixed))
        pct = _pct(cm, rev)
        cm_pct.append(None if pct is None else str(pct.quantize(Decimal("0.1"))))
        result.append(str(cm - fixed))
        goal = goals.get(key)
        goal_rev.append(str(goal.revenue_target) if goal else None)
    return {"labels": labels, "revenue": revenue, "expenses": expenses, "cm_pct": cm_pct, "result": result,
            "goal_revenue": goal_rev}


def channel_breakdown(company, start: date, end: date) -> list[dict]:
    from apps.sales.models import Channel

    labels = dict(Channel.choices)
    rows = (revenue_orders(company, start, end).values("channel")
            .annotate(revenue=Sum("total"), cogs=Sum("cogs"),
                      var=Sum(F("taxes") + F("commission") + F("fees") + F("shipping_cost")))
            .order_by("-revenue"))
    out = []
    for r in rows:
        count = revenue_orders(company, start, end).filter(channel=r["channel"]).count()
        cm = (r["revenue"] or ZERO) - (r["cogs"] or ZERO) - (r["var"] or ZERO)
        out.append({"channel": labels.get(r["channel"], r["channel"]), "orders": count,
                    "revenue": r["revenue"] or ZERO, "avg_ticket": safe_div(r["revenue"], count),
                    "contribution_margin": cm, "cm_pct": _pct(cm, r["revenue"])})
    return out


def goal_progress(company, today: date) -> dict | None:
    goal = MonthlyGoal.objects.for_company(company).filter(month=today.replace(day=1)).first()
    if not goal:
        return None
    k = sales_kpis(company, today.replace(day=1), today)
    remaining = max(ZERO, goal.revenue_target - k["revenue"])
    avg = k["avg_ticket"]
    return {"goal": goal, "revenue": k["revenue"], "remaining": remaining,
            "pct": _pct(k["revenue"], goal.revenue_target),
            "orders_needed": None if not avg else (remaining / avg).to_integral_value(rounding=ROUND_CEILING),
            "result": k["operating_result"]}
