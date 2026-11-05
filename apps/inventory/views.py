from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.companies.models import Warehouse
from apps.core.audit import log_action
from apps.core.exports import Column, TableReport, csv_response
from apps.core.money import ZERO, to_decimal
from apps.core.periods import resolve_period
from apps.core.permissions import company_view
from apps.core.views import get_tenant_object
from apps.products.models import Category, Product

from . import services
from .alerts import stock_alerts
from .forms import MovementForm, TransferForm
from .models import StockBalance, StockMovement
from .reorder import build_rows


@company_view("inventory")
def overview(request):
    company = request.company
    qs = Product.objects.for_company(company).filter(is_active=True).select_related("category")
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(sku__icontains=term))
    category = request.GET.get("categoria", "")
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    status = request.GET.get("status", "")
    products = list(qs.order_by("name"))
    if status in ("ok", "low", "out", "excess"):
        products = [p for p in products if p.stock_status == status]
    total_value = sum((p.stock_value for p in products), ZERO)
    total_units = sum((p.stock_on_hand for p in products), ZERO)
    total_reserved = sum((p.stock_reserved for p in products), ZERO)
    counts = defaultdict(int)
    for p in Product.objects.for_company(company).filter(is_active=True):
        counts[p.stock_status] += 1
    if request.GET.get("exportar") == "csv":
        report = TableReport("Posição de estoque", [
            Column("SKU"), Column("Produto"), Column("Categoria"), Column("Saldo", "qty"),
            Column("Reservado", "qty"), Column("Disponível", "qty"), Column("Custo unitário", "brl"),
            Column("Valor em estoque", "brl"), Column("Situação")],
            [[p.sku, p.name, p.category.name if p.category else "", p.stock_on_hand, p.stock_reserved,
              p.available_stock, p.total_unit_cost, p.stock_value, p.stock_status_label] for p in products],
            totals=["", "Total", "", total_units, total_reserved, "", "", total_value, ""])
        return csv_response(report)
    balances = {}
    if company.multi_warehouse_enabled:
        for b in StockBalance.objects.filter(product__company=company).select_related("warehouse"):
            balances.setdefault(b.product_id, []).append(b)
    page = Paginator(products, 30).get_page(request.GET.get("page"))
    return render(request, "inventory/overview.html", {
        "page_obj": page, "total_value": total_value, "total_units": total_units, "total_reserved": total_reserved,
        "counts": dict(counts), "categories": Category.objects.for_company(company), "q": term,
        "category": category, "status": status, "balances": balances,
    })


@company_view("inventory")
def movement_create(request):
    company = request.company
    initial = {}
    if request.GET.get("produto", "").isdigit():
        initial["product"] = int(request.GET["produto"])
    if request.GET.get("tipo"):
        initial["kind"] = request.GET["tipo"]
    form = MovementForm(request.POST or None, company=company, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        kind = cd["kind"]
        if kind in (StockMovement.Kind.ADJUST_IN, StockMovement.Kind.ADJUST_OUT, "count") \
                and not request.membership.is_manager_or_admin:
            messages.error(request, "Ajustes de estoque são restritos a gerentes e administradores.")
            return redirect("inventory:overview")
        try:
            with transaction.atomic():
                if kind == "count":
                    movement = services.adjust_to(cd["product"], cd["quantity"], user=request.user,
                                                  warehouse=cd.get("warehouse"), note=cd.get("note", ""))
                else:
                    movement = services.move(cd["product"], kind, cd["quantity"], user=request.user,
                                             warehouse=cd.get("warehouse"), unit_cost=cd.get("unit_cost"),
                                             note=cd.get("note", ""))
                if movement:
                    log_action("stock.movement", request=request, target=cd["product"],
                               details={"tipo": movement.kind, "quantidade": str(movement.quantity)})
        except services.StockError as exc:
            messages.error(request, str(exc))
        else:
            if movement is None:
                messages.info(request, "A contagem é igual ao saldo atual: nenhum ajuste necessário.")
            else:
                messages.success(request, f"{movement.get_kind_display()} registrada. "
                                          f"Saldo atual: {movement.balance_after.normalize():f}.")
            return redirect("inventory:history")
    return render(request, "inventory/movement_form.html", {"form": form})


@company_view("inventory", manager=True, feature="multi_warehouse")
def transfer_create(request):
    company = request.company
    if not company.multi_warehouse_enabled:
        messages.error(request, "Habilite múltiplos depósitos em Configurações para fazer transferências.")
        return redirect("inventory:overview")
    form = TransferForm(request.POST or None, company=company)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.transfer(cd["product"], cd["quantity"], cd["origin"], cd["destination"], user=request.user,
                              note=cd.get("note", ""))
        except services.StockError as exc:
            messages.error(request, str(exc))
        else:
            log_action("stock.transfer", request=request, target=cd["product"],
                       details={"de": str(cd["origin"]), "para": str(cd["destination"]),
                                "quantidade": str(cd["quantity"])})
            messages.success(request, "Transferência registrada.")
            return redirect("inventory:history")
    return render(request, "core/crud_form.html", {"form": form, "title": "Transferência entre depósitos",
                                                   "cancel_url": "/estoque/"})


@company_view("inventory")
def history(request):
    company = request.company
    period = resolve_period(request.GET, default="30d")
    qs = (StockMovement.objects.for_company(company)
          .filter(created_at__gte=period.start_dt, created_at__lt=period.end_dt)
          .select_related("product", "user", "warehouse", "sales_order", "purchase_order"))
    kind = request.GET.get("tipo", "")
    if kind in StockMovement.Kind.values:
        qs = qs.filter(kind=kind)
    product = request.GET.get("produto", "")
    if product.isdigit():
        qs = qs.filter(product_id=int(product))
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(product__name__icontains=term) | Q(product__sku__icontains=term) | Q(note__icontains=term))
    if request.GET.get("exportar") == "csv":
        report = TableReport("Movimentações de estoque", [
            Column("Data", "date"), Column("SKU"), Column("Produto"), Column("Tipo"), Column("Quantidade", "qty"),
            Column("Variação saldo", "qty"), Column("Saldo após", "qty"), Column("Custo unitário", "brl"),
            Column("Usuário"), Column("Observação")],
            [[m.created_at, m.product.sku, m.product.name, m.get_kind_display(), m.quantity, m.on_hand_delta,
              m.balance_after, m.unit_cost, str(m.user or ""), m.note] for m in qs])
        return csv_response(report)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "inventory/history.html", {
        "page_obj": page, "period": period, "kinds": StockMovement.Kind.choices, "kind": kind, "q": term,
    })


@company_view("inventory")
def alerts(request):
    data = stock_alerts(request.company)
    return render(request, "inventory/alerts.html", {"alerts": data, "company": request.company})


@company_view("reorder")
def reorder(request):
    company = request.company
    rows = build_rows(company, Product.objects.for_company(company).filter(is_active=True)
                      .select_related("supplier", "category"))
    show = request.GET.get("mostrar", "repor")
    if show == "repor":
        visible = [r for r in rows if r.needs_reorder]
    elif show == "proximos":
        visible = [r for r in rows if r.near_reorder]
    else:
        visible = rows
    visible.sort(key=lambda r: (r.days_of_cover is None, r.days_of_cover or 0))
    if request.GET.get("exportar") == "csv":
        report = TableReport("Ponto de pedido", [
            Column("SKU"), Column("Produto"), Column("Fornecedor"), Column("Consumo médio/dia", "text"),
            Column("Origem do consumo"), Column("Prazo (dias)", "int"), Column("Estoque de segurança", "qty"),
            Column("Disponível", "qty"), Column("A caminho", "qty"), Column("Ponto de pedido", "qty"),
            Column("Sugestão de compra", "qty")],
            [[r.product.sku, r.product.name, str(r.product.supplier or ""), r.daily_consumption,
              r.consumption_source, r.lead_time_days, r.safety_stock, r.available, r.incoming, r.reorder_point,
              r.suggested_qty] for r in visible])
        return csv_response(report)
    return render(request, "inventory/reorder.html", {
        "rows": visible, "show": show,
        "count_reorder": sum(1 for r in rows if r.needs_reorder),
        "count_near": sum(1 for r in rows if r.near_reorder), "window": company.consumption_window_days,
    })


@company_view("reorder")
def reorder_edit(request, pk):
    """Ajuste manual de prazo, estoque de segurança e consumo médio (via HTMX ou página)."""
    product = get_tenant_object(Product, request.company, pk=pk)
    if request.method == "POST":
        errors = []
        lead = request.POST.get("lead_time_days", "").strip()
        safety = to_decimal(request.POST.get("safety_stock"), None)
        manual = request.POST.get("manual_daily_consumption", "").strip()
        if not lead.isdigit():
            errors.append("Prazo de reposição deve ser um número inteiro de dias.")
        if safety is None or safety < 0:
            errors.append("Estoque de segurança inválido.")
        manual_value = to_decimal(manual, None) if manual else None
        if manual and (manual_value is None or manual_value < 0):
            errors.append("Consumo médio manual inválido.")
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            product.lead_time_days = int(lead)
            product.safety_stock = safety
            product.manual_daily_consumption = manual_value
            product.save(update_fields=["lead_time_days", "safety_stock", "manual_daily_consumption", "updated_at"])
            log_action("reorder.settings", request=request, target=product)
            messages.success(request, f"Parâmetros de reposição de {product.sku} atualizados.")
        return redirect(request.POST.get("next") or "inventory:reorder")
    row = build_rows(request.company, [product])[0]
    return render(request, "inventory/reorder_edit.html", {"product": product, "row": row})


@company_view("purchasing", manager=True, feature="purchasing")
@require_POST
def reorder_create_purchases(request):
    """Gera pedidos de compra em rascunho (um por fornecedor) com as quantidades sugeridas."""
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem
    from apps.purchasing.services import new_order_number

    company = request.company
    ids = [int(i) for i in request.POST.getlist("products") if i.isdigit()]
    products = Product.objects.for_company(company).filter(pk__in=ids).select_related("supplier")
    rows = [r for r in build_rows(company, products) if r.suggested_qty > 0]
    without_supplier = [r.product.sku for r in rows if r.product.supplier is None]
    by_supplier = defaultdict(list)
    for r in rows:
        if r.product.supplier:
            by_supplier[r.product.supplier].append(r)
    created = []
    with transaction.atomic():
        for supplier, items in by_supplier.items():
            order = PurchaseOrder.objects.create(
                company=company, supplier=supplier, number=new_order_number(company),
                order_date=timezone.localdate(), created_by=request.user,
                expected_date=timezone.localdate() + timedelta(days=supplier.default_lead_time_days),
                notes="Gerado pelo ponto de pedido.")
            for r in items:
                PurchaseOrderItem.objects.create(order=order, product=r.product, quantity=r.suggested_qty,
                                                 unit_cost=r.product.acquisition_cost)
            created.append(order)
            log_action("purchase.create_from_reorder", request=request, target=order)
    if created:
        messages.success(request, f"{len(created)} pedido(s) de compra criado(s) em rascunho. Revise antes de enviar.")
    if without_supplier:
        messages.warning(request, "Sem fornecedor cadastrado (não incluídos): " + ", ".join(without_supplier))
    if not created and not without_supplier:
        messages.info(request, "Nenhum produto selecionado precisa de reposição.")
    return redirect("purchasing:orders")


def default_warehouse(company) -> Warehouse:
    return Warehouse.default_for(company)
