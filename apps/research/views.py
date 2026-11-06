from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime

from apps.core import ratelimit
from apps.core.audit import log_action
from apps.core.permissions import company_view
from apps.core.views import TenantDeleteView, get_tenant_object

from . import trends
from .analysis import DIRECTIONS, all_stats, keyword_stats
from .forms import OpportunityForm, TrendSearchForm
from .models import OpportunitySimulation, TrendSearch
from .opportunity import OpportunityError, simulate
from .services import build_input

SERIES_COLORS = ["#ff3131", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed"]


@company_view("research", feature="marketing")
def trend_search(request):
    company = request.company
    form = TrendSearchForm(request.GET or None, initial={"geo": "BR", "periodo": "today 12-m"})
    ctx = {"form": form, "provider": trends.provider_name()}
    if request.GET.get("q") and form.is_valid():
        keywords = trends.clean_keywords(form.cleaned_data["q"])
        geo = form.cleaned_data.get("geo") or "BR"
        timeframe = form.cleaned_data.get("periodo") or "today 12-m"
        scope, ident = "trends", str(company.pk)
        limited = ratelimit.is_limited(scope, ident, settings.TRENDS_SEARCHES_PER_HOUR)
        try:
            result, from_cache = trends.interest(keywords, geo, timeframe, allow_fetch=not limited)
        except trends.TrendsError as exc:
            ctx["error"] = str(exc)
        else:
            if not from_cache:
                ratelimit.hit(scope, ident, 3600)
            last = TrendSearch.objects.for_company(company).first()
            if not last or (last.keywords, last.geo, last.timeframe) != (", ".join(keywords), geo, timeframe):
                TrendSearch.objects.create(company=company, keywords=", ".join(keywords), geo=geo,
                                           timeframe=timeframe, created_by=request.user)
            stats = all_stats(result)
            ctx.update(result=result, stats=stats, from_cache=from_cache,
                       fetched_at=parse_datetime(result.fetched_at),
                       geo_label=dict(trends.REGIONS).get(geo, geo),
                       timeframe_label=dict(trends.TIMEFRAMES).get(timeframe, timeframe),
                       best=max(stats, key=lambda s: s.score) if stats else None,
                       regions=sorted(result.regions, key=lambda r: -max(r["values"] or [0]))[:12],
                       chart=({
                           "labels": [p["label"] for p in result.timeline],
                           "datasets": [{"label": k, "data": [p["values"][i] if i < len(p["values"]) else None
                                                               for p in result.timeline],
                                         "color": SERIES_COLORS[i % len(SERIES_COLORS)]}
                                        for i, k in enumerate(result.keywords)],
                       }))
    else:
        ctx["trending"] = trends.trending_now("BR", 15)
    ctx["history"] = TrendSearch.objects.for_company(company)[:8]
    return render(request, "research/trends.html", ctx)


def _initial_from_query(request) -> dict:
    initial = {}
    product = (request.GET.get("produto") or "").strip()[:160]
    if product:
        initial.update(name=product, keyword=product[:120])
    score = request.GET.get("indice", "")
    if score.isdigit() and 0 <= int(score) <= 100:
        initial["trend_score"] = int(score)
    if request.GET.get("direcao") in DIRECTIONS:
        initial["trend_direction"] = request.GET["direcao"]
    return initial


def _trend_for(keyword: str) -> tuple[int | None, str]:
    """Busca automática da tendência ao salvar uma simulação sem pesquisa prévia (falhas são ignoradas)."""
    try:
        result, _ = trends.interest([keyword])
    except trends.TrendsError:
        return None, ""
    stats = keyword_stats(result.keywords[0], result.timeline, 0)
    return stats.score, stats.direction


@company_view("research", feature="marketing")
def opportunity(request):
    company = request.company
    simulation = None
    sim_id = request.GET.get("simulacao", "")
    if sim_id.isdigit():
        simulation = get_tenant_object(OpportunitySimulation, company, pk=int(sim_id))
        initial = dict(simulation.inputs)
    else:
        initial = _initial_from_query(request)

    defaults = {"origin": "national", "channel": "mercado_livre", "quantity": 100, "target_margin_pct": "30",
                "regime": "simplified"}
    if request.method == "POST":
        form = OpportunityForm(request.POST)
    elif simulation:
        form = OpportunityForm(data={**defaults, **initial})  # reabre já calculada
    else:
        form = OpportunityForm(initial={**defaults, **initial})
    ctx = {"form": form, "simulation": simulation}
    score = str(form["trend_score"].value() or "")
    if score.isdigit():
        ctx["trend"] = {"score": int(score), "label": DIRECTIONS.get(form["trend_direction"].value() or "", "")}

    if form.is_bound and form.is_valid():
        cleaned = form.cleaned_data
        try:
            data, assumptions = build_input(cleaned, company)
            result = simulate(data, cleaned.get("trend_direction") or "")
        except OpportunityError as exc:
            ctx["calc_error"] = str(exc)
        else:
            ctx.update(result=result, assumptions=assumptions, data=data)
            if request.method == "POST" and request.POST.get("action") == "save":
                score, direction = cleaned.get("trend_score"), cleaned.get("trend_direction") or ""
                if score is None:
                    score, direction = _trend_for(cleaned.get("keyword") or cleaned["name"])
                    if direction:
                        result = simulate(data, direction)
                sim = OpportunitySimulation.objects.create(
                    company=company, name=cleaned["name"], keyword=cleaned.get("keyword") or "",
                    origin=cleaned["origin"], channel=cleaned.get("channel") or "",
                    sale_price=result.sale_price, landed_cost=result.landed_cost,
                    margin_pct=result.net_margin_pct, target_margin_pct=result.target_margin_pct,
                    minimum_price=result.minimum_price, is_viable=result.is_viable, trend_score=score,
                    trend_direction=direction,
                    inputs={**form.serializable(), "trend_score": score, "trend_direction": direction},
                    results=result.as_dict(), created_by=request.user)
                log_action("opportunity.create", request=request, target=sim,
                           details={"produto": sim.name, "margem": str(sim.margin_pct), "viavel": sim.is_viable})
                messages.success(request, f"Simulação “{sim.name}” salva: {result.verdict_label.lower()}.")
                return redirect(f"{request.path}?simulacao={sim.pk}")

    if request.headers.get("HX-Request"):
        return render(request, "research/_opportunity_results.html", ctx)
    return render(request, "research/opportunity.html", ctx)


@company_view("research", feature="marketing")
def simulations(request):
    company = request.company
    qs = OpportunitySimulation.objects.for_company(company).select_related("created_by")
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(name__icontains=term)
    status = request.GET.get("situacao", "")
    if status == "viaveis":
        qs = qs.filter(is_viable=True)
    elif status == "reprovadas":
        qs = qs.filter(is_viable=False)
    origin = request.GET.get("origem", "")
    if origin in OpportunitySimulation.Origin.values:
        qs = qs.filter(origin=origin)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "research/simulations.html", {
        "page_obj": page, "q": term, "status": status, "origin": origin,
        "origins": OpportunitySimulation.Origin.choices, "directions": DIRECTIONS})


class OpportunityDelete(TenantDeleteView):
    model = OpportunitySimulation
    module = "research"
    success_url_name = "research:simulations"
    audit_action = "opportunity"
