"""
Pesquisa de mercado: consultas ao Google Trends e simulações de oportunidade de produto.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class CachedLookup(models.Model):
    """
    Cache de dados PÚBLICOS obtidos de serviços externos (Google Trends, cotação do dólar).

    Não guarda nada da empresa: a chave é derivada só dos parâmetros públicos da consulta
    (palavras-chave, região, período). O histórico de cada empresa fica em `TrendSearch`.
    """

    key = models.CharField(max_length=64, unique=True)
    payload = models.JSONField(default=dict)
    fetched_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "consulta externa em cache"
        verbose_name_plural = "consultas externas em cache"


class TrendSearch(TenantModel):
    """Histórico de pesquisas de tendência feitas pela empresa."""

    keywords = models.CharField("palavras-chave", max_length=300)
    geo = models.CharField("região", max_length=10, default="BR")
    timeframe = models.CharField("período", max_length=20, default="today 12-m")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "pesquisa de tendência"
        verbose_name_plural = "pesquisas de tendência"

    def __str__(self) -> str:
        return self.keywords


class OpportunitySimulation(TenantModel):
    class Origin(models.TextChoices):
        NATIONAL = "national", "Nacional"
        INTERNATIONAL = "international", "Internacional (importado)"

    name = models.CharField("produto", max_length=160)
    keyword = models.CharField("palavra-chave pesquisada", max_length=120, blank=True)
    origin = models.CharField("origem", max_length=15, choices=Origin.choices, default=Origin.NATIONAL)
    channel = models.CharField("canal de venda", max_length=20, blank=True)
    sale_price = models.DecimalField("preço de venda", max_digits=14, decimal_places=2, null=True, blank=True)
    landed_cost = models.DecimalField("custo unitário posto no estoque", max_digits=14, decimal_places=2,
                                      default=Decimal("0"))
    margin_pct = models.DecimalField("margem líquida (%)", max_digits=8, decimal_places=2, null=True, blank=True)
    target_margin_pct = models.DecimalField("margem mínima exigida (%)", max_digits=6, decimal_places=2,
                                            default=Decimal("30"))
    minimum_price = models.DecimalField("preço mínimo para a margem exigida", max_digits=14, decimal_places=2,
                                        null=True, blank=True)
    is_viable = models.BooleanField("oportunidade viável", default=False)
    trend_score = models.PositiveSmallIntegerField("índice de tendência", null=True, blank=True)
    trend_direction = models.CharField("direção da demanda", max_length=10, blank=True)
    inputs = models.JSONField("dados informados", default=dict)
    results = models.JSONField("resultado calculado", default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "simulação de oportunidade"
        verbose_name_plural = "simulações de oportunidade"

    def __str__(self) -> str:
        return self.name
