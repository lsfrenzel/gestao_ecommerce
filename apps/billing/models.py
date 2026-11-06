"""Assinaturas das empresas cobradas pelo Mercado Pago (recurso “Assinaturas” / preapproval)."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Subscription(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Aguardando pagamento"
        AUTHORIZED = "authorized", "Ativa"
        PAUSED = "paused", "Pausada"
        CANCELLED = "cancelled", "Cancelada"
        FREE = "free", "Gratuita (cupom 100%)"

    ACTIVE_STATUSES = [Status.AUTHORIZED, Status.FREE]

    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="subscriptions",
                                verbose_name="empresa")
    plan = models.ForeignKey("companies.Plan", on_delete=models.PROTECT, related_name="subscriptions",
                             verbose_name="plano")
    status = models.CharField("situação", max_length=12, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    full_amount = models.DecimalField("preço do plano", max_digits=10, decimal_places=2)
    amount = models.DecimalField("valor cobrado por mês", max_digits=10, decimal_places=2)
    discount_percent = models.PositiveSmallIntegerField("desconto (%)", default=0)
    coupon_redemption = models.ForeignKey("companies.CouponRedemption", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="subscriptions")
    discount_until = models.DateField("desconto até", null=True, blank=True)
    first_charge_date = models.DateField("primeira cobrança", null=True, blank=True)
    mp_preapproval_id = models.CharField("ID da assinatura no Mercado Pago", max_length=64, unique=True, null=True,
                                         blank=True)
    init_point = models.URLField("link do checkout", max_length=500, blank=True)
    payer_email = models.EmailField("e-mail do pagador", blank=True)
    next_payment_date = models.DateTimeField("próxima cobrança", null=True, blank=True)
    last_synced_at = models.DateTimeField("última sincronização", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "assinatura"
        verbose_name_plural = "assinaturas"

    def __str__(self) -> str:
        return f"{self.company} — {self.plan} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def external_reference(self) -> str:
        return f"subscription:{self.pk}"

    @property
    def status_color(self) -> str:
        return {"authorized": "success", "free": "success", "pending": "warning", "paused": "secondary",
                "cancelled": "danger"}.get(self.status, "secondary")


class SubscriptionPayment(TimeStampedModel):
    """Cobrança mensal autorizada (authorized_payment) informada pelo Mercado Pago."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="payments")
    mp_id = models.CharField("ID no Mercado Pago", max_length=64, unique=True)
    amount = models.DecimalField("valor", max_digits=10, decimal_places=2, default=Decimal("0"))
    status = models.CharField("situação", max_length=40)
    status_detail = models.CharField("detalhe", max_length=80, blank=True)
    debit_date = models.DateTimeField("data da cobrança", null=True, blank=True)

    class Meta:
        ordering = ["-debit_date", "-created_at"]
        verbose_name = "cobrança da assinatura"
        verbose_name_plural = "cobranças da assinatura"

    @property
    def status_label(self) -> str:
        return {"processed": "Paga", "approved": "Paga", "scheduled": "Agendada", "recycling": "Nova tentativa",
                "rejected": "Recusada", "cancelled": "Cancelada", "pending": "Pendente"}.get(self.status, self.status)


class WebhookEvent(models.Model):
    """Notificações recebidas (idempotência e diagnóstico)."""

    received_at = models.DateTimeField(auto_now_add=True)
    topic = models.CharField(max_length=60)
    resource_id = models.CharField(max_length=64)
    request_id = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["topic", "resource_id"])]
