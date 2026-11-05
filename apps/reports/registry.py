"""
Central de relatórios. Cada relatório recebe (empresa, filtros) e devolve um `TableReport` (usado na tela,
no CSV e no PDF) e, opcionalmente, dados de gráfico. Todos os dados são filtrados pela empresa ativa.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from django.db.models import F, Sum

from apps.core.exports import Column, TableReport
from apps.core.money import HUNDRED, ZERO, format_brl, format_number, safe_div
from apps.dashboard import metrics
from apps.finance import services as finance
from apps.inventory.reorder import build_rows
from apps.pricing import breakeven as be
from apps.pricing.calculations import markup_multiplier, markup_percent
from apps.products.models import Product
from apps.sales.models import SalesOrderItem


@dataclass
class Filters:
    start: object
    end: object
    period_label: str
    product: Product | None = None
    category: object = None
    channel: str = ""


@dataclass
class ReportDef:
    slug: str
    title: str
    description: str
    group: str
    builder: Callable
    uses_period: bool = True
    uses_product: bool = False
    uses_category: bool = False
    uses_channel: bool = False


def _pct(part, whole):
    r = safe_div(part, whole)
    return None if r is None else r * HUNDRED


def _orders(company, f: Filters):
    qs = metrics.revenue_orders(company, f.start, f.end)
    if f.channel:
        qs = qs.filter(channel=f.channel)
    if f.product:
        qs = qs.filter(items__product=f.product).distinct()
    if f.category:
        qs = qs.filter(items__product__category=f.category).distinct()
    return qs


def revenue_report(company, f: Filters):
    orders = _orders(company, f)
    rows_by_day = (orders.values("order_date").annotate(
        revenue=Sum("total"), cogs=Sum("cogs"),
        var=Sum(F("taxes") + F("commission") + F("fees") + F("shipping_cost"))).order_by("order_date"))
    rows, totals = [], [ZERO] * 5
    count_by_day = {}
    for o in orders.values_list("order_date", flat=True):
        count_by_day[o] = count_by_day.get(o, 0) + 1
    for r in rows_by_day:
        n = count_by_day.get(r["order_date"], 0)
        cm = (r["revenue"] or ZERO) - (r["cogs"] or ZERO) - (r["var"] or ZERO)
        rows.append([r["order_date"], n, r["revenue"], safe_div(r["revenue"], n), r["cogs"], r["var"], cm])
        totals = [totals[0] + n, totals[1] + (r["revenue"] or ZERO), totals[2] + (r["cogs"] or ZERO),
                  totals[3] + (r["var"] or ZERO), totals[4] + cm]
    report = TableReport("Relatório de faturamento", [
        Column("Data", "date"), Column("Pedidos pagos", "int"), Column("Faturamento", "brl"),
        Column("Ticket médio", "brl"), Column("CMV", "brl"), Column("Despesas variáveis", "brl"),
        Column("Margem de contribuição", "brl")], rows,
        totals=["Total", totals[0], totals[1], safe_div(totals[1], totals[0]), totals[2], totals[3], totals[4]],
        notes=["Considera somente pedidos pagos, em separação, enviados ou entregues (pendentes não são receita)."])
    chart = {"type": "line", "labels": [r[0].strftime("%d/%m") for r in rows],
             "datasets": [{"label": "Faturamento", "data": [str(r[2] or 0) for r in rows]}]}
    return report, chart


def _ranking(company, f: Filters, order_by: str, limit: int | None):
    return metrics.product_ranking(company, f.start, f.end, limit=limit or 0, order_by=order_by,
                                   category=f.category, channel=f.channel or None)


def sales_by_product_report(company, f: Filters):
    data = _ranking(company, f, "-revenue", None)
    if f.product:
        data = [d for d in data if d["product_id"] == f.product.pk]
    rows = [[d["sku"], d["name"], d["qty"], d["revenue"], d["cost"], d["gross_profit"], d["margin_pct"]] for d in data]
    report = TableReport("Vendas por produto", [
        Column("SKU"), Column("Produto"), Column("Quantidade", "qty"), Column("Faturamento", "brl"),
        Column("CMV", "brl"), Column("Lucro bruto", "brl"), Column("Margem bruta", "pct")], rows,
        totals=["", "Total", sum((d["qty"] for d in data), ZERO), sum((d["revenue"] for d in data), ZERO),
                sum((d["cost"] for d in data), ZERO), sum((d["gross_profit"] for d in data), ZERO), None])
    chart = {"type": "bar", "labels": [d["name"][:28] for d in data[:15]],
             "datasets": [{"label": "Faturamento", "data": [str(d["revenue"]) for d in data[:15]]}]}
    return report, chart


def best_sellers_report(company, f: Filters):
    data = _ranking(company, f, "-qty", 20)
    report = TableReport("Produtos mais vendidos", [
        Column("Posição", "int"), Column("SKU"), Column("Produto"), Column("Quantidade", "qty"),
        Column("Faturamento", "brl")],
        [[i + 1, d["sku"], d["name"], d["qty"], d["revenue"]] for i, d in enumerate(data)])
    chart = {"type": "bar", "indexAxis": "y", "labels": [d["name"][:28] for d in data],
             "datasets": [{"label": "Quantidade", "data": [str(d["qty"]) for d in data]}]}
    return report, chart


def worst_sellers_report(company, f: Filters):
    sold = {d["product_id"]: d for d in _ranking(company, f, "qty", None)}
    products = Product.objects.for_company(company).filter(is_active=True)
    if f.category:
        products = products.filter(category=f.category)
    rows = []
    for p in products:
        d = sold.get(p.pk)
        rows.append([p.sku, p.name, d["qty"] if d else ZERO, d["revenue"] if d else ZERO, p.stock_on_hand,
                     p.stock_value])
    rows.sort(key=lambda r: (r[2], r[3]))
    report = TableReport("Produtos menos vendidos", [
        Column("SKU"), Column("Produto"), Column("Quantidade vendida", "qty"), Column("Faturamento", "brl"),
        Column("Estoque atual", "qty"), Column("Valor parado em estoque", "brl")], rows[:30],
        notes=["Inclui produtos ativos sem nenhuma venda no período (quantidade zero)."])
    return report, None


def _products(company, f: Filters):
    qs = Product.objects.for_company(company).filter(is_active=True).select_related("category")
    if f.category:
        qs = qs.filter(category=f.category)
    if f.product:
        qs = qs.filter(pk=f.product.pk)
    return qs


def stock_report(company, f: Filters):
    products = list(_products(company, f))
    rows = [[p.sku, p.name, p.category.name if p.category else "", p.stock_on_hand, p.stock_reserved,
             p.available_stock, p.min_stock, p.max_stock, p.stock_status_label] for p in products]
    report = TableReport("Relatório de estoque", [
        Column("SKU"), Column("Produto"), Column("Categoria"), Column("Saldo", "qty"), Column("Reservado", "qty"),
        Column("Disponível", "qty"), Column("Mínimo", "qty"), Column("Máximo", "qty"), Column("Situação")], rows)
    counts = {}
    for p in products:
        counts[p.stock_status_label] = counts.get(p.stock_status_label, 0) + 1
    chart = {"type": "doughnut", "labels": list(counts), "datasets": [{"label": "Produtos",
                                                                       "data": list(counts.values())}]}
    return report, chart


def reorder_report(company, f: Filters):
    rows = [r for r in build_rows(company, _products(company, f)) if r.needs_reorder]
    report = TableReport("Produtos abaixo do ponto de pedido", [
        Column("SKU"), Column("Produto"), Column("Consumo médio/dia"), Column("Prazo (dias)", "int"),
        Column("Estoque de segurança", "qty"), Column("Disponível", "qty"), Column("A caminho", "qty"),
        Column("Ponto de pedido", "qty"), Column("Sugestão de compra", "qty")],
        [[r.product.sku, r.product.name, r.daily_consumption, r.lead_time_days, r.safety_stock, r.available,
          r.incoming, r.reorder_point, r.suggested_qty] for r in rows],
        notes=["Ponto de pedido = consumo médio diário × prazo de reposição + estoque de segurança."])
    return report, None


def stock_value_report(company, f: Filters):
    products = _products(company, f)
    by_cat: dict[str, list] = {}
    for p in products:
        key = p.category.name if p.category else "Sem categoria"
        row = by_cat.setdefault(key, [key, ZERO, ZERO, 0])
        row[1] += p.stock_on_hand
        row[2] += p.stock_value
        row[3] += 1
    rows = sorted(by_cat.values(), key=lambda r: -r[2])
    total = sum((r[2] for r in rows), ZERO)
    report = TableReport("Valor financeiro do estoque", [
        Column("Categoria"), Column("Unidades", "qty"), Column("Valor ao custo", "brl"), Column("Produtos", "int"),
        Column("Participação", "pct")],
        [r + [_pct(r[2], total)] for r in rows],
        totals=["Total", sum((r[1] for r in rows), ZERO), total, sum(r[3] for r in rows), None],
        notes=["Valor calculado pelo custo total unitário (aquisição + frete + outros custos)."])
    chart = {"type": "doughnut", "labels": [r[0] for r in rows],
             "datasets": [{"label": "Valor", "data": [str(r[2]) for r in rows]}]}
    return report, chart


def pricing_report(company, f: Filters):
    tax = company.default_tax_rate or ZERO
    rows = []
    for p in _products(company, f):
        price, cost = p.sale_price, p.total_unit_cost
        cm = price - cost - price * tax / HUNDRED
        rows.append([p.sku, p.name, cost, price, markup_percent(price, cost), markup_multiplier(price, cost),
                     _pct(price - cost, price), cm, _pct(cm, price)])
    report = TableReport("Relatório de precificação", [
        Column("SKU"), Column("Produto"), Column("Custo unitário", "brl"), Column("Preço de venda", "brl"),
        Column("Markup", "pct"), Column("Markup multiplicador"), Column("Margem bruta", "pct"),
        Column("MC estimada (R$)", "brl"), Column("MC estimada", "pct")], rows,
        notes=[f"MC estimada desconta somente os impostos padrão da empresa ({tax.normalize():f}%). "
               "Use a calculadora de precificação para incluir comissões, taxas e fretes.",
               "Markup é calculado sobre o custo; margem é calculada sobre o preço."])
    return report, None


def contribution_report(company, f: Filters):
    """MC por produto: rateia as despesas variáveis de cada pedido proporcionalmente ao valor dos itens."""
    items = (SalesOrderItem.objects.filter(order__in=_orders(company, f)).select_related("order", "product"))
    if f.product:
        items = items.filter(product=f.product)
    if f.category:
        items = items.filter(product__category=f.category)
    acc: dict[int, list] = {}
    for item in items:
        order = item.order
        share = safe_div(item.total, order.subtotal) or ZERO
        revenue = item.total + (order.shipping_charged - order.discount) * share
        variable = order.variable_expenses * share
        row = acc.setdefault(item.product_id, [item.product.sku, item.product.name, ZERO, ZERO, ZERO, ZERO])
        row[2] += item.quantity
        row[3] += revenue
        row[4] += item.cost_total
        row[5] += variable
    rows = []
    for sku, name, qty, revenue, cost, var in acc.values():
        cm = revenue - cost - var
        rows.append([sku, name, qty, revenue, cost, var, cm, _pct(cm, revenue), safe_div(cm, qty)])
    rows.sort(key=lambda r: r[6])
    report = TableReport("Margem de contribuição por produto", [
        Column("SKU"), Column("Produto"), Column("Quantidade", "qty"), Column("Receita", "brl"), Column("CMV", "brl"),
        Column("Despesas variáveis", "brl"), Column("Margem de contribuição", "brl"), Column("MC %", "pct"),
        Column("MC por unidade", "brl")], rows,
        totals=["", "Total", sum((r[2] for r in rows), ZERO), sum((r[3] for r in rows), ZERO),
                sum((r[4] for r in rows), ZERO), sum((r[5] for r in rows), ZERO), sum((r[6] for r in rows), ZERO),
                None, None],
        notes=["Ordenado do menor para o maior resultado: produtos no topo com MC negativa estão gerando prejuízo.",
               "Despesas variáveis e frete/desconto do pedido são rateados pelo valor de cada item."])
    return report, None


def breakeven_report(company, f: Filters):
    k = metrics.sales_kpis(company, f.start, f.end)
    fixed_month = finance.monthly_fixed_total(company)
    days = (f.end - f.start).days + 1
    fixed = (fixed_month * Decimal(days) / Decimal(30)).quantize(Decimal("0.01")) if days != 30 else fixed_month
    ratio = safe_div(k["contribution_margin"], k["revenue"])
    be_rev = be.breakeven_revenue(fixed, ratio)
    def pct(v):
        return "—" if v is None else f"{format_number(v, 1)}%"

    rows = [
        ["Faturamento no período", format_brl(k["revenue"])],
        ["Margem de contribuição (R$)", format_brl(k["contribution_margin"])],
        ["Índice de margem de contribuição", pct(None if ratio is None else ratio * HUNDRED)],
        ["Despesas fixas cadastradas (proporcionais ao período)", format_brl(fixed)],
        ["Ponto de equilíbrio em faturamento", format_brl(be_rev)],
        ["Diferença para o ponto de equilíbrio", format_brl(None if be_rev is None else k["revenue"] - be_rev)],
        ["Margem de segurança", pct(None if be_rev is None or not k["revenue"] else
                                    (k["revenue"] - be_rev) / k["revenue"] * HUNDRED)],
    ]
    report = TableReport("Ponto de equilíbrio", [Column("Indicador"), Column("Valor")], rows,
                         notes=["O ponto de equilíbrio depende das premissas: usa o mix de vendas e as margens "
                                "efetivamente realizadas no período e as despesas fixas mensais cadastradas.",
                                "Sem margem de contribuição positiva não há ponto de equilíbrio."])
    return report, None


def expenses_report(company, f: Filters):
    data = finance.expenses_by_category(company, f.start, f.end)
    total = sum((d["total"] for d in data), ZERO)
    report = TableReport("Despesas por categoria", [
        Column("Categoria"), Column("Natureza"), Column("Total", "brl"), Column("Participação", "pct")],
        [[d["category"], d["nature"], d["total"], _pct(d["total"], total)] for d in data],
        totals=["Total", "", total, None], notes=["Por data de competência; lançamentos cancelados não entram."])
    chart = {"type": "doughnut", "labels": [d["category"] for d in data],
             "datasets": [{"label": "Despesas", "data": [str(d["total"]) for d in data]}]}
    return report, chart


def operating_result_report(company, f: Filters):
    k = metrics.sales_kpis(company, f.start, f.end)
    lines = [
        ("(+) Faturamento (pedidos pagos)", k["revenue"]),
        ("(−) Custo dos produtos vendidos (CMV)", -k["cogs"]),
        ("(=) Lucro bruto", k["gross_profit"]),
        ("(−) Despesas variáveis (impostos, comissões, taxas, fretes e outras)", -k["variable_expenses"]),
        ("(=) Margem de contribuição", k["contribution_margin"]),
        ("(−) Despesas fixas", -k["fixed_expenses"]),
        ("(=) Resultado operacional", k["operating_result"]),
    ]
    report = TableReport("Resultado operacional (DRE gerencial simplificada)",
                         [Column("Descrição"), Column("Valor", "brl"), Column("% do faturamento", "pct")],
                         [[label, value, _pct(value, k["revenue"])] for label, value in lines],
                         notes=["Faturamento ≠ lucro: o resultado operacional desconta custos e todas as despesas.",
                                "Compras de mercadorias não são despesa: entram no resultado como CMV quando vendidas."])
    return report, None


def channel_report(company, f: Filters):
    data = metrics.channel_breakdown(company, f.start, f.end)
    report = TableReport("Desempenho por canal de venda", [
        Column("Canal"), Column("Pedidos", "int"), Column("Faturamento", "brl"), Column("Ticket médio", "brl"),
        Column("Margem de contribuição", "brl"), Column("MC %", "pct")],
        [[d["channel"], d["orders"], d["revenue"], d["avg_ticket"], d["contribution_margin"], d["cm_pct"]]
         for d in data])
    chart = {"type": "bar", "labels": [d["channel"] for d in data],
             "datasets": [{"label": "Faturamento", "data": [str(d["revenue"]) for d in data]},
                          {"label": "Margem de contribuição", "data": [str(d["contribution_margin"]) for d in data]}]}
    return report, chart


REPORTS = [
    ReportDef("faturamento", "Faturamento", "Pedidos pagos, ticket médio, CMV e margem por dia.", "Vendas",
              revenue_report, uses_channel=True, uses_category=True, uses_product=True),
    ReportDef("vendas-por-produto", "Vendas por produto", "Quantidade, faturamento e lucro bruto por produto.",
              "Vendas", sales_by_product_report, uses_category=True, uses_channel=True, uses_product=True),
    ReportDef("mais-vendidos", "Produtos mais vendidos", "Top 20 por quantidade.", "Vendas", best_sellers_report,
              uses_category=True, uses_channel=True),
    ReportDef("menos-vendidos", "Produtos menos vendidos", "Inclui produtos sem vendas no período.", "Vendas",
              worst_sellers_report, uses_category=True, uses_channel=True),
    ReportDef("canais", "Desempenho por canal de venda", "Faturamento e margem por canal.", "Vendas",
              channel_report),
    ReportDef("estoque", "Estoque", "Saldo, reservado, disponível e situação.", "Estoque", stock_report,
              uses_period=False, uses_category=True, uses_product=True),
    ReportDef("ponto-de-pedido", "Abaixo do ponto de pedido", "Produtos que precisam ser reabastecidos.",
              "Estoque", reorder_report, uses_period=False, uses_category=True),
    ReportDef("valor-estoque", "Valor financeiro do estoque", "Valor ao custo por categoria.", "Estoque",
              stock_value_report, uses_period=False, uses_category=True),
    ReportDef("precificacao", "Precificação", "Custo, preço, markup e margens por produto.", "Precificação",
              pricing_report, uses_period=False, uses_category=True, uses_product=True),
    ReportDef("margem-contribuicao", "Margem de contribuição por produto",
              "Identifica produtos que geram prejuízo.", "Precificação", contribution_report,
              uses_category=True, uses_channel=True, uses_product=True),
    ReportDef("ponto-de-equilibrio", "Ponto de equilíbrio", "Com base nas vendas do período e despesas fixas.",
              "Financeiro", breakeven_report),
    ReportDef("despesas", "Despesas por categoria", "Distribuição das despesas lançadas.", "Financeiro",
              expenses_report),
    ReportDef("resultado", "Resultado operacional", "DRE gerencial: faturamento até resultado.", "Financeiro",
              operating_result_report),
]
REPORTS_BY_SLUG = {r.slug: r for r in REPORTS}
