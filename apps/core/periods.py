"""Filtros de período usados no dashboard, relatórios e financeiro."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone

PERIOD_CHOICES = [
    ("today", "Hoje"),
    ("7d", "Últimos 7 dias"),
    ("30d", "Últimos 30 dias"),
    ("month", "Mês atual"),
    ("last_month", "Mês anterior"),
    ("year", "Ano atual"),
    ("custom", "Período personalizado"),
]
PERIOD_LABELS = dict(PERIOD_CHOICES)


@dataclass
class Period:
    key: str
    start: date
    end: date  # inclusivo

    @property
    def label(self) -> str:
        if self.key == "custom":
            return f"{self.start:%d/%m/%Y} a {self.end:%d/%m/%Y}"
        return PERIOD_LABELS.get(self.key, "")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_dt(self) -> datetime:
        return timezone.make_aware(datetime.combine(self.start, time.min))

    @property
    def end_dt(self) -> datetime:
        """Limite exclusivo (início do dia seguinte)."""
        return timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min))

    def previous(self) -> "Period":
        span = self.end - self.start
        end = self.start - timedelta(days=1)
        return Period("custom", end - span, end)

    def querystring(self) -> str:
        if self.key == "custom":
            return f"period=custom&start={self.start:%Y-%m-%d}&end={self.end:%Y-%m-%d}"
        return f"period={self.key}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def resolve_period(params, default: str = "30d", today: date | None = None) -> Period:
    today = today or timezone.localdate()
    key = (params.get("period") or default).strip()
    if key == "today":
        return Period(key, today, today)
    if key == "7d":
        return Period(key, today - timedelta(days=6), today)
    if key == "30d":
        return Period(key, today - timedelta(days=29), today)
    if key == "month":
        return Period(key, today.replace(day=1), today)
    if key == "last_month":
        last_end = today.replace(day=1) - timedelta(days=1)
        return Period(key, last_end.replace(day=1), last_end)
    if key == "year":
        return Period(key, today.replace(month=1, day=1), today)
    if key == "custom":
        start = _parse_date(params.get("start"))
        end = _parse_date(params.get("end"))
        if start and end:
            if start > end:
                start, end = end, start
            if (end - start).days > 366 * 5:
                start = end - timedelta(days=366 * 5)
            return Period(key, start, end)
    return resolve_period({"period": default}, default="30d", today=today)


def month_starts(start: date, end: date) -> list[date]:
    months, cursor = [], start.replace(day=1)
    while cursor <= end:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months
