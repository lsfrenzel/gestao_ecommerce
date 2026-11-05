"""Filtros de formatação brasileira e helpers de interface."""

from __future__ import annotations

import datetime

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.core.money import format_brl, format_number, format_qty, to_decimal

register = template.Library()


@register.filter
def brl(value):
    return format_brl(value)


@register.filter
def pct(value, places=1):
    if value is None or value == "":
        return "—"
    return f"{format_number(value, int(places))}%"


@register.filter
def num(value, places=2):
    return format_number(value, int(places))


@register.filter
def qty(value):
    return format_qty(value)


@register.filter
def neg(value):
    v = to_decimal(value, None)
    return v is not None and v < 0


@register.filter
def attr(obj, name: str):
    """Resolve atributos encadeados ('supplier.name') e métodos sem argumentos."""
    value = obj
    for part in name.split("."):
        if value is None:
            return None
        value = getattr(value, part, None)
        if callable(value) and not hasattr(value, "do_not_call_in_templates"):
            value = value()
    return value


@register.simple_tag
def cell(obj, name: str, fmt: str = "text"):
    value = attr(obj, name)
    if fmt == "brl":
        return format_brl(value)
    if fmt == "pct":
        return "—" if value is None else f"{format_number(value, 1)}%"
    if fmt == "qty":
        return format_qty(value)
    if fmt == "date":
        if isinstance(value, datetime.datetime):
            from django.utils import timezone
            return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")
        return value.strftime("%d/%m/%Y") if value else "—"
    if fmt == "bool":
        return mark_safe('<i class="bi bi-check-circle-fill text-success"></i>' if value
                         else '<i class="bi bi-dash-circle text-secondary"></i>')
    if fmt == "badge":
        return format_html('<span class="badge badge-soft">{}</span>', value or "—")
    if fmt == "display":
        display = getattr(obj, f"get_{name}_display", None)
        return display() if display else value
    return "—" if value in (None, "") else value


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Mantém a querystring atual alterando apenas os parâmetros informados."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.simple_tag
def fmtcell(value, fmt):
    from apps.core.exports import format_cell

    return format_cell(value, fmt)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def in_list(value, items):
    return value in (items or [])
