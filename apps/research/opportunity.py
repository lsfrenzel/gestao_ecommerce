"""
Simulação de oportunidade de produto — funções puras em Decimal.

Pergunta respondida: "comprando este produto por X e vendendo por Y, sobra pelo menos 30% de margem
líquida depois de TODOS os custos estimados?"

1. Custo posto no estoque (por unidade)
   - Nacional:      custo do fornecedor × (1 + frete de entrada %)
   - Internacional: valor aduaneiro (FOB + frete internacional + seguro) convertido pelo dólar
                    + custo de câmbio (IOF + spread) + tributos de importação + despesas aduaneiras
                    + frete interno até o estoque, rateados pela quantidade do lote.
     · Remessa Conforme / RTS (até US$ 3.000 por remessa): II de 20% até US$ 50; acima, 60% com dedução
       de US$ 20. ICMS "por dentro": (valor aduaneiro + II) × alíquota / (1 − alíquota).
     · Importação formal: II, IPI (sobre valor aduaneiro + II), PIS e COFINS-Importação (sobre o valor
       aduaneiro) e ICMS "por dentro" sobre a soma de tudo, mais despesas aduaneiras do embarque.
       Créditos tributários não são considerados (visão conservadora, comum a empresas do Simples).

2. Custos de venda (por unidade, no preço P)
   - Percentuais sobre P: impostos sobre a venda + comissão do canal + taxa de pagamento + marketing
     (custo de aquisição) + devoluções/perdas.
   - Valores fixos: tarifa fixa do canal (abaixo de um preço-limite), embalagem e frete subsidiado
     (a partir de um preço-limite, como o frete grátis obrigatório de marketplaces).

3. Margem líquida (%) = (P − custo posto − custos de venda) / P × 100.
   Viável quando a margem líquida ≥ margem mínima exigida (padrão: 30%).

4. Preço mínimo = menor P com margem ≥ exigida = (custo posto + fixos) / (1 − Σ% − margem exigida),
   resolvido por faixa de preço (as tarifas fixas mudam conforme o preço).
   Custo máximo de compra = maior custo do fornecedor que ainda entrega a margem exigida no preço P.

Os percentuais e valores padrão são ESTIMATIVAS de referência; todos podem ser ajustados na simulação.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from apps.core.money import HUNDRED, ONE, ZERO, money

TWO = Decimal("0.01")
NATIONAL, INTERNATIONAL = "national", "international"
SIMPLIFIED, FORMAL = "simplified", "formal"
SIMPLIFIED_LIMIT_USD = Decimal("3000")
SIMPLIFIED_LOW_BAND_USD = Decimal("50")


class OpportunityError(ValueError):
    """Dados que não permitem calcular a simulação (mensagem amigável)."""


@dataclass(frozen=True)
class Channel:
    label: str
    commission_pct: Decimal
    payment_fee_pct: Decimal
    fixed_fee: Decimal = ZERO
    fixed_fee_below: Decimal | None = None  # tarifa fixa só abaixo deste preço (None = sempre)
    shipping: Decimal = ZERO
    shipping_from: Decimal | None = None  # frete subsidiado só a partir deste preço (None = sempre)


# Referências de mercado para estimativa automática — confira as tabelas vigentes de cada canal.
CHANNELS: dict[str, Channel] = {
    "mercado_livre": Channel("Mercado Livre (anúncio clássico)", Decimal("14"), ZERO, Decimal("6.50"), Decimal("79"),
                             Decimal("22"), Decimal("79")),
    "shopee": Channel("Shopee", Decimal("20"), ZERO, Decimal("4"), None),
    "amazon": Channel("Amazon", Decimal("15"), ZERO, ZERO, None, Decimal("20"), Decimal("79")),
    "tiktok_shop": Channel("TikTok Shop", Decimal("12"), ZERO, Decimal("4"), None),
    "own_store": Channel("Loja própria (site)", ZERO, Decimal("4.99"), ZERO, None),
}
CHANNEL_CHOICES = [(key, ch.label) for key, ch in CHANNELS.items()]

REGIMES = [
    (SIMPLIFIED, "Remessa Conforme / RTS (até US$ 3.000 por remessa)"),
    (FORMAL, "Importação formal (despacho aduaneiro)"),
]

# Premissas estimadas automaticamente quando o campo é deixado em branco.
DEFAULTS = {
    "tax_pct": Decimal("6"),  # substituído pela alíquota configurada na empresa, quando houver
    "marketing_pct": Decimal("10"),  # substituído pelo histórico de campanhas (investimento/receita)
    "returns_pct": Decimal("2"),
    "packaging": Decimal("2.50"),
    "inbound_freight_pct": Decimal("5"),
    "intl_freight_pct_of_fob": Decimal("15"),
    SIMPLIFIED: {"insurance_pct": ZERO, "fx_fee_pct": Decimal("4"), "ii_pct": ZERO, "ipi_pct": ZERO,
                 "pis_pct": ZERO, "cofins_pct": ZERO, "icms_pct": Decimal("20"), "customs_fees": Decimal("15"),
                 "domestic_freight_pct": ZERO},
    FORMAL: {"insurance_pct": Decimal("1"), "fx_fee_pct": Decimal("1.5"), "ii_pct": Decimal("20"),
             "ipi_pct": Decimal("10"), "pis_pct": Decimal("2.10"), "cofins_pct": Decimal("9.65"),
             "icms_pct": Decimal("18"), "customs_fees": Decimal("1800"), "domestic_freight_pct": Decimal("3")},
}


def _d(value, default=ZERO) -> Decimal:
    if value is None or value == "":
        return default
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass
class OpportunityInput:
    origin: str
    unit_cost: Decimal  # R$ (nacional) ou US$ FOB (internacional)
    sale_price: Decimal | None = None
    quantity: int = 100
    target_margin_pct: Decimal = Decimal("30")
    # venda
    tax_pct: Decimal = ZERO
    commission_pct: Decimal = ZERO
    payment_fee_pct: Decimal = ZERO
    marketing_pct: Decimal = ZERO
    returns_pct: Decimal = ZERO
    fixed_fee: Decimal = ZERO
    fixed_fee_below: Decimal | None = None
    packaging: Decimal = ZERO
    shipping: Decimal = ZERO
    shipping_from: Decimal | None = None
    # nacional
    inbound_freight_pct: Decimal = ZERO
    # internacional
    exchange_rate: Decimal = ZERO
    intl_freight_usd: Decimal = ZERO  # por unidade
    regime: str = SIMPLIFIED
    insurance_pct: Decimal = ZERO
    fx_fee_pct: Decimal = ZERO
    ii_pct: Decimal = ZERO
    ipi_pct: Decimal = ZERO
    pis_pct: Decimal = ZERO
    cofins_pct: Decimal = ZERO
    icms_pct: Decimal = ZERO
    customs_fees: Decimal = ZERO  # R$ por embarque
    domestic_freight_pct: Decimal = ZERO

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            if name in ("origin", "regime", "quantity"):
                continue
            value = getattr(self, name)
            if value is None and name in ("sale_price", "fixed_fee_below", "shipping_from"):
                continue
            setattr(self, name, _d(value))
        self.quantity = int(self.quantity or 0)
        self.validate()

    @property
    def selling_pct(self) -> Decimal:
        return self.tax_pct + self.commission_pct + self.payment_fee_pct + self.marketing_pct + self.returns_pct

    def validate(self) -> None:
        if self.origin not in (NATIONAL, INTERNATIONAL):
            raise OpportunityError("Escolha se o produto é nacional ou internacional.")
        if self.unit_cost <= 0:
            raise OpportunityError("Informe o custo do produto no fornecedor.")
        if self.quantity < 1:
            raise OpportunityError("A quantidade do lote deve ser de pelo menos 1 unidade.")
        if self.sale_price is not None and self.sale_price <= 0:
            raise OpportunityError("O preço de venda deve ser maior que zero.")
        if self.origin == INTERNATIONAL and self.exchange_rate <= 0:
            raise OpportunityError("Cotação do dólar indisponível: informe a cotação manualmente.")
        pcts = ["tax_pct", "commission_pct", "payment_fee_pct", "marketing_pct", "returns_pct",
                "inbound_freight_pct", "insurance_pct", "fx_fee_pct", "ii_pct", "ipi_pct", "pis_pct",
                "cofins_pct", "domestic_freight_pct", "target_margin_pct"]
        for name in pcts:
            value = getattr(self, name)
            if value < 0 or value >= HUNDRED:
                raise OpportunityError("Percentuais devem estar entre 0% e 99,99%.")
        if self.icms_pct < 0 or self.icms_pct >= HUNDRED:
            raise OpportunityError("A alíquota de ICMS deve estar entre 0% e 99,99%.")
        for name in ["fixed_fee", "packaging", "shipping", "intl_freight_usd", "customs_fees"]:
            if getattr(self, name) < 0:
                raise OpportunityError("Custos e tarifas não podem ser negativos.")
        if self.selling_pct + self.target_margin_pct >= HUNDRED:
            raise OpportunityError(
                f"Os custos percentuais de venda ({self.selling_pct.normalize():f}%) somados à margem exigida "
                f"({self.target_margin_pct.normalize():f}%) chegam a 100% do preço: não existe preço viável. "
                "Revise impostos, comissão e marketing.")


@dataclass
class LandedCost:
    unit: Decimal
    lines: list[tuple[str, Decimal]]  # (descrição, R$ por unidade)
    regime_used: str = ""
    customs_value_usd: Decimal | None = None
    warnings: list[str] = field(default_factory=list)


def landed_cost(data: OpportunityInput, unit_cost: Decimal | None = None) -> LandedCost:
    cost = data.unit_cost if unit_cost is None else unit_cost
    if data.origin == NATIONAL:
        freight = cost * data.inbound_freight_pct / HUNDRED
        return LandedCost(unit=cost + freight, lines=[("Custo do fornecedor", cost), ("Frete de entrada", freight)])

    q = Decimal(data.quantity)
    rate = data.exchange_rate
    fob = cost * q
    freight = data.intl_freight_usd * q
    insurance = (fob + freight) * data.insurance_pct / HUNDRED
    cv_usd = fob + freight + insurance
    warnings = []
    regime = data.regime
    if regime == SIMPLIFIED and cv_usd > SIMPLIFIED_LIMIT_USD:
        regime = FORMAL
        warnings.append("A remessa passa de US$ 3.000: o cálculo usou importação formal (II, IPI, PIS/COFINS, "
                        "ICMS e despesas aduaneiras informados).")
    cv_brl = cv_usd * rate
    fx_cost = cv_brl * data.fx_fee_pct / HUNDRED
    icms_rate = data.icms_pct / HUNDRED
    ipi = pis = cofins = ZERO
    if regime == SIMPLIFIED:
        if cv_usd <= SIMPLIFIED_LOW_BAND_USD:
            ii_usd = cv_usd * Decimal("0.20")
        else:
            ii_usd = max(ZERO, cv_usd * Decimal("0.60") - Decimal("20"))
        ii = ii_usd * rate
        fees = data.customs_fees
        icms = (cv_brl + ii) * icms_rate / (ONE - icms_rate)
    else:
        ii = cv_brl * data.ii_pct / HUNDRED
        ipi = (cv_brl + ii) * data.ipi_pct / HUNDRED
        pis = cv_brl * data.pis_pct / HUNDRED
        cofins = cv_brl * data.cofins_pct / HUNDRED
        fees = data.customs_fees
        icms = (cv_brl + ii + ipi + pis + cofins + fees) * icms_rate / (ONE - icms_rate)
    domestic = cv_brl * data.domestic_freight_pct / HUNDRED
    per = [
        ("Produto (FOB convertido)", fob * rate),
        ("Frete internacional", freight * rate),
        ("Seguro", insurance * rate),
        ("Câmbio (IOF + spread)", fx_cost),
        ("Imposto de Importação (II)", ii),
        ("IPI", ipi),
        ("PIS/COFINS-Importação", pis + cofins),
        ("ICMS de importação", icms),
        ("Despesas aduaneiras / despacho", fees),
        ("Frete interno até o estoque", domestic),
    ]
    lines = [(label, value / q) for label, value in per if value]
    total = sum((value for _, value in per), ZERO)
    return LandedCost(unit=total / q, lines=lines, regime_used=regime, customs_value_usd=cv_usd, warnings=warnings)


def fixed_selling_costs(data: OpportunityInput, price: Decimal) -> Decimal:
    fee = data.fixed_fee if data.fixed_fee_below is None or price < data.fixed_fee_below else ZERO
    shipping = data.shipping if data.shipping_from is None or price >= data.shipping_from else ZERO
    return fee + shipping + data.packaging


def selling_cost_lines(data: OpportunityInput, price: Decimal) -> list[tuple[str, Decimal]]:
    fee = data.fixed_fee if data.fixed_fee_below is None or price < data.fixed_fee_below else ZERO
    shipping = data.shipping if data.shipping_from is None or price >= data.shipping_from else ZERO
    lines = [
        ("Impostos sobre a venda", price * data.tax_pct / HUNDRED),
        ("Comissão do canal", price * data.commission_pct / HUNDRED),
        ("Tarifa fixa do canal", fee),
        ("Taxa de pagamento", price * data.payment_fee_pct / HUNDRED),
        ("Marketing / aquisição de clientes", price * data.marketing_pct / HUNDRED),
        ("Devoluções e perdas", price * data.returns_pct / HUNDRED),
        ("Embalagem", data.packaging),
        ("Frete subsidiado", shipping),
    ]
    return [(label, money(value)) for label, value in lines if value]


def net_margin(data: OpportunityInput, price: Decimal, landed: Decimal) -> Decimal:
    return price - landed - price * data.selling_pct / HUNDRED - fixed_selling_costs(data, price)


def minimum_price(data: OpportunityInput, landed: Decimal, margin_pct: Decimal) -> Decimal:
    """Menor preço com margem líquida ≥ margin_pct, considerando as faixas das tarifas fixas."""
    denominator = ONE - data.selling_pct / HUNDRED - margin_pct / HUNDRED
    if denominator <= 0:
        raise OpportunityError("Os custos percentuais somados à margem exigida chegam a 100% do preço.")
    breaks = sorted({b for b in (data.fixed_fee_below, data.shipping_from) if b})
    bounds = [ZERO] + breaks + [None]
    candidates = []
    for low, high in zip(bounds, bounds[1:]):
        fixed = fixed_selling_costs(data, low)
        price = (landed + fixed) / denominator
        if price >= low and (high is None or price < high):
            candidates.append(price)
        if low > 0 and net_margin(data, low, landed) >= low * margin_pct / HUNDRED:
            candidates.append(low)
    best = min(candidates)
    rounded = best.quantize(TWO, ROUND_HALF_UP)
    if net_margin(data, rounded, landed) < rounded * margin_pct / HUNDRED:
        rounded += TWO  # arredondamento nunca deixa a margem abaixo da exigida
    return rounded


def max_unit_cost(data: OpportunityInput, price: Decimal) -> Decimal | None:
    """Maior custo no fornecedor (R$ ou US$) que ainda entrega a margem exigida no preço informado."""
    budget = price * (ONE - data.selling_pct / HUNDRED - data.target_margin_pct / HUNDRED) \
        - fixed_selling_costs(data, price)
    if budget <= 0:
        return None
    if data.origin == NATIONAL:
        return (budget / (ONE + data.inbound_freight_pct / HUNDRED)).quantize(TWO, ROUND_DOWN)
    low, high = ZERO, budget / data.exchange_rate
    for _ in range(60):
        mid = (low + high) / 2
        if landed_cost(data, mid).unit <= budget:
            low = mid
        else:
            high = mid
    return low.quantize(TWO, ROUND_DOWN)


VERDICTS = {
    "approved": ("Oportunidade aprovada", "success"),
    "attention": ("Margem aprovada, demanda em queda", "warning"),
    "rejected": ("Reprovada: margem abaixo do mínimo", "danger"),
    "no_price": ("Informe o preço de venda para validar", "secondary"),
}


@dataclass
class OpportunityResult:
    landed_cost: Decimal
    landed_lines: list[tuple[str, Decimal]]
    regime_used: str
    customs_value_usd: Decimal | None
    minimum_price: Decimal
    target_margin_pct: Decimal
    sale_price: Decimal | None = None
    selling_lines: list[tuple[str, Decimal]] = field(default_factory=list)
    selling_costs: Decimal | None = None
    net_margin: Decimal | None = None
    net_margin_pct: Decimal | None = None
    markup: Decimal | None = None
    max_unit_cost: Decimal | None = None
    lot_investment: Decimal = ZERO
    lot_revenue: Decimal | None = None
    lot_profit: Decimal | None = None
    roi_pct: Decimal | None = None
    is_viable: bool = False
    verdict: str = "no_price"
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return VERDICTS[self.verdict][0]

    @property
    def verdict_style(self) -> str:
        return VERDICTS[self.verdict][1]

    def as_dict(self) -> dict:
        def conv(value):
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, (list, tuple)):
                return [conv(v) for v in value]
            return value
        return {k: conv(v) for k, v in asdict(self).items()}


def simulate(data: OpportunityInput, trend_direction: str = "") -> OpportunityResult:
    landed = landed_cost(data)
    landed_unit = landed.unit
    result = OpportunityResult(
        landed_cost=money(landed_unit), landed_lines=[(label, money(v)) for label, v in landed.lines],
        regime_used=landed.regime_used, customs_value_usd=money(landed.customs_value_usd)
        if landed.customs_value_usd is not None else None,
        minimum_price=minimum_price(data, landed_unit, data.target_margin_pct),
        target_margin_pct=data.target_margin_pct, lot_investment=money(landed_unit * data.quantity),
        warnings=list(landed.warnings),
    )
    price = data.sale_price
    if price is None:
        return result
    margin = net_margin(data, price, landed_unit)
    result.sale_price = money(price)
    result.selling_lines = selling_cost_lines(data, price)
    result.selling_costs = money(price * data.selling_pct / HUNDRED + fixed_selling_costs(data, price))
    result.net_margin = money(margin)
    result.net_margin_pct = (margin / price * HUNDRED).quantize(TWO, ROUND_HALF_UP)
    result.markup = (price / landed_unit).quantize(TWO, ROUND_HALF_UP) if landed_unit > 0 else None
    result.max_unit_cost = max_unit_cost(data, price)
    result.lot_revenue = money(price * data.quantity)
    result.lot_profit = money(margin * data.quantity)
    result.roi_pct = (margin / landed_unit * HUNDRED).quantize(TWO, ROUND_HALF_UP) if landed_unit > 0 else None
    result.is_viable = result.net_margin_pct >= data.target_margin_pct
    if not result.is_viable:
        result.verdict = "rejected"
    elif trend_direction == "queda":
        result.verdict = "attention"
    else:
        result.verdict = "approved"
    return result
