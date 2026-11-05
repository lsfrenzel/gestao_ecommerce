"""Simulações de preço salvas."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantModel

from .calculations import PricingInput

ZERO = Decimal("0")


def _money_field(label):
    return models.DecimalField(label, max_digits=14, decimal_places=4, default=ZERO)


def _pct_field(label):
    return models.DecimalField(label, max_digits=7, decimal_places=3, default=ZERO)


class PricingSimulation(TenantModel):
    name = models.CharField("nome da simulação", max_length=120)
    product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="pricing_simulations", verbose_name="produto")
    acquisition_cost = _money_field("custo de aquisição/produção")
    inbound_freight = _money_field("frete de entrada")
    packaging_cost = _money_field("embalagem")
    other_unit_costs = _money_field("outros custos unitários")
    tax_pct = _pct_field("impostos (%)")
    marketplace_pct = _pct_field("comissão do marketplace (%)")
    payment_fee_pct = _pct_field("taxa de pagamento (%)")
    sales_commission_pct = _pct_field("comissão de venda (%)")
    other_pct = _pct_field("outras despesas (%)")
    subsidized_shipping = _money_field("frete subsidiado")
    handling_cost = _money_field("separação e embalagem")
    other_per_sale = _money_field("outros custos por venda")
    target_margin_pct = _pct_field("margem de contribuição desejada (%)")
    minimum_price = models.DecimalField("preço mínimo", max_digits=14, decimal_places=2, default=ZERO)
    suggested_price = models.DecimalField("preço sugerido", max_digits=14, decimal_places=2, default=ZERO)
    applied_at = models.DateTimeField("aplicado ao produto em", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    INPUT_FIELDS = ["acquisition_cost", "inbound_freight", "packaging_cost", "other_unit_costs", "tax_pct",
                    "marketplace_pct", "payment_fee_pct", "sales_commission_pct", "other_pct",
                    "subsidized_shipping", "handling_cost", "other_per_sale", "target_margin_pct"]

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "simulação de preço"
        verbose_name_plural = "simulações de preço"

    def __str__(self) -> str:
        return self.name

    def to_input(self) -> PricingInput:
        return PricingInput(**{f: getattr(self, f) for f in self.INPUT_FIELDS})
