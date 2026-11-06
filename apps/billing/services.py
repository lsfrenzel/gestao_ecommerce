"""
Regras de assinatura.

- Teste grátis: 14 dias (FREE_TRIAL_DAYS) sem cartão; ao fim, o acesso fica bloqueado até assinar.
- Plano pago: cria uma assinatura no Mercado Pago e envia a pessoa ao checkout (init_point).
  O acesso só é liberado quando a API confirma `authorized` (webhook ou consulta).
- Quem assina durante o teste não perde os dias restantes: a 1ª cobrança é agendada para o fim do teste.
- Cupom 50%: valor mensal pela metade (pelo prazo do cupom; depois o valor volta ao cheio — ver sync).
- Cupom 100%: permanente → assinatura gratuita, sem Mercado Pago; com prazo → 1ª cobrança após o prazo.
- Cancelamento: o acesso continua até a data da próxima cobrança já paga.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.companies.models import Company, Plan
from apps.core.audit import log_action

from . import gateway
from .models import Subscription, SubscriptionPayment

logger = logging.getLogger(__name__)
S = Subscription.Status


class BillingError(Exception):
    pass


def _parse_dt(value):
    if not value:
        return None
    dt = parse_datetime(value) if isinstance(value, str) else value
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def current_subscription(company: Company) -> Subscription | None:
    subs = Subscription.objects.filter(company=company).select_related("plan")
    return (subs.filter(status__in=Subscription.ACTIVE_STATUSES).first()
            or subs.filter(status__in=[S.PENDING, S.PAUSED]).first())


def quote(company: Company, plan: Plan) -> dict:
    """Calcula valor, desconto e data da 1ª cobrança para assinar `plan` agora."""
    today = timezone.localdate()
    full = plan.monthly_price
    redemption = company.active_redemption
    pct = redemption.coupon.discount_percent if redemption else 0
    until = redemption.expires_at if redemption else None
    if redemption and redemption.coupon.plans.exists() and not redemption.coupon.plans.filter(pk=plan.pk).exists():
        pct, until, redemption = 0, None, None  # cupom não vale para este plano
    first_charge = None
    if company.status == Company.Status.TRIAL and company.trial_ends_at and company.trial_ends_at >= today:
        first_charge = company.trial_ends_at + timedelta(days=1)
    amount = full
    free = False
    if pct == 100:
        if until is None:
            free, amount = True, Decimal("0.00")
        else:
            after_coupon = until + timedelta(days=1)
            first_charge = max(first_charge or after_coupon, after_coupon)
    elif pct == 50:
        amount = (full / 2).quantize(Decimal("0.01"))
    return {"plan": plan, "full_amount": full, "amount": amount, "discount_percent": pct, "discount_until": until,
            "first_charge": first_charge, "free": free or full == 0, "redemption": redemption}


@transaction.atomic
def start_checkout(company: Company, plan: Plan, *, user, request=None) -> Subscription:
    if not plan.is_active:
        raise BillingError("Este plano não está disponível.")
    current = current_subscription(company)
    if current and current.is_active and current.plan_id == plan.pk:
        raise BillingError("A empresa já assina este plano.")
    q = quote(company, plan)
    sub = Subscription.objects.create(
        company=company, plan=plan, full_amount=q["full_amount"], amount=q["amount"],
        discount_percent=q["discount_percent"], coupon_redemption=q["redemption"], discount_until=q["discount_until"],
        first_charge_date=q["first_charge"], payer_email=user.email, created_by=user)
    if q["free"]:
        sub.status = S.FREE
        sub.save(update_fields=["status", "updated_at"])
        activate(sub)
        log_action("billing.subscribe_free", request=request, actor=user, company=company, target=sub,
                   details={"plano": plan.name})
        return sub
    start = None
    if q["first_charge"]:
        start = timezone.make_aware(datetime.combine(q["first_charge"], time(12)))
    try:
        data = gateway.create_preapproval(
            reason=f"{settings.PLATFORM_NAME} — plano {plan.name}", amount=q["amount"], payer_email=user.email,
            back_url=settings.SITE_URL + reverse("billing:return"), external_reference=sub.external_reference,
            start_date=start)
    except gateway.GatewayError:
        transaction.set_rollback(True)
        raise
    sub.mp_preapproval_id = str(data.get("id") or "") or None
    sub.init_point = data.get("init_point") or data.get("sandbox_init_point") or ""
    sub.save(update_fields=["mp_preapproval_id", "init_point", "updated_at"])
    if not sub.init_point:
        raise BillingError("O Mercado Pago não retornou o link de pagamento. Tente novamente.")
    log_action("billing.checkout", request=request, actor=user, company=company, target=sub,
               details={"plano": plan.name, "valor": str(q["amount"]), "desconto": q["discount_percent"],
                        "primeira_cobranca": str(q["first_charge"] or "imediata")})
    return sub


def activate(sub: Subscription) -> None:
    """Assinatura confirmada: libera a empresa no plano e encerra assinaturas anteriores."""
    company = sub.company
    for old in Subscription.objects.filter(company=company).exclude(pk=sub.pk).exclude(status=S.CANCELLED):
        if old.mp_preapproval_id and old.status in (S.AUTHORIZED, S.PAUSED, S.PENDING):
            try:
                gateway.cancel_preapproval(old.mp_preapproval_id)
            except gateway.GatewayError:
                logger.warning("Não foi possível cancelar a assinatura anterior %s no Mercado Pago", old.pk)
        old.status = S.CANCELLED
        old.save(update_fields=["status", "updated_at"])
    company.plan = sub.plan
    if company.status != Company.Status.SUSPENDED:
        company.status = Company.Status.ACTIVE
    company.access_until = None
    company.save(update_fields=["plan", "status", "access_until", "updated_at"])


def _deactivate(sub: Subscription) -> None:
    company = sub.company
    if Subscription.objects.filter(company=company, status__in=Subscription.ACTIVE_STATUSES).exclude(pk=sub.pk).exists():
        return
    if company.status == Company.Status.ACTIVE:
        company.status = Company.Status.PENDING
        if sub.next_payment_date and sub.next_payment_date > timezone.now():
            company.access_until = timezone.localtime(sub.next_payment_date).date()
        company.save(update_fields=["status", "access_until", "updated_at"])


@transaction.atomic
def sync_subscription(sub: Subscription, data: dict | None = None) -> Subscription:
    """Atualiza a assinatura a partir da API do Mercado Pago (fonte da verdade)."""
    if not sub.mp_preapproval_id:
        return sub
    data = data or gateway.get_preapproval(sub.mp_preapproval_id)
    sub = Subscription.objects.select_for_update().select_related("company", "plan").get(pk=sub.pk)
    old = sub.status
    new = {"authorized": S.AUTHORIZED, "paused": S.PAUSED, "cancelled": S.CANCELLED}.get(data.get("status"), S.PENDING)
    sub.status = new
    sub.next_payment_date = _parse_dt(data.get("next_payment_date")) or sub.next_payment_date
    sub.last_synced_at = timezone.now()
    sub.save(update_fields=["status", "next_payment_date", "last_synced_at", "updated_at"])
    if new == S.AUTHORIZED and old != S.AUTHORIZED:
        activate(sub)
        log_action("billing.authorized", company=sub.company, target=sub, details={"plano": sub.plan.name})
    elif new in (S.PAUSED, S.CANCELLED) and old != new:
        _deactivate(sub)
        log_action(f"billing.{new}", company=sub.company, target=sub)
    return sub


def _subscription_for(data: dict) -> Subscription | None:
    sub = Subscription.objects.filter(mp_preapproval_id=str(data.get("id") or data.get("preapproval_id"))).first()
    if sub is None:
        ref = str(data.get("external_reference") or "")
        if ref.startswith("subscription:") and ref.split(":", 1)[1].isdigit():
            sub = Subscription.objects.filter(pk=int(ref.split(":", 1)[1])).first()
            if sub and not sub.mp_preapproval_id and data.get("id"):
                sub.mp_preapproval_id = str(data["id"])
                sub.save(update_fields=["mp_preapproval_id"])
    return sub


def process_notification(topic: str, resource_id: str) -> str:
    """Processa uma notificação do Mercado Pago. Retorna um resumo para o registro do evento."""
    if topic in ("subscription_preapproval", "preapproval"):
        data = gateway.get_preapproval(resource_id)
        sub = _subscription_for(data)
        if sub is None:
            return "assinatura desconhecida"
        sync_subscription(sub, data)
        return f"assinatura {sub.pk}: {data.get('status')}"
    if topic in ("subscription_authorized_payment", "authorized_payment"):
        data = gateway.get_authorized_payment(resource_id)
        sub = Subscription.objects.filter(mp_preapproval_id=str(data.get("preapproval_id"))).first()
        if sub is None:
            return "cobrança de assinatura desconhecida"
        payment = data.get("payment") or {}
        SubscriptionPayment.objects.update_or_create(
            mp_id=str(data.get("id") or resource_id),
            defaults={"subscription": sub, "amount": Decimal(str(data.get("transaction_amount") or 0)),
                      "status": str(payment.get("status") or data.get("status") or ""),
                      "status_detail": str(payment.get("status_detail") or "")[:80],
                      "debit_date": _parse_dt(data.get("debit_date") or data.get("date_created"))})
        sync_subscription(sub)
        return f"cobrança da assinatura {sub.pk}: {payment.get('status') or data.get('status')}"
    return "tópico ignorado"


@transaction.atomic
def cancel_subscription(company: Company, *, user, request=None) -> Subscription | None:
    sub = current_subscription(company)
    if sub is None:
        return None
    if sub.mp_preapproval_id and sub.status in (S.AUTHORIZED, S.PAUSED, S.PENDING):
        data = gateway.cancel_preapproval(sub.mp_preapproval_id)
        sub.next_payment_date = _parse_dt(data.get("next_payment_date")) or sub.next_payment_date
    was_active = sub.is_active
    sub.status = S.CANCELLED
    sub.save(update_fields=["status", "next_payment_date", "updated_at"])
    if was_active:
        _deactivate(sub)
    log_action("billing.cancel", request=request, actor=user, company=company, target=sub)
    return sub


def apply_coupon_expirations() -> int:
    """Cupons de 50% com prazo vencido: o valor da assinatura volta ao preço cheio no Mercado Pago."""
    today = timezone.localdate()
    updated = 0
    for sub in Subscription.objects.filter(status=S.AUTHORIZED, discount_percent__gt=0, discount_until__lt=today,
                                           mp_preapproval_id__isnull=False):
        if sub.amount == sub.full_amount:
            continue
        gateway.update_amount(sub.mp_preapproval_id, sub.full_amount)
        sub.amount, sub.discount_percent = sub.full_amount, 0
        sub.save(update_fields=["amount", "discount_percent", "updated_at"])
        log_action("billing.coupon_expired", company=sub.company, target=sub,
                   details={"novo_valor": str(sub.full_amount)})
        updated += 1
    return updated
