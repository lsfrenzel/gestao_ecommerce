"""
Cliente mínimo da API do Mercado Pago para Assinaturas (preapproval).

Documentação: https://www.mercadopago.com.br/developers/pt/reference/subscriptions/_preapproval/post
- POST /preapproval              cria a assinatura e devolve `init_point` (checkout do Mercado Pago)
- GET  /preapproval/{id}         consulta (status: pending, authorized, paused, cancelled)
- PUT  /preapproval/{id}         altera status/valor
- GET  /authorized_payments/{id} cobrança mensal da assinatura

O status de uma assinatura NUNCA é decidido pelo retorno do navegador: sempre consultamos a API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal
from urllib import error, request

from django.conf import settings

logger = logging.getLogger(__name__)
API_BASE = "https://api.mercadopago.com"


class GatewayError(Exception):
    """Falha de comunicação ou recusa da API do Mercado Pago (mensagem amigável)."""


def is_configured() -> bool:
    return bool(settings.MERCADOPAGO_ACCESS_TOKEN)


def is_test_mode() -> bool:
    return settings.MERCADOPAGO_ACCESS_TOKEN.startswith("TEST-")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    if not is_configured():
        raise GatewayError("Pagamentos indisponíveis: MERCADOPAGO_ACCESS_TOKEN não configurado.")
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(API_BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if method == "POST":
        req.add_header("X-Idempotency-Key", str(uuid.uuid4()))
    try:
        with request.urlopen(req, timeout=20) as response:  # noqa: S310 — URL fixa da API do Mercado Pago
            body = response.read().decode() or "{}"
            return json.loads(body)
    except error.HTTPError as exc:
        detail = exc.read().decode(errors="ignore")[:500]
        logger.warning("Mercado Pago %s %s -> %s %s", method, path, exc.code, detail)
        try:
            message = json.loads(detail).get("message") or detail
        except ValueError:
            message = detail
        raise GatewayError(f"O Mercado Pago recusou a operação ({exc.code}): {message}") from exc
    except (error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("Mercado Pago indisponível: %s", exc)
        raise GatewayError("Não foi possível falar com o Mercado Pago agora. Tente novamente em instantes.") from exc


def _money(value: Decimal) -> float:
    return float(Decimal(value).quantize(Decimal("0.01")))


def create_preapproval(*, reason: str, amount: Decimal, payer_email: str, back_url: str, external_reference: str,
                       start_date=None) -> dict:
    auto_recurring = {"frequency": 1, "frequency_type": "months", "transaction_amount": _money(amount),
                      "currency_id": "BRL"}
    if start_date is not None:
        auto_recurring["start_date"] = start_date.isoformat(timespec="milliseconds")
    return _request("POST", "/preapproval", {
        "reason": reason[:255], "external_reference": external_reference, "payer_email": payer_email,
        "back_url": back_url, "auto_recurring": auto_recurring, "status": "pending",
    })


def get_preapproval(preapproval_id: str) -> dict:
    return _request("GET", f"/preapproval/{preapproval_id}")


def update_preapproval(preapproval_id: str, payload: dict) -> dict:
    return _request("PUT", f"/preapproval/{preapproval_id}", payload)


def cancel_preapproval(preapproval_id: str) -> dict:
    return update_preapproval(preapproval_id, {"status": "cancelled"})


def update_amount(preapproval_id: str, amount: Decimal) -> dict:
    return update_preapproval(preapproval_id, {"auto_recurring": {"transaction_amount": _money(amount),
                                                                  "currency_id": "BRL"}})


def get_authorized_payment(payment_id: str) -> dict:
    return _request("GET", f"/authorized_payments/{payment_id}")


def valid_signature(*, signature_header: str, request_id: str, data_id: str) -> bool:
    """
    Valida o cabeçalho `x-signature` (ts=...,v1=...) com MERCADOPAGO_WEBHOOK_SECRET.
    Manifesto: "id:{data.id};request-id:{x-request-id};ts:{ts};" — HMAC-SHA256.
    Sem segredo configurado, a validação é ignorada (o recurso é sempre reconsultado na API).
    """
    secret = settings.MERCADOPAGO_WEBHOOK_SECRET
    if not secret:
        return True
    parts = dict(p.strip().split("=", 1) for p in (signature_header or "").split(",") if "=" in p)
    ts, v1 = parts.get("ts"), parts.get("v1")
    if not ts or not v1:
        return False
    manifest = ""
    if data_id:
        manifest += f"id:{data_id.lower() if data_id.isalnum() else data_id};"
    if request_id:
        manifest += f"request-id:{request_id};"
    manifest += f"ts:{ts};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
