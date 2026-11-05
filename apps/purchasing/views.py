from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.inventory.services import StockError
from apps.core.audit import log_action
from apps.core.permissions import company_view
from apps.core.views import TenantCreateView, TenantDeleteView, TenantListView, TenantUpdateView, get_tenant_object

from . import services
from .forms import PurchaseItemFormSet, PurchaseOrderForm, ReceiveForm, SupplierForm
from .models import PurchaseOrder, Supplier


class SupplierList(TenantListView):
    model = Supplier
    module = "suppliers"
    title = "Fornecedores"
    search_fields = ("name", "cnpj", "contact_name", "email")
    columns = [("Nome", "name", "text"), ("CNPJ", "cnpj", "text"), ("Contato", "contact_name", "text"),
               ("Telefone", "phone", "text"), ("Prazo (dias)", "default_lead_time_days", "text"),
               ("Ativo", "is_active", "bool")]
    create_url_name = "purchasing:supplier_new"
    detail_url_name = "purchasing:supplier_detail"
    edit_url_name = "purchasing:supplier_edit"
    delete_url_name = "purchasing:supplier_delete"


class SupplierCreate(TenantCreateView):
    model = Supplier
    form_class = SupplierForm
    module = "suppliers"
    success_url_name = "purchasing:suppliers"
    title = "Novo fornecedor"
    audit_action = "supplier"


class SupplierUpdate(TenantUpdateView):
    model = Supplier
    form_class = SupplierForm
    module = "suppliers"
    success_url_name = "purchasing:suppliers"
    title = "Editar fornecedor"
    audit_action = "supplier"


class SupplierDelete(TenantDeleteView):
    model = Supplier
    module = "suppliers"
    success_url_name = "purchasing:suppliers"
    audit_action = "supplier"
    protected_message = "Fornecedor com pedidos de compra não pode ser excluído. Marque-o como inativo."


@company_view("suppliers")
def supplier_detail(request, pk):
    supplier = get_tenant_object(Supplier, request.company, pk=pk)
    orders = PurchaseOrder.objects.for_company(request.company).filter(supplier=supplier).prefetch_related("items")
    received = [o for o in orders if o.status in (PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.PARTIAL)]
    return render(request, "purchasing/supplier_detail.html", {
        "supplier": supplier, "orders": orders[:50],
        "total_purchased": sum((o.total for o in received), 0),
        "products": supplier.products.filter(is_active=True)[:50],
    })


@company_view("purchasing", feature="purchasing")
def order_list(request):
    qs = (PurchaseOrder.objects.for_company(request.company).select_related("supplier")
          .prefetch_related("items"))
    status = request.GET.get("status", "")
    if status in PurchaseOrder.Status.values:
        qs = qs.filter(status=status)
    term = request.GET.get("q", "").strip()
    if term:
        cond = Q(supplier__name__icontains=term)
        if term.isdigit():
            cond |= Q(number=int(term))
        qs = qs.filter(cond)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    open_orders = PurchaseOrder.objects.for_company(request.company).filter(status__in=PurchaseOrder.OPEN_STATUSES)
    return render(request, "purchasing/order_list.html", {
        "page_obj": page, "statuses": PurchaseOrder.Status.choices, "status": status, "q": term,
        "open_count": open_orders.count(),
        "late_count": open_orders.filter(expected_date__lt=timezone.localdate()).count(),
    })


@company_view("purchasing", manager=True, feature="purchasing")
def order_form(request, pk=None):
    company = request.company
    order = get_tenant_object(PurchaseOrder, company, pk=pk) if pk else None
    if order and not order.is_editable:
        messages.error(request, "Somente pedidos em rascunho podem ser editados.")
        return redirect("purchasing:order_detail", pk=order.pk)
    initial = {"order_date": timezone.localdate()}
    if request.GET.get("fornecedor", "").isdigit():
        initial["supplier"] = int(request.GET["fornecedor"])
    form = PurchaseOrderForm(request.POST or None, instance=order, company=company, initial=initial)
    formset = PurchaseItemFormSet(request.POST or None, instance=order or PurchaseOrder(company=company),
                                  prefix="items", form_kwargs={"company": company})
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.company = company
            if obj.pk is None:
                obj.number = services.new_order_number(company)
                obj.created_by = request.user
            obj.save()
            formset.instance = obj
            formset.save()
            log_action("purchase.save", request=request, target=obj)
        messages.success(request, f"Pedido de compra #{obj.number} salvo como rascunho.")
        return redirect("purchasing:order_detail", pk=obj.pk)
    return render(request, "purchasing/order_form.html", {"form": form, "formset": formset, "order": order})


@company_view("purchasing", feature="purchasing")
def order_detail(request, pk):
    order = get_tenant_object(PurchaseOrder, request.company, pk=pk)
    receive_form = ReceiveForm(company=request.company, order=order) if order.can_receive else None
    return render(request, "purchasing/order_detail.html", {
        "order": order, "items": order.items.select_related("product"), "receive_form": receive_form,
        "movements": order.stock_movements.select_related("product", "user")[:50],
        "entries": order.financial_entries.all(),
    })


@company_view("purchasing", manager=True, feature="purchasing")
@require_POST
def order_action(request, pk, action):
    order = get_tenant_object(PurchaseOrder, request.company, pk=pk)
    try:
        if action == "enviar":
            services.mark_ordered(order, request=request)
            messages.success(request, "Pedido marcado como enviado ao fornecedor.")
        elif action == "cancelar":
            services.cancel(order, request=request)
            messages.success(request, "Pedido cancelado.")
        elif action == "receber":
            form = ReceiveForm(request.POST, company=request.company, order=order)
            if not form.is_valid():
                messages.error(request, "Quantidades inválidas: " + "; ".join(
                    f"{e}" for errs in form.errors.values() for e in errs))
            else:
                services.receive(order, form.quantities(), user=request.user,
                                 warehouse=form.cleaned_data.get("warehouse"), request=request)
                messages.success(request, "Recebimento registrado: estoque e custo médio atualizados, "
                                          "conta a pagar gerada no financeiro.")
        elif action == "excluir":
            if not order.is_editable:
                raise services.PurchaseError("Somente rascunhos podem ser excluídos.")
            number = order.number
            order.delete()
            log_action("purchase.delete", request=request, details={"numero": number})
            messages.success(request, f"Rascunho #{number} excluído.")
            return redirect("purchasing:orders")
    except (services.PurchaseError, StockError) as exc:
        messages.error(request, str(exc))
    return redirect("purchasing:order_detail", pk=pk)
