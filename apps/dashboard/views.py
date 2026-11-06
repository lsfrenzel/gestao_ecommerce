from __future__ import annotations

import json

from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.money import ZERO
from apps.core.periods import PERIOD_CHOICES, resolve_period
from apps.core.permissions import company_view
from apps.customers.models import Customer
from apps.inventory.reorder import build_rows
from apps.products.models import Product
from apps.purchasing.models import Supplier
from apps.sales.models import SalesOrder

from . import metrics


MODULE_HOME = {
    "products": "products:list", "pricing": "pricing:calculator", "inventory": "inventory:overview",
    "reorder": "inventory:reorder", "suppliers": "purchasing:suppliers", "purchasing": "purchasing:orders",
    "sales": "sales:list", "customers": "customers:list", "finance": "finance:overview",
    "marketing": "marketing:list", "research": "research:trends", "reports": "reports:index", "settings": "companies:settings",
}


@company_view()
def home(request):
    company = request.company
    if not request.membership.can("dashboard"):
        # Página inicial (login, logotipo) de quem não tem o dashboard: primeiro módulo liberado.
        for module in request.membership.allowed_modules():
            if module in MODULE_HOME:
                return redirect(MODULE_HOME[module])
        return render(request, "errors/403.html", {"reason": "Nenhum módulo liberado para o seu usuário. "
                                                             "Fale com o administrador da empresa."}, status=403)
    period = resolve_period(request.GET, default="30d")
    kpis = metrics.sales_kpis(company, period.start, period.end)
    previous = period.previous()
    prev = metrics.sales_kpis(company, previous.start, previous.end)

    def delta(key):
        before, now = prev.get(key) or ZERO, kpis.get(key) or ZERO
        if not before:
            return None
        return (now - before) / abs(before) * 100

    stock = metrics.stock_kpis(company)
    rows = build_rows(company, Product.objects.for_company(company).filter(is_active=True))
    near_reorder = [r for r in rows if r.near_reorder or r.needs_reorder]
    ranking = metrics.product_ranking(company, period.start, period.end, limit=10)
    top_qty = metrics.product_ranking(company, period.start, period.end, limit=8, order_by="-qty")
    evolution = metrics.monthly_evolution(company, months=6)
    charts = {
        "revenue": metrics.revenue_series(company, period),
        "by_product": {"labels": [r["name"][:26] for r in ranking], "values": [str(r["revenue"]) for r in ranking]},
        "top_qty": {"labels": [r["name"][:26] for r in top_qty], "values": [str(r["qty"]) for r in top_qty]},
        "evolution": evolution,
        "stock": {"labels": ["Normal", "Estoque baixo", "Esgotado", "Excesso"],
                  "values": [stock["status"]["ok"], stock["status"]["low"], stock["status"]["out"],
                             stock["status"]["excess"]]},
    }
    empty = not Product.objects.for_company(company).exists()
    return render(request, "dashboard/home.html", {
        "period": period, "period_choices": PERIOD_CHOICES, "kpis": kpis,
        "deltas": {k: delta(k) for k in ("revenue", "orders", "avg_ticket", "contribution_margin",
                                         "operating_result")},
        "stock": stock, "low_stock": stock["status"]["low"] + stock["status"]["out"],
        "near_reorder": near_reorder[:6], "near_reorder_count": len(near_reorder),
        "charts": json.dumps(charts), "goal": metrics.goal_progress(company, timezone.localdate()),
        "recent_orders": SalesOrder.objects.for_company(company).select_related("customer")[:6],
        "empty": empty,
    })


@company_view()
def search(request):
    """Busca global (somente dados da empresa ativa e módulos permitidos)."""
    company = request.company
    term = request.GET.get("q", "").strip()
    membership = request.membership
    results = {}
    if len(term) >= 2:
        if membership.can("products"):
            results["Produtos"] = [
                (str(p), f"/produtos/{p.pk}/") for p in Product.objects.for_company(company)
                .filter(Q(name__icontains=term) | Q(sku__icontains=term) | Q(barcode__icontains=term))[:8]]
        if membership.can("customers"):
            results["Clientes"] = [
                (c.name, f"/clientes/{c.pk}/") for c in Customer.objects.for_company(company)
                .filter(Q(name__icontains=term) | Q(email__icontains=term))[:8]]
        if membership.can("sales"):
            cond = Q(external_reference__icontains=term) | Q(customer__name__icontains=term)
            if term.isdigit():
                cond |= Q(number=int(term))
            results["Pedidos de venda"] = [
                (f"{o} — {o.customer or 'sem cliente'}", f"/vendas/{o.pk}/")
                for o in SalesOrder.objects.for_company(company).filter(cond).select_related("customer")[:8]]
        if membership.can("suppliers"):
            results["Fornecedores"] = [
                (s.name, f"/compras/fornecedores/{s.pk}/") for s in Supplier.objects.for_company(company)
                .filter(Q(name__icontains=term) | Q(cnpj__icontains=term))[:8]]
    results = {k: v for k, v in results.items() if v}
    template = "dashboard/_search_results.html" if request.headers.get("HX-Request") else "dashboard/search.html"
    return render(request, template, {"q": term, "results": results})
