"""
API REST mínima para integrações futuras (marketplaces, loja virtual, ERPs).

Autenticação: `Authorization: Bearer <token>` (token da empresa criado em Configurações → Integrações).
O token identifica a empresa: não é possível acessar dados de outra empresa pela API.

Endpoints (JSON):
- GET  /api/v1/produtos/?q=&page=          lista produtos (estoque disponível e preço)
- GET  /api/v1/produtos/<sku>/              detalhes de um produto
- POST /api/v1/pedidos/                     registra um pedido de venda
"""

from __future__ import annotations

import json
import logging
from datetime import date

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core import ratelimit
from apps.core.audit import client_ip, log_action
from apps.core.money import to_decimal

logger = logging.getLogger(__name__)


def _authenticate(request):
    from apps.companies.models import ApiToken

    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        return None
    raw = header[7:].strip()
    token = (ApiToken.objects.select_related("company").filter(key_hash=ApiToken.hash(raw), is_active=True)
             .first())
    if token is None or token.company.is_suspended:
        return None
    ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token


def api_view(view):
    def wrapper(request, *args, **kwargs):
        ip = client_ip(request) or "?"
        if ratelimit.is_limited("api", ip, 600):
            return JsonResponse({"erro": "Limite de requisições excedido."}, status=429)
        ratelimit.hit("api", ip, 60)
        token = _authenticate(request)
        if token is None:
            return JsonResponse({"erro": "Token inválido ou ausente."}, status=401)
        request.api_token = token
        request.company = token.company
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return csrf_exempt(wrapper)


def _product_json(p) -> dict:
    return {"sku": p.sku, "nome": p.name, "preco": str(p.sale_price), "estoque": str(p.stock_on_hand),
            "reservado": str(p.stock_reserved), "disponivel": str(p.available_stock), "ativo": p.is_active,
            "categoria": p.category.name if p.category else None, "codigo_barras": p.barcode}


@api_view
@require_GET
def products(request):
    from apps.products.models import Product

    qs = Product.objects.for_company(request.company).select_related("category").order_by("name")
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(sku__icontains=term))
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return JsonResponse({"resultados": [_product_json(p) for p in page], "pagina": page.number,
                         "paginas": page.paginator.num_pages})


@api_view
@require_GET
def product_detail(request, sku):
    from apps.products.models import Product

    product = Product.objects.for_company(request.company).filter(sku=sku).select_related("category").first()
    if product is None:
        return JsonResponse({"erro": "Produto não encontrado."}, status=404)
    return JsonResponse(_product_json(product))


@api_view
@require_POST
def create_order(request):
    """
    Corpo: {"canal": "mercado_livre", "referencia": "123", "data": "2026-01-31", "status": "paid",
            "forma_pagamento": "pix", "cliente": {"nome": "...", "email": "..."},
            "itens": [{"sku": "ABC", "quantidade": "1", "preco_unitario": "99.90", "desconto": "0"}],
            "frete_cobrado": "0", "custo_frete": "0", "comissao": "0", "taxas": "0"}
    """
    from apps.customers.models import Customer
    from apps.products.models import Product
    from apps.sales.models import Channel, PaymentMethod, SalesOrder
    from apps.sales.services import SalesError, save_order

    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)
    company = request.company
    channel = data.get("canal", Channel.OTHER)
    status = data.get("status", SalesOrder.Status.PENDING)
    if channel not in Channel.values:
        return JsonResponse({"erro": f"Canal inválido. Use: {', '.join(Channel.values)}"}, status=400)
    if status not in (SalesOrder.Status.PENDING, SalesOrder.Status.AWAITING_PAYMENT, SalesOrder.Status.PAID):
        return JsonResponse({"erro": "Status inicial deve ser pending, awaiting_payment ou paid."}, status=400)
    reference = str(data.get("referencia", ""))[:60]
    if reference and SalesOrder.objects.for_company(company).filter(channel=channel,
                                                                   external_reference=reference).exists():
        return JsonResponse({"erro": "Pedido já registrado (referência duplicada)."}, status=409)
    items = []
    for raw in data.get("itens") or []:
        product = Product.objects.for_company(company).filter(sku=str(raw.get("sku", ""))).first()
        if product is None:
            return JsonResponse({"erro": f"SKU não encontrado: {raw.get('sku')}"}, status=400)
        items.append({"product": product, "quantity": to_decimal(raw.get("quantidade"), None),
                      "unit_price": to_decimal(raw.get("preco_unitario"), None),
                      "discount": to_decimal(raw.get("desconto"))})
    if any(i["quantity"] is None or i["unit_price"] is None for i in items):
        return JsonResponse({"erro": "Itens exigem quantidade e preco_unitario."}, status=400)
    try:
        order_date = date.fromisoformat(data["data"]) if data.get("data") else timezone.localdate()
    except (ValueError, TypeError):
        return JsonResponse({"erro": "Data inválida (use AAAA-MM-DD)."}, status=400)
    payment = data.get("forma_pagamento", PaymentMethod.OTHER)
    order = SalesOrder(company=company, channel=channel, status=status, order_date=order_date,
                       external_reference=reference,
                       payment_method=payment if payment in PaymentMethod.values else PaymentMethod.OTHER,
                       shipping_charged=to_decimal(data.get("frete_cobrado")),
                       shipping_cost=to_decimal(data.get("custo_frete")),
                       commission=to_decimal(data.get("comissao")), fees=to_decimal(data.get("taxas")))
    try:
        with transaction.atomic():  # cliente e pedido são gravados juntos (ou nenhum dos dois)
            if isinstance(data.get("cliente"), dict) and data["cliente"].get("nome"):
                c = data["cliente"]
                email = str(c.get("email", ""))[:254]
                existing = Customer.objects.for_company(company).filter(email__iexact=email).first() if email else None
                order.customer = existing or Customer.objects.create(
                    company=company, name=str(c["nome"])[:150], email=email, phone=str(c.get("telefone", ""))[:20])
            save_order(order, items, auto_taxes=True)
    except SalesError as exc:
        return JsonResponse({"erro": str(exc)}, status=422)
    log_action("api.order_create", company=company, target=order, details={"token": request.api_token.name})
    return JsonResponse({"numero": order.number, "total": str(order.total), "status": order.status}, status=201)
