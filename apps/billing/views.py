from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.companies.models import Plan
from apps.companies.views import checkout_redirect
from apps.core import ratelimit
from apps.core.audit import client_ip
from apps.core.permissions import company_view

from . import gateway, services
from .models import Subscription, WebhookEvent

logger = logging.getLogger(__name__)


@company_view("settings", admin=True, billing_exempt=True)
@require_POST
def subscribe(request):
    plan = get_object_or_404(Plan, pk=request.POST.get("plan"), is_active=True)
    return checkout_redirect(request, request.company, plan)


@company_view("settings", admin=True, billing_exempt=True)
def checkout_return(request):
    """Retorno do checkout. O status é sempre conferido na API do Mercado Pago (nunca pelo navegador)."""
    preapproval_id = request.GET.get("preapproval_id", "")
    sub = Subscription.objects.filter(company=request.company, mp_preapproval_id=preapproval_id).first() \
        if preapproval_id else services.current_subscription(request.company)
    if sub is None or not sub.mp_preapproval_id:
        messages.info(request, "Nenhuma assinatura em andamento.")
        return redirect("companies:subscription")
    try:
        sub = services.sync_subscription(sub)
    except gateway.GatewayError as exc:
        messages.warning(request, f"Não foi possível confirmar agora: {exc} A confirmação também chega por "
                                  "notificação automática.")
        return redirect("companies:subscription")
    if sub.status == Subscription.Status.AUTHORIZED:
        messages.success(request, f"Assinatura do plano {sub.plan.name} confirmada. Obrigado!")
        return redirect("dashboard:home")
    if sub.status == Subscription.Status.PENDING:
        messages.info(request, "Pagamento em processamento. Assim que o Mercado Pago confirmar, o acesso é "
                               "liberado automaticamente — use “Verificar pagamento” para atualizar.")
    else:
        messages.warning(request, f"Situação da assinatura: {sub.get_status_display()}.")
    return redirect("companies:subscription")


@company_view("settings", admin=True, billing_exempt=True)
@require_POST
def refresh(request):
    sub = services.current_subscription(request.company)
    if sub and sub.mp_preapproval_id:
        try:
            sub = services.sync_subscription(sub)
            messages.success(request, f"Situação atualizada: {sub.get_status_display()}.")
        except gateway.GatewayError as exc:
            messages.error(request, str(exc))
    return redirect("companies:subscription")


@company_view("settings", admin=True, billing_exempt=True)
@require_POST
def cancel(request):
    try:
        sub = services.cancel_subscription(request.company, user=request.user, request=request)
    except gateway.GatewayError as exc:
        messages.error(request, str(exc))
        return redirect("companies:subscription")
    if sub is None:
        messages.info(request, "Não há assinatura para cancelar.")
    else:
        company = request.company
        company.refresh_from_db()
        until = f" O acesso continua até {company.access_until:%d/%m/%Y}." if company.access_until else ""
        messages.success(request, "Assinatura cancelada no Mercado Pago. Nenhuma nova cobrança será feita." + until)
    return redirect("companies:subscription")


@csrf_exempt
@require_POST
def webhook(request):
    """
    Notificações do Mercado Pago (Webhooks → eventos “Planos e assinaturas”).
    A assinatura `x-signature` é validada com MERCADOPAGO_WEBHOOK_SECRET e o recurso é sempre reconsultado na API.
    """
    ip = client_ip(request) or "?"
    if ratelimit.is_limited("mp-webhook", ip, 300):
        return HttpResponse(status=429)
    ratelimit.hit("mp-webhook", ip, 60)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        body = {}
    topic = str(body.get("type") or body.get("topic") or request.GET.get("type") or request.GET.get("topic") or "")
    data_id = str((body.get("data") or {}).get("id") or request.GET.get("data.id") or request.GET.get("id") or "")
    if not topic or not data_id:
        return JsonResponse({"ok": True, "ignorado": "sem tipo/id"})
    if not gateway.valid_signature(signature_header=request.headers.get("x-signature", ""),
                                   request_id=request.headers.get("x-request-id", ""),
                                   data_id=request.GET.get("data.id", data_id)):
        logger.warning("Webhook do Mercado Pago com assinatura inválida (ip=%s)", ip)
        return HttpResponse(status=401)
    event = WebhookEvent.objects.create(topic=topic[:60], resource_id=data_id[:64],
                                        request_id=request.headers.get("x-request-id", "")[:80], payload=body)
    try:
        summary = services.process_notification(topic, data_id)
    except gateway.GatewayError as exc:
        event.error = str(exc)[:300]
        event.save(update_fields=["error"])
        return HttpResponse(status=502)  # o Mercado Pago reenviará a notificação
    event.processed_at = timezone.now()
    event.error = summary[:300]
    event.save(update_fields=["processed_at", "error"])
    return JsonResponse({"ok": True})
