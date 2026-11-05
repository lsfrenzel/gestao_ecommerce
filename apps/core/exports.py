"""
Exportação de tabelas em CSV e PDF a partir de uma estrutura única (`TableReport`).

O CSV usa `;` e BOM UTF-8 para abrir corretamente no Excel em português.
Células que começam com =, +, -, @ são prefixadas com apóstrofo (proteção contra injeção de fórmulas).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify

from .money import format_brl, format_number, format_qty


@dataclass
class Column:
    label: str
    fmt: str = "text"  # text | brl | pct | qty | int | date


@dataclass
class TableReport:
    title: str
    columns: list[Column]
    rows: list[list] = field(default_factory=list)
    totals: list | None = None
    subtitle: str = ""
    notes: list[str] = field(default_factory=list)


def format_cell(value, fmt: str) -> str:
    if value is None or value == "":
        return "—" if fmt != "text" else ""
    if fmt == "brl":
        return format_brl(value)
    if fmt == "pct":
        return f"{format_number(value, 1)}%"
    if fmt == "qty":
        return format_qty(value)
    if fmt == "int":
        return format_number(value, 0)
    if fmt == "date":
        if isinstance(value, datetime):
            return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")
        if isinstance(value, date):
            return value.strftime("%d/%m/%Y")
    return str(value)


def _csv_value(value, fmt: str) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        text = format_number(value, 2 if fmt in ("brl", "pct", "text") else 3).replace(".", "")
        if fmt == "qty":
            text = format_qty(value).replace(".", "")
        return text
    if isinstance(value, (date, datetime)):
        return format_cell(value, "date")
    text = str(value)
    if text and text[0] in "=+-@\t\r":
        text = "'" + text
    return text


def csv_response(report: TableReport, filename: str | None = None) -> HttpResponse:
    name = filename or f"{slugify(report.title)}-{timezone.localdate():%Y%m%d}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response.write("﻿")
    writer = csv.writer(response, delimiter=";")
    writer.writerow([c.label for c in report.columns])
    for row in report.rows:
        writer.writerow([_csv_value(v, c.fmt) for v, c in zip(row, report.columns)])
    if report.totals:
        writer.writerow([_csv_value(v, c.fmt) for v, c in zip(report.totals, report.columns)])
    return response


def pdf_response(report: TableReport, company_name: str = "", filename: str | None = None) -> HttpResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    wide = len(report.columns) > 6
    pagesize = landscape(A4) if wide else A4
    doc = SimpleDocTemplate(buffer, pagesize=pagesize, leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=report.title)
    styles = getSampleStyleSheet()
    cell_style = styles["BodyText"].clone("cell", fontSize=7.5, leading=9)
    head_style = styles["BodyText"].clone("head", fontSize=7.5, leading=9, textColor=colors.white,
                                          fontName="Helvetica-Bold")
    story = [Paragraph(report.title, styles["Title"])]
    meta = " · ".join(p for p in [company_name, report.subtitle,
                                  f"Gerado em {timezone.localtime():%d/%m/%Y %H:%M}"] if p)
    story += [Paragraph(meta, styles["Normal"]), Spacer(1, 6 * mm)]

    data = [[Paragraph(c.label, head_style) for c in report.columns]]
    for row in report.rows:
        data.append([Paragraph(format_cell(v, c.fmt), cell_style) for v, c in zip(row, report.columns)])
    if report.totals:
        data.append([Paragraph(f"<b>{format_cell(v, c.fmt)}</b>", cell_style)
                     for v, c in zip(report.totals, report.columns)])
    if len(data) == 1:
        data.append([Paragraph("Sem dados no período/filtros selecionados.", cell_style)]
                    + [""] * (len(report.columns) - 1))

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b2545")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7e2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    for note in report.notes:
        story += [Spacer(1, 3 * mm), Paragraph(note, styles["Italic"])]
    doc.build(story)

    name = filename or f"{slugify(report.title)}-{timezone.localdate():%Y%m%d}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response
