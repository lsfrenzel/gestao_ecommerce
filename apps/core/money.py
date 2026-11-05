"""
Utilitários de precisão decimal.

Regra do sistema: valores financeiros SEMPRE em `Decimal`, nunca `float`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def to_decimal(value, default: Decimal | None = ZERO) -> Decimal | None:
    """Converte com segurança (aceita '1.234,56', '1234.56', int, Decimal). Float é convertido via str."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return default


def money(value) -> Decimal:
    """Arredonda para centavos (meio para cima, padrão comercial)."""
    return (to_decimal(value) or ZERO).quantize(CENT, rounding=ROUND_HALF_UP)


def q(value, places: str = "0.01") -> Decimal:
    return (to_decimal(value) or ZERO).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def pct_to_rate(percent) -> Decimal:
    """12,5 (%) → 0,125."""
    return (to_decimal(percent) or ZERO) / HUNDRED


def safe_div(numerator, denominator) -> Decimal | None:
    """Divisão que retorna None quando o denominador é zero/ausente (nunca levanta ZeroDivisionError)."""
    n = to_decimal(numerator, None)
    d = to_decimal(denominator, None)
    if n is None or d is None or d == 0:
        return None
    return n / d


def format_brl(value) -> str:
    """Decimal → 'R$ 1.234,56' (negativos: '-R$ 1.234,56')."""
    if value is None or value == "":
        return "—"
    v = money(value)
    sign = "-" if v < 0 else ""
    integer, _, cents = f"{abs(v):.2f}".partition(".")
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    return f"{sign}R$ {'.'.join(groups) or '0'},{cents}"


def format_number(value, places: int = 2) -> str:
    if value is None or value == "":
        return "—"
    v = q(value, "1" if places == 0 else "0." + "0" * places)
    sign = "-" if v < 0 else ""
    text = f"{abs(v):.{places}f}"
    integer, _, frac = text.partition(".")
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    out = ".".join(groups) or "0"
    return f"{sign}{out},{frac}" if places else f"{sign}{out}"


def format_qty(value) -> str:
    """Quantidade sem zeros à direita: 10.000 → '10', 2.500 → '2,5'."""
    if value is None or value == "":
        return "—"
    v = to_decimal(value)
    if v == v.to_integral_value():
        return format_number(v, 0)
    text = format(v.normalize(), "f")
    places = len(text.split(".")[1]) if "." in text else 0
    return format_number(v, min(places, 3))
