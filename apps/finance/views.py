from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.exports import Column, TableReport, csv_response
from apps.core.money import ZERO
from apps.core.periods import month_starts, resolve_period
from apps.core.permissions import company_view
from apps.core.views import TenantCreateView, TenantDeleteView, TenantListView, TenantUpdateView, get_tenant_object
from apps.dashboard.metrics import sales_kpis

from . import services
from .forms import CategoryForm, EntryForm, FixedExpenseForm, GenerateMonthForm, GoalForm, SettleForm
from .models import FinancialCategory, FinancialEntry, FixedExpense, MonthlyGoal


@company_view("finance", feature="finance")
def overview(request):
    company = request.company
    period = resolve_period(request.GET, default="month")
    kpis = sales_kpis(company, period.start, period.end)
    flow = services.cash_flow(company, period.start, period.end)
    open_ = services.open_summary(company)
    # Comparativo mensal (últimos 12 meses): entradas × saídas efetivas
    today = timezone.localdate()
    first = (today.replace(day=1) - timedelta(days=330)).replace(day=1)
    months = month_starts(first, today)[-12:]
    rows = (FinancialEntry.objects.for_company(company)
            .filter(status=FinancialEntry.Status.SETTLED, settled_at__gte=months[0])
            .annotate(m=TruncMonth("settled_at")).values("m")
            .annotate(inflow=Sum("settled_amount", filter=Q(type=FinancialEntry.Type.RECEIVABLE)),
                      outflow=Sum("settled_amount", filter=Q(type=FinancialEntry.Type.PAYABLE))))
    by_month = {(r["m"].year, r["m"].month): r for r in rows}
    comparison = []
    for m in months:
        r = by_month.get((m.year, m.month), {})
        inflow, outflow = r.get("inflow") or ZERO, r.get("outflow") or ZERO
        comparison.append({"month": m, "inflow": inflow, "outflow": outflow, "net": inflow - outflow})
    chart = json.dumps({"labels": [c["month"].strftime("%m/%Y") for c in comparison],
                        "inflow": [str(c["inflow"]) for c in comparison],
                        "outflow": [str(c["outflow"]) for c in comparison],
                        "net": [str(c["net"]) for c in comparison]})
    upcoming = (FinancialEntry.objects.for_company(company).filter(status=FinancialEntry.Status.OPEN)
                .select_related("category").order_by("due_date")[:10])
    return render(request, "finance/overview.html", {
        "period": period, "kpis": kpis, "flow": flow, "open": open_, "comparison": comparison, "chart": chart,
        "upcoming": upcoming, "by_category": services.expenses_by_category(company, period.start, period.end),
        "today": today,
    })


@company_view("finance", feature="finance")
def entry_list(request, kind):
    company = request.company
    entry_type = FinancialEntry.Type.PAYABLE if kind == "pagar" else FinancialEntry.Type.RECEIVABLE
    qs = FinancialEntry.objects.for_company(company).filter(type=entry_type).select_related("category")
    status = request.GET.get("situacao", "abertos")
    today = timezone.localdate()
    if status == "abertos":
        qs = qs.filter(status=FinancialEntry.Status.OPEN)
    elif status == "vencidos":
        qs = qs.filter(status=FinancialEntry.Status.OPEN, due_date__lt=today)
    elif status == "liquidados":
        qs = qs.filter(status=FinancialEntry.Status.SETTLED)
    if request.GET.get("period"):
        period = resolve_period(request.GET)
        qs = qs.filter(due_date__gte=period.start, due_date__lte=period.end)
    category = request.GET.get("categoria", "")
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(description__icontains=term) | Q(counterparty__icontains=term))
    qs = qs.order_by("due_date", "id") if status in ("abertos", "vencidos") else qs.order_by("-due_date", "-id")
    total = qs.aggregate(t=Sum("amount"))["t"] or ZERO
    if request.GET.get("exportar") == "csv":
        report = TableReport("Contas a pagar" if kind == "pagar" else "Contas a receber", [
            Column("Vencimento", "date"), Column("Descrição"), Column("Categoria"), Column("Cliente/fornecedor"),
            Column("Valor", "brl"), Column("Situação"), Column("Liquidado em", "date"), Column("Valor liquidado", "brl")],
            [[e.due_date, e.description, e.category.name, e.counterparty, e.amount, e.get_status_display(),
              e.settled_at, e.settled_amount] for e in qs], totals=["", "Total", "", "", total, "", "", ""])
        return csv_response(report)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "finance/entries.html", {
        "page_obj": page, "kind": kind, "entry_type": entry_type, "status": status, "total": total, "q": term,
        "categories": FinancialCategory.objects.for_company(company), "category": category, "today": today,
        "settle_form": SettleForm(initial={"settled_at": today}),
    })


@company_view("finance", feature="finance")
def entry_form(request, pk=None):
    company = request.company
    entry = get_tenant_object(FinancialEntry, company, pk=pk) if pk else None
    if entry and (entry.status != FinancialEntry.Status.OPEN or entry.sales_order_id or entry.purchase_order_id):
        messages.error(request, "Somente lançamentos manuais em aberto podem ser editados.")
        return redirect("finance:overview")
    today = timezone.localdate()
    initial = {"type": FinancialEntry.Type.RECEIVABLE if request.GET.get("tipo") == "receber"
               else FinancialEntry.Type.PAYABLE, "competence_date": today, "due_date": today}
    form = EntryForm(request.POST or None, instance=entry, company=company, initial=initial if not entry else None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.company = company
        obj.created_by = obj.created_by or request.user
        obj.save()
        log_action("finance.entry_save", request=request, target=obj, details={"valor": str(obj.amount)})
        messages.success(request, "Lançamento salvo.")
        return redirect("finance:entries", kind="pagar" if obj.type == FinancialEntry.Type.PAYABLE else "receber")
    return render(request, "core/crud_form.html", {"form": form, "title": "Lançamento financeiro",
                                                   "cancel_url": "/financeiro/"})


@company_view("finance", feature="finance")
@require_POST
def entry_settle(request, pk):
    entry = get_tenant_object(FinancialEntry, request.company, pk=pk)
    form = SettleForm(request.POST)
    kind = "pagar" if entry.type == FinancialEntry.Type.PAYABLE else "receber"
    if not form.is_valid():
        messages.error(request, "Informe data e valor válidos.")
        return redirect("finance:entries", kind=kind)
    try:
        services.settle(entry, settled_at=form.cleaned_data["settled_at"], amount=form.cleaned_data["amount"])
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        log_action("finance.settle", request=request, target=entry,
                   details={"valor": str(form.cleaned_data["amount"])})
        messages.success(request, "Pagamento registrado." if kind == "pagar" else "Recebimento registrado.")
    return redirect("finance:entries", kind=kind)


@company_view("finance", manager=True, feature="finance")
@require_POST
def entry_cancel(request, pk):
    entry = get_tenant_object(FinancialEntry, request.company, pk=pk)
    kind = "pagar" if entry.type == FinancialEntry.Type.PAYABLE else "receber"
    if entry.status != FinancialEntry.Status.OPEN:
        messages.error(request, "Somente lançamentos em aberto podem ser cancelados.")
    else:
        entry.status = FinancialEntry.Status.CANCELLED
        entry.save(update_fields=["status", "updated_at"])
        log_action("finance.cancel", request=request, target=entry)
        messages.success(request, "Lançamento cancelado.")
    return redirect("finance:entries", kind=kind)


class CategoryList(TenantListView):
    model = FinancialCategory
    module = "finance"
    title = "Categorias financeiras"
    search_fields = ("name",)
    columns = [("Nome", "name", "text"), ("Natureza", "nature", "display"), ("Do sistema", "is_system", "bool")]
    create_url_name = "finance:category_new"
    edit_url_name = "finance:category_edit"
    delete_url_name = "finance:category_delete"


class CategoryCreate(TenantCreateView):
    model = FinancialCategory
    form_class = CategoryForm
    module = "finance"
    success_url_name = "finance:categories"
    title = "Nova categoria financeira"


class CategoryUpdate(TenantUpdateView):
    model = FinancialCategory
    form_class = CategoryForm
    module = "finance"
    success_url_name = "finance:categories"
    title = "Editar categoria financeira"

    def get_queryset(self):
        return super().get_queryset().filter(is_system=False)


class CategoryDelete(TenantDeleteView):
    model = FinancialCategory
    module = "finance"
    success_url_name = "finance:categories"
    protected_message = "Categoria em uso por lançamentos ou despesas fixas não pode ser excluída."

    def get_queryset(self):
        return super().get_queryset().filter(is_system=False)


@company_view("finance", feature="finance")
def fixed_expenses(request):
    company = request.company
    form = GenerateMonthForm(request.POST or None, initial={"month": timezone.localdate()})
    if request.method == "POST":
        if not request.membership.is_manager_or_admin:
            messages.error(request, "Ação restrita a gerentes e administradores.")
        elif form.is_valid():
            n = services.generate_fixed_expenses(company, form.cleaned_data["month"], user=request.user)
            log_action("finance.generate_fixed", request=request, details={"mes": str(form.cleaned_data["month"]),
                                                                          "gerados": n})
            messages.success(request, f"{n} conta(s) a pagar gerada(s)." if n else
                             "Nenhuma conta nova: as despesas deste mês já foram geradas.")
        return redirect("finance:fixed")
    expenses = FixedExpense.objects.for_company(company).select_related("category")
    return render(request, "finance/fixed.html", {"expenses": expenses, "form": form,
                                                  "total": services.monthly_fixed_total(company)})


class FixedCreate(TenantCreateView):
    model = FixedExpense
    form_class = FixedExpenseForm
    module = "finance"
    success_url_name = "finance:fixed"
    title = "Nova despesa fixa mensal"
    audit_action = "fixed_expense"


class FixedUpdate(TenantUpdateView):
    model = FixedExpense
    form_class = FixedExpenseForm
    module = "finance"
    success_url_name = "finance:fixed"
    title = "Editar despesa fixa"
    audit_action = "fixed_expense"


class FixedDelete(TenantDeleteView):
    model = FixedExpense
    module = "finance"
    success_url_name = "finance:fixed"
    audit_action = "fixed_expense"


class GoalList(TenantListView):
    model = MonthlyGoal
    module = "finance"
    title = "Metas mensais"
    columns = [("Mês", "month", "date"), ("Meta de faturamento", "revenue_target", "brl"),
               ("Meta de resultado", "profit_target", "brl")]
    create_url_name = "finance:goal_new"
    edit_url_name = "finance:goal_edit"
    delete_url_name = "finance:goal_delete"


class GoalCreate(TenantCreateView):
    model = MonthlyGoal
    form_class = GoalForm
    module = "finance"
    success_url_name = "finance:goals"
    title = "Nova meta mensal"


class GoalUpdate(TenantUpdateView):
    model = MonthlyGoal
    form_class = GoalForm
    module = "finance"
    success_url_name = "finance:goals"
    title = "Editar meta mensal"


class GoalDelete(TenantDeleteView):
    model = MonthlyGoal
    module = "finance"
    success_url_name = "finance:goals"
