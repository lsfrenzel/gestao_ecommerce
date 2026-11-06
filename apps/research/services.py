"""Montagem automática das premissas da simulação de oportunidade a partir dos dados da empresa."""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.money import HUNDRED, ZERO

from . import fx
from .opportunity import CHANNELS, DEFAULTS, FORMAL, INTERNATIONAL, SIMPLIFIED, OpportunityInput

ASSUMPTION_LABELS = {
    "tax_pct": ("Impostos sobre a venda", "%"),
    "commission_pct": ("Comissão do canal", "%"),
    "fixed_fee": ("Tarifa fixa por venda", "R$"),
    "payment_fee_pct": ("Taxa de pagamento", "%"),
    "marketing_pct": ("Marketing / aquisição", "%"),
    "returns_pct": ("Devoluções e perdas", "%"),
    "packaging": ("Embalagem por unidade", "R$"),
    "shipping": ("Frete subsidiado por venda", "R$"),
    "inbound_freight_pct": ("Frete de entrada", "% do custo"),
    "exchange_rate": ("Cotação do dólar", "R$"),
    "intl_freight_usd": ("Frete internacional por unidade", "US$"),
    "insurance_pct": ("Seguro internacional", "%"),
    "fx_fee_pct": ("Câmbio (IOF + spread)", "%"),
    "ii_pct": ("Imposto de Importação", "%"),
    "ipi_pct": ("IPI", "%"),
    "pis_pct": ("PIS-Importação", "%"),
    "cofins_pct": ("COFINS-Importação", "%"),
    "icms_pct": ("ICMS de importação", "%"),
    "customs_fees": ("Despesas aduaneiras por embarque", "R$"),
    "domestic_freight_pct": ("Frete interno até o estoque", "%"),
}
SELLING = ["tax_pct", "commission_pct", "fixed_fee", "payment_fee_pct", "marketing_pct", "returns_pct",
           "packaging", "shipping"]
NATIONAL_ONLY = ["inbound_freight_pct"]
IMPORT_SIMPLIFIED = ["exchange_rate", "intl_freight_usd", "fx_fee_pct", "icms_pct", "customs_fees"]
IMPORT_FORMAL = ["exchange_rate", "intl_freight_usd", "insurance_pct", "fx_fee_pct", "ii_pct", "ipi_pct",
                 "pis_pct", "cofins_pct", "icms_pct", "customs_fees", "domestic_freight_pct"]


def marketing_share(company) -> tuple[Decimal, str]:
    """Investimento ÷ receita atribuída das campanhas dos últimos 180 dias (o inverso do ROAS)."""
    from apps.marketing.models import Campaign

    since = timezone.localdate() - timedelta(days=180)
    totals = Campaign.objects.for_company(company).filter(end_date__gte=since).aggregate(
        inv=Sum("investment"), rev=Sum("attributed_revenue"))
    inv, rev = totals["inv"] or ZERO, totals["rev"] or ZERO
    if inv > 0 and rev > 0:
        share = min(Decimal("60"), (inv / rev * HUNDRED).quantize(Decimal("0.1"), ROUND_HALF_UP))
        return share, "histórico das suas campanhas (180 dias)"
    return DEFAULTS["marketing_pct"], "estimativa padrão (sem campanhas com receita)"


def auto_assumptions(company, origin: str, regime: str, channel_key: str, unit_cost=None) -> dict:
    """{campo: (valor, origem da estimativa)} para tudo que o usuário não informar."""
    channel = CHANNELS.get(channel_key) or CHANNELS["mercado_livre"]
    values: dict[str, tuple[Decimal | None, str]] = {}
    if company.default_tax_rate and company.default_tax_rate > 0:
        values["tax_pct"] = (company.default_tax_rate, "alíquota configurada na empresa")
    else:
        values["tax_pct"] = (DEFAULTS["tax_pct"], "estimativa Simples Nacional (comércio)")
    src = f"referência do canal {channel.label}"
    values["commission_pct"] = (channel.commission_pct, src)
    values["fixed_fee"] = (channel.fixed_fee, src)
    values["payment_fee_pct"] = (channel.payment_fee_pct, src)
    values["shipping"] = (channel.shipping, src)
    values["marketing_pct"] = marketing_share(company)
    values["returns_pct"] = (DEFAULTS["returns_pct"], "estimativa padrão")
    values["packaging"] = (DEFAULTS["packaging"], "estimativa padrão")
    if origin == INTERNATIONAL:
        regime_defaults = DEFAULTS[FORMAL if regime == FORMAL else SIMPLIFIED]
        regime_src = "padrão da importação formal" if regime == FORMAL else "regra do Remessa Conforme"
        for name in IMPORT_FORMAL:
            if name in regime_defaults:
                values[name] = (regime_defaults[name], regime_src)
        quote = fx.usd_brl()
        values["exchange_rate"] = (quote.rate, f"{quote.source}, cotação automática") if quote else (
            None, "indisponível — informe manualmente")
        if unit_cost:
            freight = (Decimal(unit_cost) * DEFAULTS["intl_freight_pct_of_fob"] / HUNDRED).quantize(
                Decimal("0.01"), ROUND_HALF_UP)
            values["intl_freight_usd"] = (freight, "estimativa: 15% do valor FOB")
        else:
            values["intl_freight_usd"] = (ZERO, "estimativa: 15% do valor FOB")
    else:
        values["inbound_freight_pct"] = (DEFAULTS["inbound_freight_pct"], "estimativa padrão")
    return values


def relevant_fields(origin: str, regime: str) -> list[str]:
    if origin == INTERNATIONAL:
        return SELLING + (IMPORT_FORMAL if regime == FORMAL else IMPORT_SIMPLIFIED)
    return SELLING + NATIONAL_ONLY


def build_input(cleaned: dict, company) -> tuple[OpportunityInput, list[dict]]:
    """Combina o que o usuário informou com as estimativas automáticas. Retorna (entrada, premissas usadas)."""
    origin = cleaned["origin"]
    regime = cleaned.get("regime") or SIMPLIFIED
    channel_key = cleaned.get("channel") or "mercado_livre"
    channel = CHANNELS.get(channel_key) or CHANNELS["mercado_livre"]
    unit_cost = cleaned["unit_cost_usd"] if origin == INTERNATIONAL else cleaned["unit_cost_brl"]
    auto = auto_assumptions(company, origin, regime, channel_key, unit_cost)
    values, used = {}, []
    for name in relevant_fields(origin, regime):
        informed = cleaned.get(name)
        if informed is not None:
            value, source = informed, "informado por você"
        else:
            value, source = auto.get(name, (ZERO, "estimativa padrão"))
        values[name] = value
        label, unit = ASSUMPTION_LABELS[name]
        used.append({"field": name, "label": label, "unit": unit, "value": value, "source": source,
                     "auto": informed is None})
    data = OpportunityInput(
        origin=origin, unit_cost=unit_cost, sale_price=cleaned.get("sale_price"),
        quantity=cleaned.get("quantity") or 1, target_margin_pct=cleaned["target_margin_pct"], regime=regime,
        fixed_fee_below=channel.fixed_fee_below, shipping_from=channel.shipping_from,
        **{k: (ZERO if v is None else v) for k, v in values.items()},
    )
    return data, used
