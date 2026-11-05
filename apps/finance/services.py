"""
Regras financeiras e indicadores.

Conceitos (não confundir):
- Faturamento: valor total dos pedidos PAGOS no período (pedido pendente não é receita).
- Lucro bruto: faturamento − custo dos produtos vendidos (CMV).
- Margem de contribuição: faturamento − CMV − despesas variáveis (impostos, comissões, taxas, frete pago
  pela loja e demais despesas variáveis lançadas).
- Resultado operacional: margem de contribuição − despesas fixas.
- Fluxo de caixa: entradas e saídas efetivamente pagas/recebidas (data de liquidação).
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.money import ZERO, money

from .models import FinancialCategory, FinancialEntry, FixedExpense, Nature

DEFAULT_CATEGORIES = [
    ("Vendas de produtos", Nature.REVENUE),
    ("Outras receitas", Nature.REVENUE),
    ("Despesas de venda (comissões, taxas, frete)", Nature.VARIABLE),
    ("Impostos sobre vendas", Nature.VARIABLE),
    ("Estornos e devoluções", Nature.VARIABLE),
    ("Marketing e anúncios", Nature.VARIABLE),
    ("Compras de mercadorias", Nature.PURCHASE),
    ("Aluguel", Nature.FIXED),
    ("Internet e telefone", Nature.FIXED),
    ("Energia", Nature.FIXED),
    ("Salários e encargos", Nature.FIXED),
    ("Pró-labore", Nature.FIXED),
    ("Sistemas e assinaturas", Nature.FIXED),
    ("Contabilidade", Nature.FIXED),
    ("Outras despesas administrativas", Nature.FIXED),
]
SYSTEM_SALES = "Vendas de produtos"
SYSTEM_SALES_EXPENSES = "Despesas de venda (comissões, taxas, frete)"
SYSTEM_TAXES = "Impostos sobre vendas"
SYSTEM_REFUNDS = "Estornos e devoluções"
SYSTEM_PURCHASES = "Compras de mercadorias"


def ensure_default_categories(company) -> None:
    for name, nature in DEFAULT_CATEGORIES:
        FinancialCategory.objects.get_or_create(company=company, name=name,
                                                defaults={"nature": nature, "is_system": True})


def _category(company, name: str) -> FinancialCategory:
    ensure_default_categories(company)
    return FinancialCategory.objects.get(company=company, name=name)


@transaction.atomic
def register_sale_payment(order, *, user=None) -> None:
    """Pedido pago: receita recebida + despesas variáveis da venda já liquidadas."""
    today = timezone.localdate()
    company = order.company
    counterparty = str(order.customer or "")
    if order.total > 0:
        FinancialEntry.objects.create(
            company=company, type=FinancialEntry.Type.RECEIVABLE, category=_category(company, SYSTEM_SALES),
            description=f"Pedido #{order.number} ({order.get_channel_display()})", amount=order.total,
            competence_date=order.order_date, due_date=today, status=FinancialEntry.Status.SETTLED,
            settled_at=today, settled_amount=order.total, counterparty=counterparty, sales_order=order,
            created_by=user)
    expenses = order.commission + order.fees + order.shipping_cost
    if expenses > 0:
        FinancialEntry.objects.create(
            company=company, type=FinancialEntry.Type.PAYABLE,
            category=_category(company, SYSTEM_SALES_EXPENSES),
            description=f"Comissões, taxas e frete — pedido #{order.number}", amount=expenses,
            competence_date=order.order_date, due_date=today, status=FinancialEntry.Status.SETTLED,
            settled_at=today, settled_amount=expenses, sales_order=order, created_by=user)
    if order.taxes > 0:
        FinancialEntry.objects.create(
            company=company, type=FinancialEntry.Type.PAYABLE, category=_category(company, SYSTEM_TAXES),
            description=f"Impostos — pedido #{order.number}", amount=order.taxes,
            competence_date=order.order_date, due_date=_next_month_day(order.order_date, 20),
            sales_order=order, created_by=user)


@transaction.atomic
def register_sale_refund(order, *, user=None, reason: str = "Estorno") -> None:
    """Cancelamento/devolução de pedido pago: estorno ao cliente e cancelamento de impostos em aberto."""
    today = timezone.localdate()
    company = order.company
    if order.total > 0:
        FinancialEntry.objects.create(
            company=company, type=FinancialEntry.Type.PAYABLE, category=_category(company, SYSTEM_REFUNDS),
            description=f"{reason} — pedido #{order.number}", amount=order.total, competence_date=today,
            due_date=today, status=FinancialEntry.Status.SETTLED, settled_at=today, settled_amount=order.total,
            counterparty=str(order.customer or ""), sales_order=order, created_by=user)
    FinancialEntry.objects.filter(company=company, sales_order=order, status=FinancialEntry.Status.OPEN,
                                  category__name=SYSTEM_TAXES).update(status=FinancialEntry.Status.CANCELLED)


def payable_for_purchase(order, amount: Decimal, *, user=None) -> FinancialEntry | None:
    if amount <= 0:
        return None
    today = timezone.localdate()
    return FinancialEntry.objects.create(
        company=order.company, type=FinancialEntry.Type.PAYABLE,
        category=_category(order.company, SYSTEM_PURCHASES),
        description=f"Compra #{order.number} — {order.supplier}", amount=amount, competence_date=today,
        due_date=today, counterparty=str(order.supplier), purchase_order=order, created_by=user)


def _next_month_day(base: date, day: int) -> date:
    year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


@transaction.atomic
def generate_fixed_expenses(company, month: date, *, user=None) -> int:
    """Gera as contas a pagar do mês a partir das despesas fixas ativas (sem duplicar)."""
    created = 0
    first = month.replace(day=1)
    last_day = calendar.monthrange(first.year, first.month)[1]
    for expense in FixedExpense.objects.for_company(company).filter(is_active=True, monthly_amount__gt=0):
        exists = FinancialEntry.objects.filter(company=company, fixed_expense=expense,
                                               competence_date__year=first.year,
                                               competence_date__month=first.month).exists()
        if exists:
            continue
        FinancialEntry.objects.create(
            company=company, type=FinancialEntry.Type.PAYABLE, category=expense.category,
            description=f"{expense.name} — {first:%m/%Y}", amount=expense.monthly_amount, competence_date=first,
            due_date=first.replace(day=min(expense.due_day, last_day)), fixed_expense=expense, created_by=user)
        created += 1
    return created


def monthly_fixed_total(company) -> Decimal:
    return money(FixedExpense.objects.for_company(company).filter(is_active=True).aggregate(
        t=Sum("monthly_amount"))["t"] or ZERO)


@transaction.atomic
def settle(entry: FinancialEntry, *, settled_at: date, amount: Decimal | None = None) -> None:
    if entry.status != FinancialEntry.Status.OPEN:
        raise ValueError("Somente lançamentos em aberto podem ser liquidados.")
    entry.status = FinancialEntry.Status.SETTLED
    entry.settled_at = settled_at
    entry.settled_amount = money(amount if amount is not None else entry.amount)
    entry.save(update_fields=["status", "settled_at", "settled_amount", "updated_at"])


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------
def entries_summary(company, start: date, end: date) -> dict:
    """Totais por competência (excluindo lançamentos gerados automaticamente por pedidos)."""
    base = (FinancialEntry.objects.for_company(company)
            .exclude(status=FinancialEntry.Status.CANCELLED)
            .filter(competence_date__gte=start, competence_date__lte=end))
    manual = base.filter(sales_order__isnull=True)
    agg = manual.aggregate(
        fixed=Sum("amount", filter=Q(category__nature=Nature.FIXED, type=FinancialEntry.Type.PAYABLE)),
        variable=Sum("amount", filter=Q(category__nature=Nature.VARIABLE, type=FinancialEntry.Type.PAYABLE)),
        other_revenue=Sum("amount", filter=Q(type=FinancialEntry.Type.RECEIVABLE)),
    )
    return {k: money(v or ZERO) for k, v in agg.items()}


def cash_flow(company, start: date, end: date) -> dict:
    settled = (FinancialEntry.objects.for_company(company)
               .filter(status=FinancialEntry.Status.SETTLED, settled_at__gte=start, settled_at__lte=end))
    agg = settled.aggregate(
        inflow=Sum("settled_amount", filter=Q(type=FinancialEntry.Type.RECEIVABLE)),
        outflow=Sum("settled_amount", filter=Q(type=FinancialEntry.Type.PAYABLE)),
    )
    inflow, outflow = money(agg["inflow"] or ZERO), money(agg["outflow"] or ZERO)
    return {"inflow": inflow, "outflow": outflow, "net": inflow - outflow}


def open_summary(company) -> dict:
    today = timezone.localdate()
    base = FinancialEntry.objects.for_company(company).filter(status=FinancialEntry.Status.OPEN)
    agg = base.aggregate(
        payable=Sum("amount", filter=Q(type=FinancialEntry.Type.PAYABLE)),
        receivable=Sum("amount", filter=Q(type=FinancialEntry.Type.RECEIVABLE)),
        overdue=Sum("amount", filter=Q(type=FinancialEntry.Type.PAYABLE, due_date__lt=today)),
        overdue_count=Count("id", filter=Q(due_date__lt=today)),
    )
    return {k: v or ZERO for k, v in agg.items()}


def expenses_by_category(company, start: date, end: date) -> list[dict]:
    rows = (FinancialEntry.objects.for_company(company)
            .filter(type=FinancialEntry.Type.PAYABLE, competence_date__gte=start, competence_date__lte=end)
            .exclude(status=FinancialEntry.Status.CANCELLED)
            .values("category__name", "category__nature").annotate(total=Sum("amount")).order_by("-total"))
    labels = dict(Nature.choices)
    return [{"category": r["category__name"], "nature": labels.get(r["category__nature"], ""),
             "total": r["total"] or ZERO} for r in rows]
