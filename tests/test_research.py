"""Pesquisa de mercado: indicadores do Google Trends, simulação de oportunidade, views e isolamento."""

from datetime import date, timedelta
from decimal import Decimal as D
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Membership
from apps.marketing.models import Campaign
from apps.research import fx, trends
from apps.research.analysis import keyword_stats
from apps.research.models import CachedLookup, OpportunitySimulation, TrendSearch
from apps.research.opportunity import (
    FORMAL,
    INTERNATIONAL,
    NATIONAL,
    SIMPLIFIED,
    OpportunityError,
    OpportunityInput,
    landed_cost,
    max_unit_cost,
    minimum_price,
    net_margin,
    simulate,
)
from apps.research.services import build_input, marketing_share

from .factories import CompanyTestCase, add_member, make_company


def ml_input(**extra) -> OpportunityInput:
    """Venda no Mercado Livre: 6% imposto, 14% comissão, 10% marketing, 2% devoluções = 32% do preço."""
    base = dict(origin=NATIONAL, unit_cost=D("40"), sale_price=D("129.90"), quantity=100,
                target_margin_pct=D("30"), tax_pct=D("6"), commission_pct=D("14"), marketing_pct=D("10"),
                returns_pct=D("2"), fixed_fee=D("6.50"), fixed_fee_below=D("79"), packaging=D("2.50"),
                shipping=D("22"), shipping_from=D("79"), inbound_freight_pct=D("5"))
    base.update(extra)
    return OpportunityInput(**base)


def import_input(regime=SIMPLIFIED, **extra) -> OpportunityInput:
    base = dict(origin=INTERNATIONAL, unit_cost=D("10"), sale_price=D("300"), quantity=100, exchange_rate=D("5"),
                intl_freight_usd=D("1.50"), regime=regime, tax_pct=D("6"), commission_pct=D("14"),
                marketing_pct=D("10"), returns_pct=D("2"), packaging=D("2.50"), shipping=D("22"),
                shipping_from=D("79"), fixed_fee=D("6.50"), fixed_fee_below=D("79"))
    if regime == SIMPLIFIED:
        base.update(fx_fee_pct=D("4"), icms_pct=D("20"), customs_fees=D("15"))
    else:
        base.update(insurance_pct=D("1"), fx_fee_pct=D("1.5"), ii_pct=D("20"), ipi_pct=D("10"), pis_pct=D("2.10"),
                    cofins_pct=D("9.65"), icms_pct=D("18"), customs_fees=D("1800"), domestic_freight_pct=D("3"))
    base.update(extra)
    return OpportunityInput(**base)


class NationalOpportunityTests(TestCase):
    def test_landed_cost_adds_inbound_freight(self):
        self.assertEqual(landed_cost(ml_input()).unit, D("42.00"))

    def test_margin_below_30_is_rejected(self):
        # 129,90 − 42 − 32% × 129,90 (41,568) − frete 22 − embalagem 2,50 = 21,832 → 16,81%
        result = simulate(ml_input())
        self.assertEqual(result.net_margin, D("21.83"))
        self.assertEqual(result.net_margin_pct, D("16.81"))
        self.assertFalse(result.is_viable)
        self.assertEqual(result.verdict, "rejected")

    def test_minimum_price_respects_price_bands(self):
        # Abaixo de R$ 79 a resposta seria 134,21 (fora da faixa); acima: (42 + 24,50) / 0,38 = 175,00
        data = ml_input()
        price = minimum_price(data, D("42"), D("30"))
        self.assertEqual(price, D("175.00"))
        self.assertGreaterEqual(net_margin(data, price, D("42")), price * D("0.30"))

    def test_minimum_price_below_band_when_cheap_product(self):
        # Custo 10 → (10,50 + 6,50 + 2,50) / 0,38 = 51,32 (< 79: tarifa fixa, sem frete subsidiado)
        data = ml_input(unit_cost=D("10"))
        self.assertEqual(minimum_price(data, D("10.50"), D("30")), D("51.32"))

    def test_max_unit_cost_gives_exactly_target_margin(self):
        data = ml_input()
        cost = max_unit_cost(data, D("129.90"))
        self.assertEqual(cost, D("23.67"))  # (129,90 × 0,38 − 24,50) / 1,05
        viable = simulate(ml_input(unit_cost=cost))
        self.assertTrue(viable.is_viable)
        self.assertFalse(simulate(ml_input(unit_cost=cost + D("0.10"))).is_viable)

    def test_approved_and_attention_verdicts(self):
        self.assertEqual(simulate(ml_input(sale_price=D("199.90"))).verdict, "approved")
        self.assertEqual(simulate(ml_input(sale_price=D("199.90")), "queda").verdict, "attention")

    def test_without_price_only_minimum_is_shown(self):
        result = simulate(ml_input(sale_price=None))
        self.assertEqual(result.verdict, "no_price")
        self.assertIsNone(result.net_margin_pct)
        self.assertEqual(result.minimum_price, D("175.00"))

    def test_lot_figures(self):
        result = simulate(ml_input(sale_price=D("199.90"), quantity=50))
        self.assertEqual(result.lot_investment, D("2100.00"))
        self.assertEqual(result.lot_revenue, D("9995.00"))
        self.assertEqual(result.lot_profit, D("3471.60"))  # 69,432 × 50 (sem arredondar a unidade antes)

    def test_impossible_combination_raises(self):
        with self.assertRaises(OpportunityError):
            ml_input(marketing_pct=D("50"))  # 70% + 30% de margem = 100%
        with self.assertRaises(OpportunityError):
            ml_input(unit_cost=D("0"))


class ImportOpportunityTests(TestCase):
    def test_simplified_regime_above_50_dollars(self):
        # cv = 1.000 + 150 = US$ 1.150 → II = 60% − 20 = US$ 670 (R$ 3.350); ICMS = (5.750 + 3.350) × 0,2/0,8
        landed = landed_cost(import_input())
        self.assertEqual(landed.regime_used, SIMPLIFIED)
        self.assertEqual(landed.customs_value_usd, D("1150"))
        lines = dict(landed.lines)
        self.assertEqual(lines["Imposto de Importação (II)"], D("33.5"))
        self.assertEqual(lines["ICMS de importação"], D("22.75"))
        self.assertEqual(landed.unit, D("116.2"))

    def test_simplified_regime_low_band_is_20_percent(self):
        landed = landed_cost(import_input(quantity=2))  # cv = US$ 23 → II 20% = US$ 4,60
        self.assertEqual(dict(landed.lines)["Imposto de Importação (II)"], D("4.6") * 5 / 2)

    def test_simplified_over_3000_switches_to_formal(self):
        landed = landed_cost(import_input(quantity=300))  # US$ 3.450
        self.assertEqual(landed.regime_used, FORMAL)
        self.assertTrue(landed.warnings)

    def test_formal_import_cascade(self):
        landed = landed_cost(import_input(regime=FORMAL))
        self.assertEqual(landed.unit.quantize(D("0.01")), D("126.37"))
        self.assertIn("IPI", dict(landed.lines))

    def test_max_fob_for_target_margin(self):
        data = import_input()
        max_fob = max_unit_cost(data, D("300"))
        self.assertTrue(simulate(import_input(unit_cost=max_fob)).is_viable)
        self.assertFalse(simulate(import_input(unit_cost=max_fob + D("0.05"))).is_viable)

    def test_missing_exchange_rate(self):
        with self.assertRaises(OpportunityError):
            import_input(exchange_rate=D("0"))


class TrendStatsTests(TestCase):
    def timeline(self, values):
        start = date(2025, 11, 2)
        return [{"date": (start + timedelta(weeks=i)).isoformat(), "label": f"s{i}", "values": [v],
                 "partial": False} for i, v in enumerate(values)]

    def test_rising_series(self):
        stats = keyword_stats("tênis", self.timeline([20] * 6 + [40, 40, 40, 60, 60, 60]), 0)
        self.assertEqual(stats.direction, "alta")
        self.assertEqual(stats.growth_pct, D("50.0"))  # último quarto 60 vs anterior 40
        self.assertEqual(stats.peak, 60)
        self.assertEqual(stats.score, 68)  # (60 + (50 + 50/2)) / 2 = 67,5 → 68
        self.assertTrue(0 <= stats.score <= 100)

    def test_falling_series_and_partial_point_ignored(self):
        tl = self.timeline([80, 80, 80, 80, 80, 80, 40, 40])
        tl.append({"date": "2026-01-04", "label": "parcial", "values": [100], "partial": True})
        stats = keyword_stats("x", tl, 0)
        self.assertEqual(stats.direction, "queda")
        self.assertEqual(stats.peak, 80)

    def test_seasonal_peak_needs_a_year(self):
        weekly = [10] * 52
        weekly[6] = 100  # 2025-12-14
        stats = keyword_stats("x", self.timeline(weekly), 0)
        self.assertEqual(stats.seasonal_peak, "dezembro")
        self.assertEqual(keyword_stats("x", self.timeline([1, 2, 3]), 0).seasonal_peak, "")


TIMELINE_JSON = {"default": {"timelineData": [
    {"time": "1735689600", "formattedTime": "1 de jan.", "value": [40, 10], "isPartial": False},
    {"time": "1736294400", "formattedTime": "8 de jan.", "value": [60, 20]},
    {"time": "1736899200", "formattedTime": "15 de jan.", "value": [80, 15], "isPartial": True},
]}}
EXPLORE_JSON = {"widgets": [
    {"id": "TIMESERIES", "token": "t1", "request": {"a": 1}},
    {"id": "GEO_MAP", "token": "t2", "request": {"b": 1}},
    {"id": "RELATED_QUERIES_0", "token": "t3", "request": {"restriction": {"complexKeywordsRestriction": {
        "keyword": [{"value": "camiseta"}]}}}},
]}


def fake_google(url):
    import json

    if "/api/explore" in url:
        return ")]}'\n" + json.dumps(EXPLORE_JSON)
    if "multiline" in url:
        return ")]}',\n" + json.dumps(TIMELINE_JSON)
    if "comparedgeo" in url:
        return ")]}',\n" + json.dumps({"default": {"geoMapData": [
            {"geoCode": "BR-SP", "geoName": "São Paulo", "value": [100, 30], "hasData": [True, True]}]}})
    if "relatedsearches" in url:
        return ")]}',\n" + json.dumps({"default": {"rankedList": [
            {"rankedKeyword": [{"query": "camiseta oversized", "formattedValue": "100"}]},
            {"rankedKeyword": [{"query": "camiseta dry fit", "formattedValue": "+250%"}]}]}})
    return "<html></html>"


@override_settings(SERPAPI_API_KEY="")
class GoogleTrendsClientTests(TestCase):
    def test_parses_widgets_and_caches(self):
        with mock.patch.object(trends, "_read", side_effect=lambda opener, url, timeout=20: fake_google(url)) as read:
            result, cached = trends.interest(["camiseta", "moletom"], "BR", "today 12-m")
            calls = read.call_count
            again, cached_again = trends.interest(["Camiseta", "moletom "], "BR", "today 12-m")
        self.assertFalse(cached)
        self.assertTrue(cached_again)
        self.assertEqual(read.call_count, calls)  # segunda consulta veio do cache
        self.assertEqual(result.provider, "google")
        self.assertEqual([p["values"] for p in result.timeline], [[40, 10], [60, 20], [80, 15]])
        self.assertTrue(result.timeline[-1]["partial"])
        self.assertEqual(result.regions[0]["code"], "BR-SP")
        self.assertEqual(result.related["camiseta"]["rising"][0]["value"], "+250%")
        self.assertEqual(again.keywords, result.keywords)

    def test_rate_limited_message(self):
        with mock.patch.object(trends, "_read", side_effect=trends.TrendsRateLimited("limite")):
            with self.assertRaises(trends.TrendsRateLimited):
                trends.interest(["x"])

    def test_allow_fetch_false_uses_only_cache(self):
        with self.assertRaises(trends.TrendsRateLimited):
            trends.interest(["algo novo"], allow_fetch=False)

    def test_keywords_are_cleaned_and_limited(self):
        self.assertEqual(trends.clean_keywords(" a , A, b;c,d,e,f , "), ["a", "b", "c", "d", "e"])
        with self.assertRaises(trends.TrendsError):
            trends.interest([" "])
        with self.assertRaises(trends.TrendsError):
            trends.interest(["x"], geo="US")

    def test_trending_rss(self):
        rss = """<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss"><channel>
          <item><title>Copa do Brasil</title><ht:approx_traffic>50 mil+</ht:approx_traffic>
          <ht:news_item><ht:news_item_title>Final hoje</ht:news_item_title>
          <ht:news_item_url>https://g1.globo.com/x</ht:news_item_url></ht:news_item></item>
          <item><title>Bad</title><ht:news_item><ht:news_item_url>javascript:alert(1)</ht:news_item_url></ht:news_item></item>
        </channel></rss>"""
        items = trends.parse_trending_rss(rss)
        self.assertEqual(items[0], {"query": "Copa do Brasil", "traffic": "50 mil+", "news": "Final hoje",
                                    "url": "https://g1.globo.com/x"})
        self.assertEqual(items[1]["url"], "")
        self.assertEqual(trends.parse_trending_rss("não é xml"), [])


@override_settings(SERPAPI_API_KEY="chave-de-teste")
class SerpApiClientTests(TestCase):
    def test_serpapi_normalization(self):
        def fake(params):
            if params["data_type"] == "TIMESERIES":
                return {"interest_over_time": {"timeline_data": [
                    {"date": "jan", "timestamp": "1735689600",
                     "values": [{"query": "tênis", "extracted_value": 55}]}]}}
            if params["data_type"] == "RELATED_QUERIES":
                return {"related_queries": {"rising": [{"query": "tênis branco", "value": "+90%"}], "top": []}}
            return {"interest_by_region": [{"geo": "BR-RJ", "location": "Rio de Janeiro", "extracted_value": 100}]}
        with mock.patch.object(trends, "_serpapi", side_effect=fake):
            result, _ = trends.interest(["tênis"])
        self.assertEqual(result.provider, "serpapi")
        self.assertEqual(result.timeline[0]["values"], [55])
        self.assertEqual(result.related["tênis"]["rising"][0]["query"], "tênis branco")
        self.assertEqual(result.regions[0]["name"], "Rio de Janeiro")


class FxTests(TestCase):
    def test_uses_cache_and_falls_back(self):
        with mock.patch.object(fx, "_awesome", side_effect=OSError("off")), \
                mock.patch.object(fx, "_ptax", return_value=fx.Quote(D("5.4321"), "BCB", "hoje")):
            quote = fx.usd_brl()
        self.assertEqual(quote.rate, D("5.4321"))
        with mock.patch.object(fx, "_awesome", side_effect=AssertionError("não deveria chamar")):
            self.assertEqual(fx.usd_brl().rate, D("5.4321"))

    def test_none_when_all_sources_fail(self):
        with mock.patch.object(fx, "_awesome", side_effect=OSError), mock.patch.object(fx, "_ptax", side_effect=OSError):
            self.assertIsNone(fx.usd_brl())


class AutoAssumptionTests(CompanyTestCase):
    def cleaned(self, **extra):
        data = {"origin": NATIONAL, "channel": "mercado_livre", "unit_cost_brl": D("40"), "unit_cost_usd": None,
                "sale_price": D("129.90"), "quantity": 100, "target_margin_pct": D("30"), "regime": ""}
        data.update(extra)
        return data

    def test_company_tax_and_campaign_history_are_used(self):
        self.company.default_tax_rate = D("8")
        self.company.save()
        today = timezone.localdate()
        Campaign.objects.create(company=self.company, name="C", channel="meta_ads", investment=D("1000"),
                                attributed_revenue=D("8000"), start_date=today - timedelta(days=30), end_date=today)
        data, used = build_input(self.cleaned(), self.company)
        self.assertEqual(data.tax_pct, D("8"))
        self.assertEqual(data.marketing_pct, D("12.5"))
        self.assertEqual(data.commission_pct, D("14"))
        sources = {u["field"]: u["source"] for u in used}
        self.assertIn("empresa", sources["tax_pct"])
        self.assertIn("campanhas", sources["marketing_pct"])

    def test_informed_values_override_estimates(self):
        data, used = build_input(self.cleaned(commission_pct=D("11"), marketing_pct=D("5")), self.company)
        self.assertEqual(data.commission_pct, D("11"))
        self.assertFalse(next(u for u in used if u["field"] == "commission_pct")["auto"])
        self.assertEqual(marketing_share(self.company)[0], D("10"))  # sem campanhas: padrão

    def test_international_uses_automatic_exchange_rate(self):
        with mock.patch.object(fx, "usd_brl", return_value=fx.Quote(D("5"), "Teste", "agora")):
            data, used = build_input(self.cleaned(origin=INTERNATIONAL, unit_cost_usd=D("10"), regime=SIMPLIFIED),
                                     self.company)
        self.assertEqual(data.exchange_rate, D("5"))
        self.assertEqual(data.intl_freight_usd, D("1.50"))
        self.assertEqual(data.icms_pct, D("20"))


class ResearchViewTests(CompanyTestCase):
    def test_menu_and_pages(self):
        with mock.patch.object(trends, "trending_now", return_value=[{"query": "Copa", "traffic": "10 mil+",
                                                                       "news": "", "url": ""}]):
            response = self.client.get(reverse("research:trends"))
        self.assertContains(response, "Em alta agora")
        self.assertContains(response, reverse("research:trends"))  # item “Pesquisa” no menu
        self.assertContains(response, "Copa")
        self.assertEqual(self.client.get(reverse("research:opportunity")).status_code, 200)
        self.assertEqual(self.client.get(reverse("research:simulations")).status_code, 200)

    @override_settings(SERPAPI_API_KEY="")
    def test_search_renders_stats_and_records_history(self):
        with mock.patch.object(trends, "_read", side_effect=lambda opener, url, timeout=20: fake_google(url)):
            response = self.client.get(reverse("research:trends"), {"q": "camiseta, moletom", "geo": "BR",
                                                                    "periodo": "today 12-m"})
        self.assertContains(response, "Simular oportunidade")
        self.assertContains(response, "camiseta dry fit")
        self.assertContains(response, "São Paulo")
        self.assertEqual(TrendSearch.objects.for_company(self.company).count(), 1)

    def test_search_error_is_friendly(self):
        with mock.patch.object(trends, "interest", side_effect=trends.TrendsRateLimited("Google limitou")):
            response = self.client.get(reverse("research:trends"), {"q": "x"})
        self.assertContains(response, "Google limitou")

    def test_live_calculation_and_save(self):
        payload = {"name": "Camiseta oversized", "origin": "national", "channel": "mercado_livre",
                   "unit_cost_brl": "40,00", "sale_price": "199,90", "quantity": "100", "target_margin_pct": "30",
                   "trend_score": "72", "trend_direction": "alta", "keyword": "camiseta oversized"}
        partial = self.client.post(reverse("research:opportunity"), payload, HTTP_HX_REQUEST="true")
        self.assertContains(partial, "Oportunidade aprovada")
        self.assertNotContains(partial, "<html")
        saved = self.client.post(reverse("research:opportunity"), {**payload, "action": "save"})
        sim = OpportunitySimulation.objects.get(company=self.company)
        self.assertRedirects(saved, reverse("research:opportunity") + f"?simulacao={sim.pk}")
        self.assertTrue(sim.is_viable)
        self.assertEqual(sim.trend_score, 72)
        self.assertGreaterEqual(sim.margin_pct, D("30"))
        reopened = self.client.get(reverse("research:opportunity"), {"simulacao": sim.pk})
        self.assertContains(reopened, "Oportunidade aprovada")
        self.assertContains(self.client.get(reverse("research:simulations")), "Camiseta oversized")

    def test_save_without_research_fetches_trend_automatically(self):
        stats_result = trends.TrendResult(keywords=["boné"], geo="BR", timeframe="today 12-m", provider="google",
                                          fetched_at=timezone.now().isoformat(), timeline=[
                                              {"date": "2026-01-04", "label": "a", "values": [80], "partial": False},
                                              {"date": "2026-01-11", "label": "b", "values": [40], "partial": False}])
        with mock.patch.object(trends, "interest", return_value=(stats_result, False)):
            self.client.post(reverse("research:opportunity"), {
                "name": "Boné", "origin": "national", "channel": "shopee", "unit_cost_brl": "15",
                "sale_price": "79,90", "quantity": "50", "target_margin_pct": "30", "action": "save"})
        sim = OpportunitySimulation.objects.get(company=self.company)
        self.assertEqual(sim.trend_direction, "queda")
        self.assertIsNotNone(sim.trend_score)

    def test_margin_below_30_not_allowed(self):
        response = self.client.post(reverse("research:opportunity"), {
            "name": "X", "origin": "national", "channel": "own_store", "unit_cost_brl": "10", "quantity": "1",
            "target_margin_pct": "20"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "30")
        self.assertContains(response, "Corrija os campos")

    def test_international_requires_usd_cost(self):
        response = self.client.post(reverse("research:opportunity"), {
            "name": "X", "origin": "international", "channel": "own_store", "quantity": "1",
            "target_margin_pct": "30"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "dólares")

    def test_operator_without_module_is_blocked_and_manager_has_access(self):
        operator = add_member(self.company)
        self.client.force_login(operator)
        self.assertEqual(self.client.get(reverse("research:opportunity")).status_code, 403)
        manager = add_member(self.company, role=Membership.Role.MANAGER)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("research:opportunity")).status_code, 200)


class ResearchIsolationTests(CompanyTestCase):
    def test_other_company_simulation_is_404(self):
        other = make_company(name="Outra")
        sim = OpportunitySimulation.objects.create(company=other, name="Segredo", origin="national")
        TrendSearch.objects.create(company=other, keywords="termo secreto")
        self.assertEqual(self.client.get(reverse("research:opportunity"), {"simulacao": sim.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("research:simulation_delete", args=[sim.pk])).status_code, 404)
        with mock.patch.object(trends, "trending_now", return_value=[]):
            page = self.client.get(reverse("research:trends"))
        self.assertNotContains(page, "termo secreto")
        self.assertNotContains(self.client.get(reverse("research:simulations")), "Segredo")
        self.assertTrue(OpportunitySimulation.objects.filter(pk=sim.pk).exists())

    def test_cache_holds_only_public_query_data(self):
        with mock.patch.object(trends, "_read", side_effect=lambda opener, url, timeout=20: fake_google(url)), \
                override_settings(SERPAPI_API_KEY=""):
            trends.interest(["camiseta"])
        row = CachedLookup.objects.get()
        self.assertNotIn("company", row.payload)
        self.assertEqual(set(row.payload), {"keywords", "geo", "timeframe", "provider", "fetched_at", "timeline",
                                            "related", "regions"})
