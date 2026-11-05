"""
Importação de pedidos por CSV (uma linha por item; linhas com o mesmo `pedido` formam um pedido).

Colunas: pedido, data (DD/MM/AAAA ou AAAA-MM-DD), canal, status, cliente, email, telefone, sku, quantidade,
preco_unitario, desconto, frete_cobrado, custo_frete, comissao, taxas, forma_pagamento.
Valores de frete/comissão/taxas são lidos da primeira linha de cada pedido.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from django.db import transaction

from apps.core.money import to_decimal
from apps.customers.models import Customer
from apps.products.models import Product

from .models import Channel, PaymentMethod, SalesOrder
from .services import SalesError, save_order

CHANNEL_ALIASES = {
    "loja": Channel.OWN_STORE, "loja virtual": Channel.OWN_STORE, "site": Channel.OWN_STORE,
    "mercado livre": Channel.MERCADO_LIVRE, "mercadolivre": Channel.MERCADO_LIVRE, "ml": Channel.MERCADO_LIVRE,
    "shopee": Channel.SHOPEE, "amazon": Channel.AMAZON, "instagram": Channel.INSTAGRAM,
    "whatsapp": Channel.WHATSAPP, "outros": Channel.OTHER,
}
STATUS_ALIASES = {
    "pendente": SalesOrder.Status.PENDING, "aguardando pagamento": SalesOrder.Status.AWAITING_PAYMENT,
    "pago": SalesOrder.Status.PAID, "em separacao": SalesOrder.Status.SEPARATING,
    "em separação": SalesOrder.Status.SEPARATING, "enviado": SalesOrder.Status.SHIPPED,
    "entregue": SalesOrder.Status.DELIVERED,
}
FLOW_AFTER_PAID = [SalesOrder.Status.SEPARATING, SalesOrder.Status.SHIPPED, SalesOrder.Status.DELIVERED]


def _parse_date(raw: str):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _choice(raw: str, aliases: dict, values: list, default):
    key = (raw or "").strip().lower()
    if not key:
        return default
    if key in values:
        return key
    return aliases.get(key)


def import_orders(company, rows: list[dict], *, user=None) -> dict:
    from .services import change_status

    groups: OrderedDict[str, list[tuple[int, dict]]] = OrderedDict()
    for line, row in enumerate(rows, start=2):
        ref = (row.get("pedido") or "").strip()
        groups.setdefault(ref or f"__linha{line}", []).append((line, row))
    created, errors = 0, []
    for ref, lines in groups.items():
        first_line, first = lines[0]
        channel = _choice(first.get("canal"), CHANNEL_ALIASES, Channel.values, Channel.OTHER)
        status = _choice(first.get("status"), STATUS_ALIASES, SalesOrder.Status.values, SalesOrder.Status.PAID)
        order_date = _parse_date(first.get("data", ""))
        if channel is None or status is None or order_date is None:
            errors.append(f"Linha {first_line}: canal, status ou data inválidos.")
            continue
        if status in (SalesOrder.Status.CANCELLED, SalesOrder.Status.RETURNED):
            errors.append(f"Linha {first_line}: importe apenas pedidos ativos (não cancelados/devolvidos).")
            continue
        external = "" if ref.startswith("__linha") else ref[:60]
        if external and SalesOrder.objects.for_company(company).filter(channel=channel,
                                                                      external_reference=external).exists():
            errors.append(f"Linha {first_line}: pedido {external} já importado — ignorado.")
            continue
        items, bad = [], False
        for line, row in lines:
            product = Product.objects.for_company(company).filter(sku__iexact=(row.get("sku") or "").strip()).first()
            qty = to_decimal(row.get("quantidade"), None)
            price = to_decimal(row.get("preco_unitario") or row.get("preco"), None)
            if product is None or qty is None or qty <= 0 or price is None or price < 0:
                errors.append(f"Linha {line}: SKU inexistente ou quantidade/preço inválidos.")
                bad = True
                break
            items.append({"product": product, "quantity": qty, "unit_price": price,
                          "discount": to_decimal(row.get("desconto"))})
        if bad:
            continue
        payment = _choice(first.get("forma_pagamento"), {}, PaymentMethod.values, PaymentMethod.OTHER) \
            or PaymentMethod.OTHER
        initial = SalesOrder.Status.PAID if status in FLOW_AFTER_PAID else status
        order = SalesOrder(company=company, channel=channel, status=initial, order_date=order_date,
                           external_reference=external, payment_method=payment,
                           shipping_charged=to_decimal(first.get("frete_cobrado")),
                           shipping_cost=to_decimal(first.get("custo_frete")),
                           commission=to_decimal(first.get("comissao")), fees=to_decimal(first.get("taxas")),
                           taxes=to_decimal(first.get("impostos")))
        try:
            with transaction.atomic():  # cliente e pedido são gravados juntos (ou nenhum dos dois)
                if first.get("cliente"):
                    email = (first.get("email") or "").strip()[:254]
                    existing = (Customer.objects.for_company(company).filter(email__iexact=email).first()
                                if email else None)
                    order.customer = existing or Customer.objects.create(
                        company=company, name=first["cliente"][:150], email=email,
                        phone=(first.get("telefone") or "")[:20])
                save_order(order, items, user=user, auto_taxes=not first.get("impostos"))
                if status in FLOW_AFTER_PAID:
                    for step in FLOW_AFTER_PAID[:FLOW_AFTER_PAID.index(status) + 1]:
                        change_status(order, step, user=user)
        except SalesError as exc:
            errors.append(f"Linha {first_line}: {exc}")
            continue
        created += 1
    return {"created": created, "errors": errors}
