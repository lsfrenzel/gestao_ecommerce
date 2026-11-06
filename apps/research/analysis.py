"""
Indicadores calculados sobre a série de interesse do Google Trends (funções puras).

O Google Trends entrega o interesse RELATIVO de 0 a 100 (100 = pico de buscas no período e na região,
considerando todos os termos comparados). Não é volume absoluto de buscas.

- Interesse médio       = média dos pontos completos do período.
- Interesse recente     = média do último quarto da série.
- Variação (%)          = (média do último quarto − média do quarto anterior) / média do quarto anterior × 100.
- Direção               = "alta" se variação ≥ +10%, "queda" se ≤ −10%, senão "estável".
- Índice de tendência   = 50% nível (interesse recente) + 50% impulso (50 + variação/2, limitado a 0–100).
  Heurística de 0 a 100 para ordenar oportunidades; não é uma previsão de vendas.
- Pico sazonal          = mês com maior interesse médio (séries de 12 meses ou mais).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

HUNDRED = Decimal("100")
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]
DIRECTIONS = {"alta": "Em alta", "estavel": "Estável", "queda": "Em queda"}


@dataclass
class KeywordStats:
    keyword: str
    average: Decimal
    recent: Decimal
    previous: Decimal
    growth_pct: Decimal | None
    direction: str  # alta | estavel | queda
    peak: int
    peak_label: str
    current: int
    score: int
    seasonal_peak: str
    points: int

    @property
    def direction_label(self) -> str:
        return DIRECTIONS[self.direction]


def _mean(values: list[int]) -> Decimal:
    if not values:
        return Decimal("0")
    return (Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.1"), ROUND_HALF_UP)


def keyword_stats(keyword: str, timeline: list[dict], index: int) -> KeywordStats:
    complete = [p for p in timeline if not p.get("partial")] or list(timeline)
    values = [int(p["values"][index]) if index < len(p["values"]) else 0 for p in complete]
    quarter = max(1, len(values) // 4)
    recent_vals = values[-quarter:]
    previous_vals = values[-2 * quarter:-quarter] if len(values) >= 2 * quarter else []
    recent, previous = _mean(recent_vals), _mean(previous_vals)
    growth = None
    if previous > 0:
        growth = ((recent - previous) / previous * HUNDRED).quantize(Decimal("0.1"), ROUND_HALF_UP)
    elif recent > 0 and previous_vals:
        growth = HUNDRED  # surgiu do zero
    if growth is None:
        direction = "estavel"
    elif growth >= 10:
        direction = "alta"
    elif growth <= -10:
        direction = "queda"
    else:
        direction = "estavel"
    peak = max(values) if values else 0
    peak_point = complete[values.index(peak)] if values else {}
    momentum = Decimal("50") + (growth or Decimal("0")) / 2
    momentum = min(HUNDRED, max(Decimal("0"), momentum))
    score = int(((min(recent, HUNDRED) + momentum) / 2).quantize(Decimal("1"), ROUND_HALF_UP))
    return KeywordStats(
        keyword=keyword, average=_mean(values), recent=recent, previous=previous, growth_pct=growth,
        direction=direction, peak=peak, peak_label=peak_point.get("label", ""),
        current=values[-1] if values else 0, score=score,
        seasonal_peak=seasonal_peak(complete, index), points=len(values),
    )


def seasonal_peak(timeline: list[dict], index: int) -> str:
    """Mês com maior interesse médio — só faz sentido com pelo menos ~12 meses de dados."""
    if len(timeline) < 2:
        return ""
    first, last = date.fromisoformat(timeline[0]["date"]), date.fromisoformat(timeline[-1]["date"])
    if (last - first).days < 330:
        return ""
    by_month: dict[int, list[int]] = defaultdict(list)
    for point in timeline:
        by_month[date.fromisoformat(point["date"]).month].append(int(point["values"][index]))
    best = max(by_month, key=lambda m: _mean(by_month[m]))
    return MONTHS[best - 1]


def all_stats(result) -> list[KeywordStats]:
    return [keyword_stats(k, result.timeline, i) for i, k in enumerate(result.keywords)]
