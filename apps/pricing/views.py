from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.money import ZERO, money
from apps.core.permissions import company_view
from apps.core.views import TenantDeleteView, TenantListView, get_tenant_object
from apps.finance.services import monthly_fixed_total
from apps.products.models import Product
from apps.products.services import record_price_change
from apps.sales.models import SalesOrder, SalesOrderItem

from . import breakeven as be
from .calculations import PricingError, PricingInput, calculate_pricing, price_curve, simulate_prices
from .forms import ApplyPriceForm, BreakevenForm, MixFormSet, PricingForm
from .models import PricingSimulation


def _initial_from_product(product: Product, company) -> dict:
    return {
        "product": product.pk, "name": f"Precificação {product.sku}",
        "acquisition_cost": product.acquisition_cost, "inbound_freight": product.purchase_freight,
        "other_unit_costs": product.other_costs, "tax_pct": company.default_tax_rate,
        "target_margin_pct": company.default_target_margin,
        "simulate_prices": str(product.sale_price).replace(".", ",") if product.sale_price else "",
    }


def _compute(form) -> dict:
    """Executa os cálculos a partir de um PricingForm válido."""
    data = {f: form.cleaned_data[f] for f in PricingSimulation.INPUT_FIELDS}
    ctx: dict = {}
    try:
        pricing_input = PricingInput(**data)
        result = calculate_pricing(pricing_input)
    except PricingError as exc:
        return {"error": str(exc)}
    ctx["result"] = result
    product = form.cleaned_data.get("product")
    prices = list(form.cleaned_data.get("price_list") or [])
    if product and product.sale_price > 0 and product.sale_price not in prices:
        prices.insert(0, product.sale_price)
    ctx["simulations"] = simulate_prices(pricing_input, prices)
    ctx["current_price"] = product.sale_price if product else None
    if product and product.sale_price > 0:
        ctx["current_vs_suggested"] = product.sale_price - result.suggested_price
    curve = simulate_prices(pricing_input, price_curve(pricing_input, result.suggested_price))
    ctx["chart"] = json.dumps({
        "labels": [str(a.price) for a in curve],
        "cm": [str(a.contribution_margin) for a in curve],
        "cm_pct": [str(a.contribution_margin_pct) for a in curve],
        "minimum": str(result.minimum_price), "suggested": str(result.suggested_price),
    })
    ctx["product"] = product
    return ctx


@company_view("pricing", feature="pricing")
def calculator(request):
    company = request.company
    initial = {"tax_pct": company.default_tax_rate, "target_margin_pct": company.default_target_margin}
    sim_id = request.GET.get("simulacao")
    product_id = request.GET.get("produto")
    instance = None
    if sim_id and sim_id.isdigit():
        instance = get_tenant_object(PricingSimulation, company, pk=int(sim_id))
    elif product_id and product_id.isdigit():
        initial = _initial_from_product(get_tenant_object(Product, company, pk=int(product_id)), company)

    if request.method == "POST":
        form = PricingForm(request.POST, company=company)
        ctx = {"form": form}
        if form.is_valid():
            ctx.update(_compute(form))
            if request.POST.get("action") == "save" and "result" in ctx:
                sim = form.save(commit=False)
                sim.company = company
                sim.created_by = request.user
                sim.name = form.cleaned_data.get("name") or f"Simulação {timezone.localtime():%d/%m/%Y %H:%M}"
                sim.minimum_price = ctx["result"].minimum_price
                sim.suggested_price = ctx["result"].suggested_price
                sim.save()
                log_action("pricing.save", request=request, target=sim,
                           details={"preco_sugerido": str(sim.suggested_price)})
                messages.success(request, "Simulação salva.")
                return redirect(f"{request.path}?simulacao={sim.pk}")
        if request.headers.get("HX-Request"):
            return render(request, "pricing/_results.html", ctx)
        return render(request, "pricing/calculator.html", ctx)

    form = PricingForm(instance=instance, initial=initial if instance is None else None, company=company)
    ctx = {"form": form, "simulation": instance}
    # Simulação salva ou produto escolhido: mostra o resultado já calculado ao abrir a página.
    prefill = None
    if instance is not None:
        prefill = {**{f: getattr(instance, f) for f in PricingSimulation.INPUT_FIELDS},
                   "name": instance.name, "product": instance.product_id or ""}
    elif "product" in initial:
        prefill = {**{f: initial.get(f, 0) for f in PricingSimulation.INPUT_FIELDS}, **initial}
    if prefill is not None:
        bound = PricingForm(prefill, company=company)
        if bound.is_valid():
            ctx.update(_compute(bound))
    return render(request, "pricing/calculator.html", ctx)


@company_view("pricing", manager=True, feature="pricing")
@require_POST
def apply_price(request):
    form = ApplyPriceForm(request.POST, company=request.company)
    if not form.is_valid():
        messages.error(request, "Selecione um produto e informe um preço válido.")
        return redirect(request.POST.get("back") or "pricing:calculator")
    product = form.cleaned_data["product"]
    with transaction.atomic():
        old_price, old_cost = product.sale_price, product.total_unit_cost
        product.sale_price = money(form.cleaned_data["price"])
        product.save(update_fields=["sale_price", "updated_at"])
        record_price_change(product, old_price=old_price, old_cost=old_cost, user=request.user,
                            reason="Aplicado pela calculadora de precificação")
        sim_id = request.POST.get("simulation")
        if sim_id and sim_id.isdigit():
            PricingSimulation.objects.for_company(request.company).filter(pk=int(sim_id)).update(
                applied_at=timezone.now())
        log_action("pricing.apply", request=request, target=product,
                   details={"de": str(old_price), "para": str(product.sale_price)})
    messages.success(request, f"Preço de {product.sku} atualizado para R$ {product.sale_price}.")
    return redirect("products:detail", pk=product.pk)


class SimulationList(TenantListView):
    model = PricingSimulation
    module = "pricing"
    title = "Simulações de preço salvas"
    search_fields = ("name", "product__name", "product__sku")
    template_name = "pricing/simulations.html"

    def get_queryset(self):
        return super().get_queryset().select_related("product", "created_by")


class SimulationDelete(TenantDeleteView):
    model = PricingSimulation
    module = "pricing"
    require_manager = False
    success_url_name = "pricing:simulations"


# ---------------------------------------------------------------------------
# Ponto de equilíbrio
# ---------------------------------------------------------------------------
def _sales_mix(company, days: int = 90) -> list[dict]:
    since = timezone.localdate() - timedelta(days=days - 1)
    rows = (SalesOrderItem.objects.filter(order__company=company, order__status__in=SalesOrder.REVENUE_STATUSES,
                                          order__order_date__gte=since)
            .values("product_id").annotate(qty=Sum("quantity")).order_by("-qty")[:15])
    total = sum((r["qty"] for r in rows), ZERO)
    if total <= 0:
        return []
    return [{"product": r["product_id"], "share_pct": (r["qty"] / total * 100).quantize(Decimal("0.01"))}
            for r in rows]


def _current_monthly_revenue(company) -> Decimal:
    from apps.dashboard.metrics import sales_kpis

    today = timezone.localdate()
    return sales_kpis(company, today - timedelta(days=29), today)["revenue"]


@company_view("pricing", feature="breakeven")
def breakeven_view(request):
    company = request.company
    fixed_default = monthly_fixed_total(company)
    data = request.POST if request.method == "POST" else None
    form = BreakevenForm(data, company=company, initial={
        "fixed_costs": fixed_default, "variable_pct": company.default_tax_rate, "per_unit_costs": ZERO,
        "current_revenue": _current_monthly_revenue(company), "mode": request.GET.get("modo", "single"),
    })
    mix_initial = _sales_mix(company) if request.GET.get("mix") == "vendas" else []
    formset = MixFormSet(data, prefix="mix", form_kwargs={"company": company},
                         initial=mix_initial or [{} for _ in range(3)])
    ctx = {"form": form, "formset": formset, "fixed_default": fixed_default, "mix_from_sales": bool(mix_initial)}

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        cd = form.cleaned_data
        var_pct = cd.get("variable_pct") or ZERO
        per_unit = cd.get("per_unit_costs") or ZERO

        def variable_cost(price, unit_cost):
            return unit_cost + per_unit + price * var_pct / Decimal("100")

        items = []
        if cd["mode"] == "single":
            product = cd.get("product")
            price = cd.get("manual_price") or (product.sale_price if product else ZERO)
            unit_cost = cd.get("manual_unit_cost")
            if unit_cost is None:
                unit_cost = product.total_unit_cost if product else ZERO
            name = product.name if product else "Produto (manual)"
            items.append(be.MixItem(name, price, variable_cost(price, unit_cost), Decimal("100")))
        else:
            for row in formset.cleaned_data:
                product = row.get("product")
                if not product or not row.get("share_pct"):
                    continue
                price = row.get("price") or product.sale_price
                items.append(be.MixItem(product.name, price, variable_cost(price, product.total_unit_cost),
                                        row["share_pct"]))
        try:
            result = be.compute(cd["fixed_costs"], items, cd.get("current_revenue"))
        except be.BreakevenError as exc:
            ctx["error"] = str(exc)
        else:
            ctx["result"] = result
            ctx["scenarios"] = be.scenarios(result, fixed_increase_pct=cd.get("fixed_increase_pct"),
                                            cm_improvement_pp=cd.get("cm_improvement_pp"))
            target = cd.get("target_profit")
            if target is not None:
                ctx["target"] = {"profit": target,
                                 "units": be.units_for_target(result.fixed_costs, result.unit_cm, target),
                                 "revenue": be.revenue_for_target(result.fixed_costs, result.cm_ratio, target)}
            ctx["chart"] = json.dumps(be.chart_series(result))
    return render(request, "pricing/breakeven.html", ctx)
