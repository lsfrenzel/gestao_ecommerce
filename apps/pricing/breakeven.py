"""
Ponto de equilíbrio — funções puras em Decimal.

- Margem de contribuição unitária (MCu) = Preço − (custo unitário + despesas variáveis por unidade)
- Índice de MC = MCu / Preço
- PE em faturamento = Despesas fixas / Índice de MC
- PE em unidades = Despesas fixas / MCu
- Mix de produtos: MC média ponderada = Σ (participação_i × MCu_i), com participação em unidades vendidas.

O ponto de equilíbrio depende das premissas informadas (preços, custos, despesas e mix de vendas):
mudanças no mix alteram o resultado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from apps.core.money import HUNDRED, ONE, ZERO, money, to_decimal


class BreakevenError(ValueError):
    pass


def _d(value) -> Decimal:
    v = to_decimal(value, None)
    return ZERO if v is None else v


def unit_contribution(price, variable_cost) -> Decimal:
    return _d(price) - _d(variable_cost)


def contribution_ratio(price, variable_cost) -> Decimal | None:
    price = _d(price)
    if price <= 0:
        return None
    return unit_contribution(price, variable_cost) / price


def breakeven_units(fixed_costs, unit_cm) -> Decimal | None:
    """None quando a MC unitária é ≤ 0 (equilíbrio impossível)."""
    unit_cm = _d(unit_cm)
    if unit_cm <= 0:
        return None
    return _d(fixed_costs) / unit_cm


def breakeven_revenue(fixed_costs, cm_ratio) -> Decimal | None:
    cm_ratio = to_decimal(cm_ratio, None)
    if cm_ratio is None or cm_ratio <= 0:
        return None
    return _d(fixed_costs) / cm_ratio


@dataclass
class MixItem:
    name: str
    price: Decimal
    variable_cost: Decimal
    share_pct: Decimal  # participação no mix (% das unidades vendidas)

    def __post_init__(self):
        self.price, self.variable_cost, self.share_pct = _d(self.price), _d(self.variable_cost), _d(self.share_pct)

    @property
    def unit_cm(self) -> Decimal:
        return self.price - self.variable_cost


@dataclass
class BreakevenResult:
    fixed_costs: Decimal
    avg_price: Decimal
    avg_variable_cost: Decimal
    unit_cm: Decimal
    cm_ratio: Decimal | None
    cm_ratio_pct: Decimal | None
    breakeven_units: Decimal | None  # arredondado para cima (unidades inteiras)
    breakeven_revenue: Decimal | None
    items: list[dict] = field(default_factory=list)
    current_revenue: Decimal | None = None
    current_result: Decimal | None = None
    safety_margin_pct: Decimal | None = None
    warnings: list[str] = field(default_factory=list)


def _ceil(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.to_integral_value(rounding=ROUND_CEILING)


def compute(fixed_costs, items: list[MixItem], current_revenue=None) -> BreakevenResult:
    """Calcula o PE para um produto (lista com 1 item, participação 100%) ou um mix."""
    fixed = _d(fixed_costs)
    if fixed < 0:
        raise BreakevenError("As despesas fixas não podem ser negativas.")
    items = [i for i in items if i.share_pct > 0]
    if not items:
        raise BreakevenError("Informe ao menos um produto com participação no mix maior que zero.")
    for item in items:
        if item.price <= 0:
            raise BreakevenError(f"O preço de “{item.name}” deve ser maior que zero.")
        if item.variable_cost < 0:
            raise BreakevenError(f"O custo variável de “{item.name}” não pode ser negativo.")
    total_share = sum((i.share_pct for i in items), ZERO)
    weights = [i.share_pct / total_share for i in items]  # normaliza para 100%

    avg_price = sum((w * i.price for w, i in zip(weights, items)), ZERO)
    avg_var = sum((w * i.variable_cost for w, i in zip(weights, items)), ZERO)
    unit_cm = avg_price - avg_var
    ratio = unit_cm / avg_price if avg_price > 0 else None
    warnings = []
    if unit_cm <= 0:
        warnings.append("A margem de contribuição média é zero ou negativa: cada venda aumenta o prejuízo e "
                        "não existe ponto de equilíbrio com essas premissas.")
    if total_share != HUNDRED and len(items) > 1:
        warnings.append(f"As participações somavam {total_share.normalize():f}% e foram normalizadas para 100%.")

    be_units = breakeven_units(fixed, unit_cm)
    be_revenue = breakeven_revenue(fixed, ratio)
    result = BreakevenResult(
        fixed_costs=money(fixed),
        avg_price=money(avg_price),
        avg_variable_cost=money(avg_var),
        unit_cm=money(unit_cm),
        cm_ratio=ratio,
        cm_ratio_pct=None if ratio is None else (ratio * HUNDRED).quantize(Decimal("0.01")),
        breakeven_units=_ceil(be_units),
        breakeven_revenue=None if be_revenue is None else money(be_revenue),
        warnings=warnings,
    )
    for w, item in zip(weights, items):
        result.items.append({
            "name": item.name,
            "price": money(item.price),
            "variable_cost": money(item.variable_cost),
            "unit_cm": money(item.unit_cm),
            "cm_ratio_pct": (item.unit_cm / item.price * HUNDRED).quantize(Decimal("0.01")),
            "share_pct": (w * HUNDRED).quantize(Decimal("0.01")),
            "units": None if be_units is None else _ceil(be_units * w),
            "revenue": None if be_units is None else money(be_units * w * item.price),
        })
    if current_revenue is not None:
        current = _d(current_revenue)
        result.current_revenue = money(current)
        if ratio is not None:
            result.current_result = money(current * ratio - fixed)
        if be_revenue is not None and current > 0:
            result.safety_margin_pct = ((current - be_revenue) / current * HUNDRED).quantize(Decimal("0.01"))
    return result


def units_for_target(fixed_costs, unit_cm, target_profit) -> Decimal | None:
    """Unidades necessárias para atingir uma meta de resultado (lucro operacional)."""
    unit_cm = _d(unit_cm)
    if unit_cm <= 0:
        return None
    return _ceil((_d(fixed_costs) + _d(target_profit)) / unit_cm)


def revenue_for_target(fixed_costs, cm_ratio, target_profit) -> Decimal | None:
    cm_ratio = to_decimal(cm_ratio, None)
    if cm_ratio is None or cm_ratio <= 0:
        return None
    return money((_d(fixed_costs) + _d(target_profit)) / cm_ratio)


def scenarios(result: BreakevenResult, base_revenue=None, *, sales_increases=(10, 20, 30),
              fixed_increase_pct=None, cm_improvement_pp=None) -> dict:
    """Simulações de sensibilidade sobre o resultado calculado."""
    out: dict = {"sales": [], "fixed": None, "cm": None}
    ratio = result.cm_ratio
    fixed = result.fixed_costs
    base = _d(base_revenue if base_revenue is not None else result.current_revenue)
    if ratio is not None and base > 0:
        for inc in sales_increases:
            revenue = base * (ONE + _d(inc) / HUNDRED)
            out["sales"].append({"increase_pct": _d(inc), "revenue": money(revenue),
                                 "result": money(revenue * ratio - fixed)})
    if fixed_increase_pct is not None and ratio is not None and ratio > 0:
        new_fixed = fixed * (ONE + _d(fixed_increase_pct) / HUNDRED)
        out["fixed"] = {"increase_pct": _d(fixed_increase_pct), "fixed_costs": money(new_fixed),
                        "breakeven_revenue": money(new_fixed / ratio),
                        "breakeven_units": units_for_target(new_fixed, result.unit_cm, 0)}
    if cm_improvement_pp is not None and ratio is not None:
        new_ratio = ratio + _d(cm_improvement_pp) / HUNDRED
        if new_ratio > 0:
            new_unit_cm = result.avg_price * new_ratio
            out["cm"] = {"improvement_pp": _d(cm_improvement_pp),
                         "cm_ratio_pct": (new_ratio * HUNDRED).quantize(Decimal("0.01")),
                         "breakeven_revenue": money(fixed / new_ratio),
                         "breakeven_units": units_for_target(fixed, new_unit_cm, 0)}
    return out


def chart_series(result: BreakevenResult, points: int = 11) -> dict:
    """Retas de receita e custo total para o gráfico custo-volume-lucro."""
    if result.cm_ratio is None or result.breakeven_revenue is None:
        return {}
    top = max(result.breakeven_revenue * 2, (result.current_revenue or ZERO) * Decimal("1.2"))
    if top <= 0:
        top = Decimal("1000")
    variable_ratio = ONE - result.cm_ratio
    labels, revenue, cost = [], [], []
    for i in range(points):
        r = top * i / (points - 1)
        labels.append(str(money(r)))
        revenue.append(str(money(r)))
        cost.append(str(money(result.fixed_costs + r * variable_ratio)))
    return {"labels": labels, "revenue": revenue, "cost": cost,
            "breakeven": str(result.breakeven_revenue),
            "current": str(result.current_revenue) if result.current_revenue is not None else None}
