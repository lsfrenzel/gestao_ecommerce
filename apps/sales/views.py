from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.csv_import import read_csv
from apps.core.exports import Column, TableReport, csv_response
from apps.core.periods import resolve_period
from apps.core.permissions import company_view
from apps.core.views import get_tenant_object
from apps.inventory.services import StockError
from apps.products.forms import CsvImportForm

from . import services
from .forms import OrderItemFormSet, SalesOrderForm, StatusForm
from .importer import import_orders
from .models import Channel, SalesOrder


@company_view("sales")
def order_list(request):
    company = request.company
    period = resolve_period(request.GET, default="30d")
    qs = (SalesOrder.objects.for_company(company).filter(order_date__gte=period.start, order_date__lte=period.end)
          .select_related("customer"))
    status = request.GET.get("status", "")
    if status in SalesOrder.Status.values:
        qs = qs.filter(status=status)
    channel = request.GET.get("canal", "")
    if channel in Channel.values:
        qs = qs.filter(channel=channel)
    term = request.GET.get("q", "").strip()
    if term:
        cond = Q(customer__name__icontains=term) | Q(external_reference__icontains=term)
        if term.isdigit():
            cond |= Q(number=int(term))
        qs = qs.filter(cond)
    if request.GET.get("exportar") == "csv":
        report = TableReport("Pedidos de venda", [
            Column("Nº", "int"), Column("Data", "date"), Column("Cliente"), Column("Canal"), Column("Status"),
            Column("Pagamento"), Column("Subtotal", "brl"), Column("Desconto", "brl"), Column("Frete cobrado", "brl"),
            Column("Total", "brl"), Column("CMV", "brl"), Column("Despesas variáveis", "brl"),
            Column("Margem de contribuição", "brl")],
            [[o.number, o.order_date, str(o.customer or ""), o.get_channel_display(), o.get_status_display(),
              o.get_payment_method_display(), o.subtotal, o.discount, o.shipping_charged, o.total, o.cogs,
              o.variable_expenses, o.contribution_margin] for o in qs])
        return csv_response(report)
    summary = qs.filter(status__in=SalesOrder.REVENUE_STATUSES).aggregate(revenue=Sum("total"), n=Count("id"))
    by_status = dict(qs.values_list("status").annotate(n=Count("id")))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "sales/list.html", {
        "page_obj": page, "period": period, "statuses": SalesOrder.Status.choices, "channels": Channel.choices,
        "status": status, "channel": channel, "q": term, "summary": summary, "by_status": by_status,
    })


@company_view("sales")
def order_form(request, pk=None):
    company = request.company
    order = get_tenant_object(SalesOrder, company, pk=pk) if pk else None
    if order and not order.is_editable:
        messages.error(request, "Somente pedidos pendentes ou aguardando pagamento podem ser editados.")
        return redirect("sales:detail", pk=order.pk)
    initial_items = [{"product": i.product_id, "quantity": i.quantity, "unit_price": i.unit_price,
                      "discount": i.discount} for i in order.items.all()] if order else []
    initial = None
    if not order:
        initial = {"order_date": timezone.localdate()}
        if request.GET.get("cliente", "").isdigit():  # “Nova venda” a partir da ficha do cliente
            initial["customer"] = int(request.GET["cliente"])
    form = SalesOrderForm(request.POST or None, instance=order, company=company, initial=initial)
    formset = OrderItemFormSet(request.POST or None, prefix="items", form_kwargs={"company": company},
                               initial=initial_items or None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        obj = form.save(commit=False)
        obj.company = company
        if not order:
            obj.status = form.cleaned_data.get("initial_status") or SalesOrder.Status.PENDING
        try:
            services.save_order(obj, formset.items(), user=request.user, request=request,
                                auto_taxes=form.cleaned_data.get("auto_taxes"))
        except (services.SalesError, StockError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Pedido #{obj.number} salvo.")
            return redirect("sales:detail", pk=obj.pk)
    return render(request, "sales/form.html", {"form": form, "formset": formset, "order": order})


@company_view("sales")
def order_detail(request, pk):
    order = get_tenant_object(SalesOrder, request.company, pk=pk)
    return render(request, "sales/detail.html", {
        "order": order, "items": order.items.select_related("product"),
        "status_form": StatusForm(order=order) if order.allowed_transitions() else None,
        "movements": order.stock_movements.select_related("product", "user")[:50],
        "entries": order.financial_entries.select_related("category"),
    })


@company_view("sales")
@require_POST
def order_status(request, pk):
    order = get_tenant_object(SalesOrder, request.company, pk=pk)
    form = StatusForm(request.POST, order=order)
    if not form.is_valid():
        messages.error(request, "Status inválido para este pedido.")
        return redirect("sales:detail", pk=pk)
    new = form.cleaned_data["status"]
    if new in (SalesOrder.Status.CANCELLED, SalesOrder.Status.RETURNED) and not request.membership.is_manager_or_admin:
        messages.error(request, "Cancelamentos e devoluções são restritos a gerentes e administradores.")
        return redirect("sales:detail", pk=pk)
    try:
        services.change_status(order, new, user=request.user, request=request,
                               restock=form.cleaned_data.get("restock", False), note=form.cleaned_data.get("note"))
    except (services.SalesError, StockError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Pedido #{order.number}: {SalesOrder.Status(new).label}.")
    return redirect("sales:detail", pk=pk)


@company_view("sales", manager=True, feature="csv_import")
def order_import(request):
    form = CsvImportForm(request.POST or None, request.FILES or None)
    del form.fields["update_existing"]
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            rows = read_csv(form.cleaned_data["file"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            result = import_orders(request.company, rows, user=request.user)
            messages.success(request, f"{result['created']} pedido(s) importado(s).")
    return render(request, "sales/import.html", {"form": form, "result": result})


@company_view("sales")
def import_template(request):
    report = TableReport("modelo", [Column(c) for c in [
        "pedido", "data", "canal", "status", "cliente", "email", "telefone", "sku", "quantidade", "preco_unitario",
        "desconto", "frete_cobrado", "custo_frete", "comissao", "taxas", "impostos", "forma_pagamento"]],
        [["ML-1001", "31/01/2026", "mercado_livre", "pago", "Maria Souza", "maria@exemplo.com", "", "CAM-001",
          "2", "79,90", "0", "0", "12,50", "22,37", "0", "", "marketplace"]])
    return csv_response(report, "modelo-importacao-pedidos.csv")
