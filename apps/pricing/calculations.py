"""
Cálculos de precificação — funções puras em Decimal (sem acesso a banco), cobertas por testes.

Definições usadas no sistema:

- Custo unitário total = aquisição/produção + frete de entrada + embalagem + outros custos unitários.
- Despesas variáveis percentuais = impostos + comissão do marketplace + taxa de pagamento + comissão de venda
  + outras (% do preço de venda).
- Despesas variáveis fixas por venda = frete subsidiado + separação/embalagem + outros (R$ por unidade).

- Preço sugerido = (Custo unitário total + Despesas variáveis fixas por venda)
                   / (1 − Σ% despesas variáveis − % margem de contribuição desejada)
- Preço mínimo = mesma fórmula com margem desejada = 0 (contribuição zero: cobre custos e despesas variáveis).

- Markup (%) = (Preço − Custo unitário) / Custo unitário × 100
- Markup multiplicador = Preço / Custo unitário
- Margem sobre o custo = (Margem de contribuição em R$ / Custo unitário) × 100
- Margem de contribuição (R$) = Preço − Custo unitário − Despesas variáveis (percentuais + fixas por venda)
- Margem de contribuição (%) = MC / Preço × 100

Markup ≠ margem: markup é calculado sobre o CUSTO; margem é calculada sobre o PREÇO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from apps.core.money import HUNDRED, ONE, ZERO, money, to_decimal

PERCENT_FIELDS = ["tax_pct", "marketplace_pct", "payment_fee_pct", "sales_commission_pct", "other_pct"]
UNIT_COST_FIELDS = ["acquisition_cost", "inbound_freight", "packaging_cost", "other_unit_costs"]
PER_SALE_FIELDS = ["subsidized_shipping", "handling_cost", "other_per_sale"]


class PricingError(ValueError):
    """Combinação de dados que não permite calcular um preço válido."""


def _d(value) -> Decimal:
    result = to_decimal(value, None)
    if result is None:
        return ZERO
    return result


def markup_percent(price, unit_cost) -> Decimal | None:
    """Markup (%) sobre o custo. None quando o custo é zero (indefinido)."""
    price, unit_cost = _d(price), _d(unit_cost)
    if unit_cost <= 0:
        return None
    return (price - unit_cost) / unit_cost * HUNDRED


def markup_multiplier(price, unit_cost) -> Decimal | None:
    price, unit_cost = _d(price), _d(unit_cost)
    if unit_cost <= 0:
        return None
    return price / unit_cost


@dataclass
class PricingInput:
    acquisition_cost: Decimal = ZERO
    inbound_freight: Decimal = ZERO
    packaging_cost: Decimal = ZERO
    other_unit_costs: Decimal = ZERO
    tax_pct: Decimal = ZERO
    marketplace_pct: Decimal = ZERO
    payment_fee_pct: Decimal = ZERO
    sales_commission_pct: Decimal = ZERO
    other_pct: Decimal = ZERO
    subsidized_shipping: Decimal = ZERO
    handling_cost: Decimal = ZERO
    other_per_sale: Decimal = ZERO
    target_margin_pct: Decimal = ZERO

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            setattr(self, name, _d(getattr(self, name)))
        errors = validate_input(self)
        if errors:
            raise PricingError(" ".join(errors))

    @property
    def unit_cost(self) -> Decimal:
        return sum((getattr(self, f) for f in UNIT_COST_FIELDS), ZERO)

    @property
    def variable_pct(self) -> Decimal:
        return sum((getattr(self, f) for f in PERCENT_FIELDS), ZERO)

    @property
    def per_sale_costs(self) -> Decimal:
        return sum((getattr(self, f) for f in PER_SALE_FIELDS), ZERO)


def validate_input(data: PricingInput) -> list[str]:
    errors = []
    for name in UNIT_COST_FIELDS + PER_SALE_FIELDS:
        if getattr(data, name) < 0:
            errors.append("Custos e despesas não podem ser negativos.")
            break
    for name in PERCENT_FIELDS + ["target_margin_pct"]:
        value = getattr(data, name)
        if value < 0 or value >= HUNDRED:
            errors.append("Percentuais devem estar entre 0% e 99,99%.")
            break
    return errors


@dataclass
class PriceAnalysis:
    """Resultado da análise de um preço de venda específico."""

    price: Decimal
    unit_cost: Decimal
    variable_pct_amount: Decimal  # despesas percentuais em R$
    per_sale_costs: Decimal
    total_variable_expenses: Decimal  # despesas variáveis por venda (R$), sem o custo do produto
    contribution_margin: Decimal  # R$ por unidade
    contribution_margin_pct: Decimal | None
    markup_pct: Decimal | None
    markup_multiplier: Decimal | None
    margin_on_cost_pct: Decimal | None
    result_per_unit: Decimal  # resultado estimado por unidade (antes das despesas fixas) = MC

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


def analyze_price(data: PricingInput, price) -> PriceAnalysis:
    price = _d(price)
    if price < 0:
        raise PricingError("O preço de venda não pode ser negativo.")
    unit_cost = data.unit_cost
    pct_amount = price * data.variable_pct / HUNDRED
    variable = pct_amount + data.per_sale_costs
    cm = price - unit_cost - variable
    return PriceAnalysis(
        price=money(price),
        unit_cost=money(unit_cost),
        variable_pct_amount=money(pct_amount),
        per_sale_costs=money(data.per_sale_costs),
        total_variable_expenses=money(variable),
        contribution_margin=money(cm),
        contribution_margin_pct=(cm / price * HUNDRED).quantize(Decimal("0.01"), ROUND_HALF_UP) if price > 0 else None,
        markup_pct=_q2(markup_percent(price, unit_cost)),
        markup_multiplier=_q4(markup_multiplier(price, unit_cost)),
        margin_on_cost_pct=_q2(cm / unit_cost * HUNDRED) if unit_cost > 0 else None,
        result_per_unit=money(cm),
    )


def _q2(value):
    return None if value is None else value.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _q4(value):
    return None if value is None else value.quantize(Decimal("0.0001"), ROUND_HALF_UP)


def price_for_margin(data: PricingInput, margin_pct) -> Decimal:
    """
    Preço = (Custo unitário + Despesas fixas por venda) / (1 − %variáveis − %margem).
    Levanta PricingError se o denominador for ≤ 0.
    """
    margin = _d(margin_pct)
    denominator = ONE - data.variable_pct / HUNDRED - margin / HUNDRED
    if denominator <= 0:
        raise PricingError(
            "A soma das despesas variáveis percentuais "
            f"({data.variable_pct.normalize():f}%) com a margem desejada ({margin.normalize():f}%) "
            "é igual ou superior a 100% do preço. Não existe preço que atenda a essa combinação: "
            "reduza a margem desejada ou os percentuais de despesas."
        )
    numerator = data.unit_cost + data.per_sale_costs
    return money(numerator / denominator)


@dataclass
class PricingResult:
    unit_cost: Decimal
    minimum_price: Decimal
    suggested_price: Decimal
    analysis: PriceAnalysis
    variable_pct: Decimal
    per_sale_costs: Decimal
    warnings: list[str] = field(default_factory=list)


def calculate_pricing(data: PricingInput) -> PricingResult:
    minimum = price_for_margin(data, ZERO)
    suggested = price_for_margin(data, data.target_margin_pct)
    warnings = []
    if data.unit_cost == 0:
        warnings.append("Custo unitário zerado: markup e margem sobre o custo ficam indefinidos.")
    return PricingResult(
        unit_cost=money(data.unit_cost),
        minimum_price=minimum,
        suggested_price=suggested,
        analysis=analyze_price(data, suggested),
        variable_pct=data.variable_pct,
        per_sale_costs=money(data.per_sale_costs),
        warnings=warnings,
    )


def simulate_prices(data: PricingInput, prices) -> list[PriceAnalysis]:
    return [analyze_price(data, p) for p in prices if _d(p) > 0]


def price_curve(data: PricingInput, center: Decimal, steps: int = 9, spread_pct: Decimal = Decimal("40")) -> list[Decimal]:
    """Gera preços ao redor de `center` (±spread%) para o gráfico do simulador."""
    center = _d(center)
    if center <= 0:
        return []
    low = center * (ONE - spread_pct / HUNDRED)
    high = center * (ONE + spread_pct / HUNDRED)
    step = (high - low) / (steps - 1)
    return [money(low + step * i) for i in range(steps)]
