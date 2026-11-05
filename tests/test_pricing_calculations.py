"""Cálculos de preço de venda, markup e margem de contribuição (funções puras)."""

from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.pricing.calculations import (PricingError, PricingInput, analyze_price, calculate_pricing,
                                       markup_multiplier, markup_percent, price_for_margin, simulate_prices)


class PriceFormulaTests(SimpleTestCase):
    def test_suggested_price_formula(self):
        # (custo 50 + fixas por venda 10) / (1 − 0,20 − 0,30) = 60 / 0,5 = 120
        data = PricingInput(acquisition_cost="40", inbound_freight="5", packaging_cost="3", other_unit_costs="2",
                            tax_pct="6", marketplace_pct="10", payment_fee_pct="4", subsidized_shipping="8",
                            handling_cost="2", target_margin_pct="30")
        self.assertEqual(data.unit_cost, D("50"))
        self.assertEqual(data.variable_pct, D("20"))
        self.assertEqual(data.per_sale_costs, D("10"))
        self.assertEqual(price_for_margin(data, data.target_margin_pct), D("120.00"))

    def test_minimum_price_has_zero_contribution(self):
        data = PricingInput(acquisition_cost="50", tax_pct="10", other_per_sale="4", target_margin_pct="25")
        result = calculate_pricing(data)
        # mínimo = 54 / 0,9 = 60
        self.assertEqual(result.minimum_price, D("60.00"))
        self.assertEqual(analyze_price(data, result.minimum_price).contribution_margin, D("0.00"))
        # sugerido = 54 / (1 − 0,10 − 0,25) = 83,08
        self.assertEqual(result.suggested_price, D("83.08"))
        self.assertAlmostEqual(float(result.analysis.contribution_margin_pct), 25.0, delta=0.01)

    def test_percentages_are_converted_to_decimals(self):
        data = PricingInput(acquisition_cost="100", tax_pct="12.5", target_margin_pct="0")
        self.assertEqual(price_for_margin(data, 0), D("114.29"))  # 100 / 0,875

    def test_invalid_denominator_is_rejected_with_explanation(self):
        data = PricingInput(acquisition_cost="10", marketplace_pct="40", tax_pct="20", target_margin_pct="40")
        with self.assertRaises(PricingError) as ctx:
            price_for_margin(data, data.target_margin_pct)
        self.assertIn("100%", str(ctx.exception))

    def test_denominator_exactly_zero_is_rejected(self):
        data = PricingInput(acquisition_cost="10", tax_pct="50", target_margin_pct="50")
        with self.assertRaises(PricingError):
            calculate_pricing(data)

    def test_negative_or_out_of_range_inputs(self):
        with self.assertRaises(PricingError):
            PricingInput(acquisition_cost="-1")
        with self.assertRaises(PricingError):
            PricingInput(acquisition_cost="10", tax_pct="100")
        with self.assertRaises(PricingError):
            PricingInput(acquisition_cost="10", target_margin_pct="-5")

    def test_missing_values_default_to_zero(self):
        data = PricingInput(acquisition_cost=None, tax_pct="", target_margin_pct=None)
        result = calculate_pricing(data)
        self.assertEqual(result.suggested_price, D("0.00"))
        self.assertTrue(result.warnings)  # custo zerado → aviso


class MarkupTests(SimpleTestCase):
    def test_markup_percent_and_multiplier(self):
        self.assertEqual(markup_percent(D("150"), D("100")), D("50"))
        self.assertEqual(markup_multiplier(D("150"), D("100")), D("1.5"))

    def test_markup_is_not_margin(self):
        data = PricingInput(acquisition_cost="100")
        analysis = analyze_price(data, D("200"))
        self.assertEqual(analysis.markup_pct, D("100.00"))  # sobre o custo
        self.assertEqual(analysis.contribution_margin_pct, D("50.00"))  # sobre o preço

    def test_markup_with_zero_cost_is_undefined(self):
        self.assertIsNone(markup_percent(D("10"), D("0")))
        self.assertIsNone(markup_multiplier(D("10"), 0))

    def test_negative_markup_when_price_below_cost(self):
        self.assertEqual(markup_percent(D("80"), D("100")), D("-20"))


class ContributionMarginTests(SimpleTestCase):
    def test_contribution_margin_values(self):
        data = PricingInput(acquisition_cost="30", tax_pct="10", marketplace_pct="10", handling_cost="5")
        a = analyze_price(data, D("100"))
        self.assertEqual(a.variable_pct_amount, D("20.00"))
        self.assertEqual(a.total_variable_expenses, D("25.00"))
        self.assertEqual(a.contribution_margin, D("45.00"))
        self.assertEqual(a.contribution_margin_pct, D("45.00"))
        self.assertEqual(a.margin_on_cost_pct, D("150.00"))
        self.assertEqual(a.result_per_unit, a.contribution_margin)

    def test_negative_contribution(self):
        data = PricingInput(acquisition_cost="90", tax_pct="20")
        self.assertEqual(analyze_price(data, D("100")).contribution_margin, D("-10.00"))

    def test_zero_price_has_no_percentage(self):
        a = analyze_price(PricingInput(acquisition_cost="10"), D("0"))
        self.assertIsNone(a.contribution_margin_pct)

    def test_simulation_ignores_invalid_prices(self):
        data = PricingInput(acquisition_cost="10")
        self.assertEqual(len(simulate_prices(data, [D("20"), D("0"), D("-5"), D("30")])), 2)

    def test_uses_decimal_not_float(self):
        result = calculate_pricing(PricingInput(acquisition_cost="0.1", inbound_freight="0.2", target_margin_pct="0"))
        self.assertIsInstance(result.suggested_price, D)
        self.assertEqual(result.unit_cost, D("0.30"))
