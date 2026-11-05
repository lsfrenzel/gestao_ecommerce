"""Modelos base compartilhados e auditoria."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        abstract = True


class TenantQuerySet(models.QuerySet):
    def for_company(self, company):
        """Único ponto de entrada recomendado para consultar dados de uma empresa."""
        if company is None:
            return self.none()
        return self.filter(company=company)


class TenantModel(TimeStampedModel):
    """
    Base de todo dado pertencente a uma empresa (tenant).

    O isolamento é garantido no backend: as views usam `Model.objects.for_company(request.company)`
    (ver `apps.core.views.CompanyScopedMixin`), e os formulários restringem as opções de chaves
    estrangeiras à mesma empresa (`apps.core.forms.CompanyScopedFormMixin`).
    """

    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="+",
                                verbose_name="empresa", db_index=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Registro imutável de ações relevantes (financeiras, de estoque, de permissões e administrativas)."""

    created_at = models.DateTimeField("data", auto_now_add=True, db_index=True)
    company = models.ForeignKey("companies.Company", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="audit_logs", verbose_name="empresa")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="audit_logs", verbose_name="usuário")
    action = models.CharField("ação", max_length=80, db_index=True)
    target_type = models.CharField("tipo do alvo", max_length=60, blank=True)
    target_id = models.CharField("id do alvo", max_length=40, blank=True)
    target_repr = models.CharField("alvo", max_length=200, blank=True)
    details = models.JSONField("detalhes", default=dict, blank=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)
    platform_scope = models.BooleanField("ação da plataforma", default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "registro de auditoria"
        verbose_name_plural = "auditoria"

    def __str__(self) -> str:
        return f"{self.created_at:%d/%m/%Y %H:%M} {self.action} {self.target_repr}"
