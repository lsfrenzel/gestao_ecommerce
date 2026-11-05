from __future__ import annotations

import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.permissions import company_view
from apps.core.views import TenantCreateView, TenantDeleteView, TenantUpdateView, get_tenant_object
from apps.sales.models import SalesOrder

from .forms import CustomerForm
from .models import Customer

REVENUE = SalesOrder.REVENUE_STATUSES


@company_view("customers")
def customer_list(request):
    qs = Customer.objects.for_company(request.company).annotate(
        total_spent=Sum("orders__total", filter=Q(orders__status__in=REVENUE)),
        order_count=Count("orders", filter=Q(orders__status__in=REVENUE)),
        last_purchase=Max("orders__order_date", filter=Q(orders__status__in=REVENUE)))
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(email__icontains=term) | Q(phone__icontains=term))
    order = request.GET.get("ordem", "nome")
    qs = qs.order_by({"total": "-total_spent", "recentes": "-last_purchase"}.get(order, "name"))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "customers/list.html", {"page_obj": page, "q": term, "ordem": order})


@company_view("customers")
def customer_detail(request, pk):
    customer = get_tenant_object(Customer, request.company, pk=pk)
    orders = SalesOrder.objects.for_company(request.company).filter(customer=customer).order_by("-order_date")
    summary = customer.purchase_summary()
    avg = summary["total"] / summary["count"] if summary["count"] else None
    log_action("customer.view", request=request, target=customer)
    return render(request, "customers/detail.html", {"customer": customer, "orders": orders[:50],
                                                     "summary": summary, "avg_ticket": avg})


class CustomerCreate(TenantCreateView):
    model = Customer
    form_class = CustomerForm
    module = "customers"
    success_url_name = "customers:list"
    title = "Novo cliente"
    audit_action = "customer"


class CustomerUpdate(TenantUpdateView):
    model = Customer
    form_class = CustomerForm
    module = "customers"
    success_url_name = "customers:list"
    title = "Editar cliente"
    audit_action = "customer"


class CustomerDelete(TenantDeleteView):
    model = Customer
    module = "customers"
    success_url_name = "customers:list"
    audit_action = "customer"
    protected_message = ("Cliente com pedidos não pode ser excluído (os pedidos são registros fiscais/comerciais). "
                         "Para atender a uma solicitação de eliminação (LGPD), use “Anonimizar”.")


@company_view("customers", manager=True)
def customer_export(request, pk):
    """LGPD — portabilidade/acesso: exporta os dados pessoais e o histórico do titular em JSON."""
    customer = get_tenant_object(Customer, request.company, pk=pk)
    orders = SalesOrder.objects.for_company(request.company).filter(customer=customer).prefetch_related(
        "items__product")
    payload = {
        "controlador": request.company.legal_name or request.company.trade_name,
        "gerado_em": timezone.localtime().isoformat(),
        "titular": {"nome": customer.name, "email": customer.email, "telefone": customer.phone,
                    "documento": customer.document, "endereco": customer.address, "cidade": customer.city,
                    "uf": customer.state, "consentimento_marketing": customer.marketing_consent,
                    "cadastrado_em": customer.created_at.isoformat()},
        "pedidos": [{"numero": o.number, "data": o.order_date.isoformat(), "status": o.get_status_display(),
                     "total": str(o.total),
                     "itens": [{"produto": i.product.name, "quantidade": str(i.quantity),
                                "preco_unitario": str(i.unit_price)} for i in o.items.all()]} for o in orders],
    }
    log_action("customer.lgpd_export", request=request, target=customer)
    response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=2),
                            content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="dados-cliente-{customer.pk}.json"'
    return response


@company_view("customers", admin=True)
@require_POST
def customer_anonymize(request, pk):
    """LGPD — eliminação: remove os dados pessoais preservando os pedidos (obrigações legais/contábeis)."""
    customer = get_tenant_object(Customer, request.company, pk=pk)
    with transaction.atomic():
        customer.name = f"Cliente anonimizado #{customer.pk}"
        customer.email = customer.phone = customer.document = customer.address = customer.city = ""
        customer.state = customer.notes = ""
        customer.marketing_consent = False
        customer.anonymized_at = timezone.now()
        customer.save()
        log_action("customer.lgpd_anonymize", request=request, target=customer)
    messages.success(request, "Dados pessoais anonimizados. O histórico de pedidos foi preservado sem identificação.")
    return redirect("customers:detail", pk=pk)
