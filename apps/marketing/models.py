"""
Campanhas de marketing e indicadores.

- Taxa de conversão = Pedidos / Visitantes × 100
- CPC = Investimento / Cliques
- CAC = Investimento / Novos clientes adquiridos
- ROAS = Receita atribuída / Investimento   (ROAS NÃO é lucro nem ROI: não desconta custos)
- Ticket médio = Receita atribuída / Pedidos
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantModel
from apps.core.money import HUNDRED, safe_div

ZERO = Decimal("0")


def conversion_rate(orders, visitors):
    r = safe_div(orders, visitors)
    return None if r is None else r * HUNDRED


def cpc(investment, clicks):
    return safe_div(investment, clicks)


def cac(investment, new_customers):
    return safe_div(investment, new_customers)


def roas(revenue, investment):
    return safe_div(revenue, investment)


def average_ticket(revenue, orders):
    return safe_div(revenue, orders)


class Campaign(TenantModel):
    CHANNELS = [
        ("meta_ads", "Meta Ads (Facebook/Instagram)"),
        ("google_ads", "Google Ads"),
        ("tiktok_ads", "TikTok Ads"),
        ("marketplace_ads", "Anúncios em marketplace"),
        ("influencer", "Influenciadores"),
        ("email", "E-mail marketing"),
        ("whatsapp", "WhatsApp"),
        ("organic", "Orgânico/redes sociais"),
        ("other", "Outros"),
    ]

    name = models.CharField("nome da campanha", max_length=120)
    channel = models.CharField("canal utilizado", max_length=20, choices=CHANNELS)
    investment = models.DecimalField("investimento em divulgação (R$)", max_digits=14, decimal_places=2,
                                     default=ZERO, validators=[MinValueValidator(ZERO)])
    start_date = models.DateField("início")
    end_date = models.DateField("fim")
    clicks = models.PositiveIntegerField("cliques", default=0)
    visitors = models.PositiveIntegerField("visitantes", default=0)
    orders = models.PositiveIntegerField("pedidos", default=0)
    new_customers = models.PositiveIntegerField("novos clientes adquiridos", default=0)
    attributed_revenue = models.DecimalField("faturamento atribuído (R$)", max_digits=14, decimal_places=2,
                                             default=ZERO, validators=[MinValueValidator(ZERO)])
    notes = models.TextField("observações", blank=True)

    class Meta:
        ordering = ["-start_date", "name"]
        verbose_name = "campanha"
        verbose_name_plural = "campanhas"

    def __str__(self) -> str:
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A data final deve ser igual ou posterior à inicial."})

    @property
    def conversion_rate(self):
        return conversion_rate(self.orders, self.visitors)

    @property
    def cpc(self):
        return cpc(self.investment, self.clicks)

    @property
    def cac(self):
        return cac(self.investment, self.new_customers)

    @property
    def roas(self):
        return roas(self.attributed_revenue, self.investment)

    @property
    def average_ticket(self):
        return average_ticket(self.attributed_revenue, self.orders)
