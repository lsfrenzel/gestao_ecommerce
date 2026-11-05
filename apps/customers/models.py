"""Clientes (dados pessoais mínimos — LGPD)."""

from __future__ import annotations

from django.db import models
from django.db.models import Max, Sum

from apps.core.models import TenantModel


class Customer(TenantModel):
    name = models.CharField("nome", max_length=150)
    email = models.EmailField("e-mail", blank=True)
    phone = models.CharField("telefone", max_length=20, blank=True)
    document = models.CharField("CPF ou CNPJ", max_length=18, blank=True)
    address = models.CharField("endereço", max_length=255, blank=True)
    city = models.CharField("cidade", max_length=80, blank=True)
    state = models.CharField("UF", max_length=2, blank=True)
    notes = models.TextField("observações", blank=True)
    marketing_consent = models.BooleanField("autoriza comunicações de marketing", default=False)
    anonymized_at = models.DateTimeField("anonimizado em", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["company", "name"]), models.Index(fields=["company", "email"])]
        verbose_name = "cliente"
        verbose_name_plural = "clientes"

    def __str__(self) -> str:
        return self.name

    @property
    def masked_document(self) -> str:
        """Exibe apenas os 2 últimos dígitos (minimização de dados)."""
        digits = "".join(ch for ch in self.document if ch.isdigit())
        if not digits:
            return ""
        return "•" * (len(digits) - 2) + digits[-2:]

    def purchase_summary(self) -> dict:
        from apps.sales.models import SalesOrder

        agg = (SalesOrder.objects.for_company(self.company)
               .filter(customer=self, status__in=SalesOrder.REVENUE_STATUSES)
               .aggregate(total=Sum("total"), last=Max("order_date")))
        count = SalesOrder.objects.for_company(self.company).filter(customer=self,
                                                                  status__in=SalesOrder.REVENUE_STATUSES).count()
        return {"total": agg["total"] or 0, "last": agg["last"], "count": count}
