"""Ponto de equilíbrio, ponto de pedido e indicadores de marketing."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase
from django.utils import timezone

from apps.inventory.models import StockMovement
from apps.inventory.reorder import build_rows, reorder_point, suggested_quantity
from apps.marketing.models import cac, conversion_rate, cpc, roas
from apps.pricing import breakeven as be
from apps.sales.models import SalesOrder
from apps.sales.services import save_order

from .factories import CompanyTestCase, make_product


class BreakevenTests(SimpleTestCase):
    def test_single_product(self):
        # preço 100, custo variável 60 → MC 40 (40%); fixas 4.000 → 100 un. / R$ 10.000
        result = be.compute(D("4000"), [be.MixItem("A", D("100"), D("60"), D("100"))], current_revenue=D("12000"))
        self.assertEqual(result.unit_cm, D("40.00"))
        self.assertEqual(result.cm_ratio_pct, D("40.00"))
        self.assertEqual(result.breakeven_units, D("100"))
        self.assertEqual(result.breakeven_revenue, D("10000.00"))
        self.assertEqual(result.current_result, D("800.00"))  # 12.000 × 40% − 4.000
        self.assertEqual(result.safety_margin_pct, D("16.67"))

    def test_basic_formulas(self):
        self.assertEqual(be.breakeven_units(D("1000"), D("25")), D("40"))
        self.assertEqual(be.breakeven_revenue(D("1000"), D("0.25")), D("4000"))
        self.assertEqual(be.unit_contribution(D("50"), D("30")), D("20"))
        self.assertEqual(be.contribution_ratio(D("50"), D("30")), D("0.4"))

    def test_weighted_mix(self):
        items = [be.MixItem("A", D("100"), D("60"), D("50")),  # MC 40
                 be.MixItem("B", D("50"), D("40"), D("50"))]   # MC 10
        result = be.compute(D("2500"), items)
        self.assertEqual(result.unit_cm, D("25.00"))  # 0,5×40 + 0,5×10
        self.assertEqual(result.avg_price, D("75.00"))
        self.assertEqual(result.breakeven_units, D("100"))
        self.assertEqual([i["units"] for i in result.items], [D("50"), D("50")])
        # mix diferente → ponto de equilíbrio diferente
        other = be.compute(D("2500"), [be.MixItem("A", D("100"), D("60"), D("80")),
                                       be.MixItem("B", D("50"), D("40"), D("20"))])
        self.assertLess(other.breakeven_units, result.breakeven_units)

    def test_mix_shares_are_normalized(self):
        result = be.compute(D("100"), [be.MixItem("A", D("10"), D("5"), D("1")), be.MixItem("B", D("10"), D("5"), D("3"))])
        self.assertTrue(any("normalizadas" in w for w in result.warnings))

    def test_zero_or_negative_margin_has_no_breakeven(self):
        result = be.compute(D("1000"), [be.MixItem("A", D("50"), D("50"), D("100"))])
        self.assertIsNone(result.breakeven_units)
        self.assertIsNone(result.breakeven_revenue)
        self.assertTrue(result.warnings)
        self.assertIsNone(be.breakeven_units(D("100"), D("-5")))
        self.assertIsNone(be.breakeven_revenue(D("100"), None))

    def test_invalid_inputs(self):
        with self.assertRaises(be.BreakevenError):
            be.compute(D("100"), [])
        with self.assertRaises(be.BreakevenError):
            be.compute(D("100"), [be.MixItem("A", D("0"), D("0"), D("100"))])
        with self.assertRaises(be.BreakevenError):
            be.compute(D("-1"), [be.MixItem("A", D("10"), D("5"), D("100"))])

    def test_target_and_scenarios(self):
        result = be.compute(D("4000"), [be.MixItem("A", D("100"), D("60"), D("100"))], current_revenue=D("10000"))
        self.assertEqual(be.units_for_target(result.fixed_costs, result.unit_cm, D("2000")), D("150"))
        self.assertEqual(be.revenue_for_target(result.fixed_costs, result.cm_ratio, D("2000")), D("15000.00"))
        sc = be.scenarios(result, fixed_increase_pct=D("10"), cm_improvement_pp=D("10"))
        self.assertEqual([s["result"] for s in sc["sales"]], [D("400.00"), D("800.00"), D("1200.00")])
        self.assertEqual(sc["fixed"]["breakeven_revenue"], D("11000.00"))
        self.assertEqual(sc["cm"]["breakeven_revenue"], D("8000.00"))
        self.assertIsNone(be.units_for_target(D("100"), D("0"), D("10")))


class ReorderFormulaTests(SimpleTestCase):
    def test_reorder_point(self):
        self.assertEqual(reorder_point(D("2.5"), 10, D("5")), D("30"))
        self.assertEqual(reorder_point(0, 0, 0), 0)
        with self.assertRaises(ValueError):
            reorder_point(D("-1"), 5, 0)

    def test_suggested_quantity(self):
        # PP = 2×10+5 = 25; posição 20 → repõe até máx. 60 → 40
        self.assertEqual(suggested_quantity(daily_consumption=D("2"), lead_time_days=10, safety_stock=D("5"),
                                            available=D("15"), incoming=D("5"), max_stock=D("60")), D("40"))
        # sem máximo: alvo = PP + consumo × prazo = 45
        self.assertEqual(suggested_quantity(daily_consumption=D("2"), lead_time_days=10, safety_stock=D("5"),
                                            available=D("20")), D("25"))
        # acima do ponto de pedido → nada a comprar
        self.assertEqual(suggested_quantity(daily_consumption=D("2"), lead_time_days=10, safety_stock=D("5"),
                                            available=D("30")), D("0"))


class ReorderFromHistoryTests(CompanyTestCase):
    def test_average_consumption_uses_sales_history_and_manual_override(self):
        product = make_product(self.company, stock_qty=100, lead_time_days=10, safety_stock=D("5"))
        order = SalesOrder(company=self.company, order_date=timezone.localdate(), status=SalesOrder.Status.PAID)
        save_order(order, [{"product": product, "quantity": 45, "unit_price": D("25")}])
        product.refresh_from_db()
        row = build_rows(self.company, [product])[0]
        self.assertEqual(row.consumption_source, "histórico")
        self.assertEqual(row.daily_consumption, D("0.5000"))  # 45 / 90 dias
        self.assertEqual(row.reorder_point, D("10.00"))  # 0,5 × 10 + 5
        self.assertFalse(row.needs_reorder)
        product.manual_daily_consumption = D("6")
        product.save()
        row = build_rows(self.company, [product])[0]
        self.assertEqual(row.consumption_source, "manual")
        self.assertEqual(row.reorder_point, D("65.00"))
        self.assertTrue(row.needs_reorder)  # disponível 55 ≤ 65
        self.assertGreater(row.suggested_qty, 0)

    def test_old_sales_outside_window_are_ignored(self):
        product = make_product(self.company, stock_qty=10)
        order = SalesOrder(company=self.company, order_date=timezone.localdate(), status=SalesOrder.Status.PAID)
        save_order(order, [{"product": product, "quantity": 5, "unit_price": D("25")}])
        StockMovement.objects.filter(kind=StockMovement.Kind.SALE).update(
            created_at=timezone.now() - timedelta(days=200))
        self.assertEqual(build_rows(self.company, [product])[0].daily_consumption, D("0"))


class MarketingMetricsTests(SimpleTestCase):
    def test_metrics(self):
        self.assertEqual(conversion_rate(5, 200), D("2.5"))
        self.assertEqual(cpc(D("100"), 400), D("0.25"))
        self.assertEqual(cac(D("300"), 10), D("30"))
        self.assertEqual(roas(D("1500"), D("500")), D("3"))

    def test_division_by_zero_returns_none(self):
        self.assertIsNone(conversion_rate(5, 0))
        self.assertIsNone(cpc(D("100"), 0))
        self.assertIsNone(cac(D("100"), 0))
        self.assertIsNone(roas(D("100"), D("0")))
