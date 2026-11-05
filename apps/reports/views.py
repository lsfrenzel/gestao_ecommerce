from __future__ import annotations

import json
from collections import OrderedDict
from decimal import Decimal

from django.http import Http404
from django.shortcuts import render

from apps.core.audit import log_action
from apps.core.exports import csv_response, format_cell, pdf_response
from apps.core.periods import PERIOD_CHOICES, resolve_period
from apps.core.permissions import company_view
from apps.products.models import Category, Product
from apps.sales.models import Channel

from .registry import REPORTS, REPORTS_BY_SLUG, Filters


@company_view("reports")
def index(request):
    groups: OrderedDict[str, list] = OrderedDict()
    for r in REPORTS:
        groups.setdefault(r.group, []).append(r)
    return render(request, "reports/index.html", {"groups": groups})


@company_view("reports")
def report_view(request, slug):
    definition = REPORTS_BY_SLUG.get(slug)
    if definition is None:
        raise Http404
    company = request.company
    period = resolve_period(request.GET, default="30d")
    product = category = None
    if definition.uses_product and request.GET.get("produto", "").isdigit():
        product = Product.objects.for_company(company).filter(pk=int(request.GET["produto"])).first()
    if definition.uses_category and request.GET.get("categoria", "").isdigit():
        category = Category.objects.for_company(company).filter(pk=int(request.GET["categoria"])).first()
    channel = request.GET.get("canal", "") if definition.uses_channel else ""
    if channel not in Channel.values:
        channel = ""
    filters = Filters(start=period.start, end=period.end, period_label=period.label, product=product,
                      category=category, channel=channel)
    report, chart = definition.builder(company, filters)
    subtitle = []
    if definition.uses_period:
        subtitle.append(f"Período: {period.start:%d/%m/%Y} a {period.end:%d/%m/%Y}")
    if product:
        subtitle.append(f"Produto: {product}")
    if category:
        subtitle.append(f"Categoria: {category}")
    if channel:
        subtitle.append(f"Canal: {Channel(channel).label}")
    report.subtitle = " · ".join(subtitle)

    export = request.GET.get("exportar")
    if export == "csv":
        log_action("report.export", request=request, details={"relatorio": slug, "formato": "csv"})
        return csv_response(report)
    if export == "pdf":
        if not company.has_feature("reports_pdf"):
            return render(request, "companies/feature_unavailable.html", {"feature": "reports_pdf"}, status=402)
        log_action("report.export", request=request, details={"relatorio": slug, "formato": "pdf"})
        return pdf_response(report, company_name=str(company))

    def display(row):
        return [(format_cell(v, c.fmt), c.fmt not in ("text", "date"),
                 isinstance(v, Decimal) and v < 0) for v, c in zip(row, report.columns)]

    return render(request, "reports/report.html", {
        "definition": definition, "report": report,
        "rows": [display(r) for r in report.rows], "totals": display(report.totals) if report.totals else None, "chart": json.dumps(chart) if chart else None,
        "period": period, "period_choices": PERIOD_CHOICES,
        "products": Product.objects.for_company(company).filter(is_active=True).only("id", "name", "sku")
        if definition.uses_product else [],
        "categories": Category.objects.for_company(company) if definition.uses_category else [],
        "channels": Channel.choices, "product": product, "category": category, "channel": channel,
        "pdf_enabled": company.has_feature("reports_pdf"),
    })
