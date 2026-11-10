"""Produtos, precificação via interface, financeiro, relatórios/exportações e health check."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Plan
from apps.dashboard.metrics import sales_kpis
from apps.finance import services as finance
from apps.finance.models import FinancialCategory, FinancialEntry, FixedExpense, Nature
from apps.inventory.models import StockMovement
from apps.pricing.models import PricingSimulation
from apps.products.models import Category, PriceHistory, Product
from apps.reports.registry import REPORTS
from apps.sales.models import SalesOrder
from apps.sales.services import change_status, save_order

from .factories import CompanyTestCase, make_product

PRODUCT_DATA = {"name": "Camiseta", "sku": "cam-01", "unit": "un", "acquisition_cost": "20", "purchase_freight": "2",
                "other_costs": "1", "sale_price": "59.90", "min_stock": "2", "max_stock": "50", "lead_time_days": "7",
                "safety_stock": "1", "is_active": "on"}


class ProductTests(CompanyTestCase):
    def test_create_with_initial_stock_and_history(self):
        response = self.client.post(reverse("products:create"), {**PRODUCT_DATA, "initial_stock": "12"})
        product = Product.objects.get()
        self.assertRedirects(response, reverse("products:detail", args=[product.pk]))
        self.assertEqual(product.sku, "CAM-01")
        self.assertEqual(product.total_unit_cost, D("23"))
        self.assertEqual(product.stock_on_hand, D("12"))
        self.assertEqual(StockMovement.objects.get(product=product).kind, StockMovement.Kind.INITIAL)
        self.assertEqual(PriceHistory.objects.filter(product=product).count(), 1)

    def test_edit_records_price_history(self):
        product = make_product(self.company, sku="CAM-01")
        self.client.post(reverse("products:edit", args=[product.pk]), {**PRODUCT_DATA, "sale_price": "69.90"})
        product.refresh_from_db()
        self.assertEqual(product.sale_price, D("69.90"))
        history = PriceHistory.objects.get(product=product)
        self.assertEqual((history.old_sale_price, history.new_sale_price), (D("25.00"), D("69.90")))
        self.assertContains(self.client.get(reverse("products:detail", args=[product.pk])), "69,90")

    def test_validation(self):
        make_product(self.company, sku="CAM-01")
        response = self.client.post(reverse("products:create"), {**PRODUCT_DATA, "acquisition_cost": "-5",
                                                                 "max_stock": "1"})
        errors = response.context["form"].errors
        self.assertIn("sku", errors)  # duplicado (sem diferenciar maiúsculas)
        self.assertIn("acquisition_cost", errors)
        self.assertIn("max_stock", errors)  # máximo < mínimo
        self.assertEqual(Product.objects.count(), 1)

    def test_rejects_non_image_upload(self):
        fake = SimpleUploadedFile("x.png", b"<?php echo 1; ?>", content_type="image/png")
        response = self.client.post(reverse("products:create"), {**PRODUCT_DATA, "image": fake})
        self.assertIn("image", response.context["form"].errors)

    def test_search_filter_and_archive(self):
        cat = Category.objects.create(company=self.company, name="Bonés")
        make_product(self.company, sku="BON-1", name="Boné azul", category=cat)
        make_product(self.company, sku="MEI-1", name="Meia")
        response = self.client.get(reverse("products:list") + "?q=bon")
        self.assertContains(response, "Boné azul")
        self.assertNotContains(response, "Meia")
        response = self.client.get(reverse("products:list") + f"?categoria={cat.pk}")
        self.assertEqual(response.context["total"], 1)
        product = Product.objects.get(sku="MEI-1")
        self.client.post(reverse("products:toggle_active", args=[product.pk]))
        self.assertNotContains(self.client.get(reverse("products:list")), "Meia")

    def test_delete_only_without_movements(self):
        clean = make_product(self.company)
        used = make_product(self.company, stock_qty=1)
        self.client.post(reverse("products:delete", args=[clean.pk]))
        self.client.post(reverse("products:delete", args=[used.pk]))
        self.assertFalse(Product.objects.filter(pk=clean.pk).exists())
        self.assertTrue(Product.objects.filter(pk=used.pk).exists())

    def test_csv_import_and_export(self):
        csv = ("sku;nome;categoria;custo_aquisicao;preco_venda;unidade\n"
               "A1;Produto A;Linha 1;10,50;29,90;un\n"
               "A2;Produto B;Linha 1;abc;10;un\n"
               ";Sem SKU;;1;2;un\n"
               "A3;Produto C;;1;2;caixa-grande\n").encode("utf-8")
        response = self.client.post(reverse("products:import"), {"file": SimpleUploadedFile("p.csv", csv),
                                                                 "update_existing": "on"})
        result = response.context["result"]
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(result["errors"]), 3)
        product = Product.objects.get(sku="A1")
        self.assertEqual((product.acquisition_cost, product.sale_price), (D("10.5"), D("29.90")))
        self.assertEqual(product.category.name, "Linha 1")
        export = self.client.get(reverse("products:list") + "?exportar=csv")
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("A1;Produto A", export.content.decode("utf-8-sig"))

    def test_csv_import_rejects_empty_file(self):
        response = self.client.post(reverse("products:import"), {"file": SimpleUploadedFile("p.csv", b"")})
        self.assertIsNone(response.context["result"])

    def test_plan_product_limit(self):
        self.company.plan = Plan.objects.create(name="Limitado", max_products=1, features=[])
        self.company.save()
        make_product(self.company)
        response = self.client.post(reverse("products:create"), PRODUCT_DATA)
        self.assertEqual(Product.objects.count(), 1)
        self.assertRedirects(response, reverse("products:list"))


class PricingViewTests(CompanyTestCase):
    def payload(self, **extra):
        data = {"acquisition_cost": "40", "inbound_freight": "5", "packaging_cost": "3", "other_unit_costs": "2",
                "tax_pct": "6", "marketplace_pct": "10", "payment_fee_pct": "4", "sales_commission_pct": "0",
                "other_pct": "0", "subsidized_shipping": "8", "handling_cost": "2", "other_per_sale": "0",
                "target_margin_pct": "30", "simulate_prices": "100; 150"}
        data.update(extra)
        return data

    def test_calculate_save_and_apply(self):
        product = make_product(self.company, price="90")
        response = self.client.post(reverse("pricing:calculator"), self.payload(product=product.pk))
        self.assertEqual(response.context["result"].suggested_price, D("120.00"))
        self.assertEqual(len(response.context["simulations"]), 3)  # preço atual + 2 simulados
        response = self.client.post(reverse("pricing:calculator"), self.payload(product=product.pk, action="save",
                                                                                name="Teste"))
        sim = PricingSimulation.objects.get()
        self.assertEqual(sim.suggested_price, D("120.00"))
        self.client.post(reverse("pricing:apply"), {"product": product.pk, "price": "120.00", "simulation": sim.pk})
        product.refresh_from_db()
        self.assertEqual(product.sale_price, D("120.00"))
        self.assertTrue(PriceHistory.objects.filter(product=product, reason__icontains="calculadora").exists())
        sim.refresh_from_db()
        self.assertIsNotNone(sim.applied_at)

    def test_invalid_combination_shows_message(self):
        response = self.client.post(reverse("pricing:calculator"),
                                    self.payload(marketplace_pct="50", target_margin_pct="45"),
                                    HTTP_HX_REQUEST="true")
        self.assertContains(response, "Combinação inválida")
        self.assertFalse(PricingSimulation.objects.exists())

    def test_breakeven_view(self):
        expense_cat = FinancialCategory.objects.get(company=self.company, name="Aluguel")
        FixedExpense.objects.create(company=self.company, name="Aluguel", category=expense_cat,
                                    monthly_amount=D("4000"))
        product = make_product(self.company, cost="60", price="100")
        response = self.client.post(reverse("pricing:breakeven"), {
            "mode": "single", "fixed_costs": "4000", "variable_pct": "0", "per_unit_costs": "0",
            "product": product.pk, "current_revenue": "12000", "target_profit": "2000",
            "mix-TOTAL_FORMS": "0", "mix-INITIAL_FORMS": "0", "mix-MIN_NUM_FORMS": "0", "mix-MAX_NUM_FORMS": "30"})
        result = response.context["result"]
        self.assertEqual(result.breakeven_units, D("100"))
        self.assertEqual(response.context["target"]["units"], D("150"))
        self.assertEqual(response.context["fixed_default"], D("4000"))


class FinanceAndReportTests(CompanyTestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(self.company, cost="40", price="100", stock_qty=20)
        today = timezone.localdate()
        self.paid = save_order(SalesOrder(company=self.company, order_date=today, status=SalesOrder.Status.PAID,
                                          commission=D("10"), taxes=D("6")),
                               [{"product": self.product, "quantity": 2, "unit_price": D("100")}])
        self.pending = save_order(SalesOrder(company=self.company, order_date=today),
                                  [{"product": self.product, "quantity": 1, "unit_price": D("100")}])
        rent = FinancialCategory.objects.get(company=self.company, name="Aluguel")
        FinancialEntry.objects.create(company=self.company, type="payable", category=rent, description="Aluguel",
                                      amount=D("50"), competence_date=today, due_date=today)

    def test_kpis_distinguish_concepts_and_ignore_pending(self):
        today = timezone.localdate()
        k = sales_kpis(self.company, today - timedelta(days=1), today)
        self.assertEqual(k["revenue"], D("200.00"))  # pedido pendente não é receita
        self.assertEqual(k["orders"], 1)
        self.assertEqual(k["cogs"], D("80.00"))
        self.assertEqual(k["gross_profit"], D("120.00"))
        self.assertEqual(k["variable_expenses"], D("16.00"))
        self.assertEqual(k["contribution_margin"], D("104.00"))
        self.assertEqual(k["fixed_expenses"], D("50.00"))
        self.assertEqual(k["operating_result"], D("54.00"))
        self.assertEqual(k["avg_ticket"], D("200.00"))
        self.assertEqual(k["pending_orders"], 1)

    def test_cancelled_order_leaves_revenue(self):
        change_status(self.paid, SalesOrder.Status.CANCELLED)
        today = timezone.localdate()
        self.assertEqual(sales_kpis(self.company, today, today)["revenue"], D("0"))
        flow = finance.cash_flow(self.company, today, today)
        self.assertEqual(flow["inflow"], D("200.00"))
        self.assertEqual(flow["outflow"], D("210.00"))  # comissão + estorno (impostos em aberto cancelados)
        self.assertFalse(FinancialEntry.objects.filter(sales_order=self.paid, status="open").exists())

    def test_kpis_are_rounded_to_cents_and_prefill_breakeven_form(self):
        today = timezone.localdate()
        k = sales_kpis(self.company, today, today)
        self.assertEqual(k["revenue"].as_tuple().exponent, -2)
        response = self.client.get(reverse("pricing:breakeven") + "?modo=mix&mix=vendas")
        form = response.context["form"]
        data = {name: form[name].value() for name in form.fields if form[name].value() is not None}
        data.update({"mix-TOTAL_FORMS": "0", "mix-INITIAL_FORMS": "0", "mix-MIN_NUM_FORMS": "0",
                     "mix-MAX_NUM_FORMS": "30", "mode": "single", "product": self.product.pk})
        response = self.client.post(reverse("pricing:breakeven"), data)
        self.assertFalse(response.context["form"].errors)

    def test_empty_period_has_no_division_errors(self):
        far = timezone.localdate() - timedelta(days=400)
        k = sales_kpis(self.company, far, far)
        self.assertEqual(k["revenue"], D("0"))
        self.assertIsNone(k["avg_ticket"])
        self.assertIsNone(k["contribution_margin_pct"])

    def test_settle_and_cash_flow(self):
        entry = FinancialEntry.objects.get(description="Aluguel")
        response = self.client.post(reverse("finance:entry_settle", args=[entry.pk]),
                                    {"settled_at": timezone.localdate().isoformat(), "amount": "50"})
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, "settled")
        with self.assertRaises(ValueError):
            finance.settle(entry, settled_at=timezone.localdate())

    def test_settle_returns_to_filtered_list(self):
        entry = FinancialEntry.objects.get(description="Aluguel")
        list_url = reverse("finance:entries", kwargs={"kind": "pagar"}) + "?situacao=abertos&q=Aluguel"
        response = self.client.post(reverse("finance:entry_settle", args=[entry.pk]),
                                    {"settled_at": timezone.localdate().isoformat(), "amount": "50"},
                                    HTTP_REFERER="http://testserver" + list_url)
        self.assertRedirects(response, list_url, fetch_redirect_response=False)
        # Referer externo é ignorado (sem redirecionamento aberto).
        other = FinancialEntry.objects.create(company=self.company, type="payable", category=entry.category,
                                              description="Outro", amount=D("10"),
                                              competence_date=timezone.localdate(), due_date=timezone.localdate())
        response = self.client.post(reverse("finance:entry_settle", args=[other.pk]),
                                    {"settled_at": timezone.localdate().isoformat(), "amount": "10"},
                                    HTTP_REFERER="https://evil.example/financeiro/contas/pagar/")
        self.assertRedirects(response, reverse("finance:entries", kwargs={"kind": "pagar"}),
                             fetch_redirect_response=False)

    def test_generate_fixed_expenses_without_duplicates(self):
        cat = FinancialCategory.objects.get(company=self.company, name="Internet e telefone")
        FixedExpense.objects.create(company=self.company, name="Internet", category=cat, monthly_amount=D("100"),
                                    due_day=31)
        month = timezone.localdate().replace(month=2, day=1)
        self.assertEqual(finance.generate_fixed_expenses(self.company, month), 1)
        self.assertEqual(finance.generate_fixed_expenses(self.company, month), 0)
        entry = FinancialEntry.objects.get(fixed_expense__isnull=False)
        self.assertLessEqual(entry.due_date.day, 29)  # dia 31 ajustado ao fim de fevereiro

    def test_receivable_requires_revenue_category(self):
        rent = FinancialCategory.objects.get(company=self.company, name="Aluguel")
        response = self.client.post(reverse("finance:entry_new"), {
            "type": "receivable", "category": rent.pk, "description": "x", "amount": "10",
            "competence_date": timezone.localdate().isoformat(), "due_date": timezone.localdate().isoformat()})
        self.assertIn("category", response.context["form"].errors)
        response = self.client.post(reverse("finance:entry_new"), {
            "type": "payable", "category": rent.pk, "description": "x", "amount": "0",
            "competence_date": timezone.localdate().isoformat(), "due_date": timezone.localdate().isoformat()})
        self.assertIn("amount", response.context["form"].errors)

    def test_all_reports_render_and_export(self):
        for report in REPORTS:
            url = reverse("reports:report", args=[report.slug])
            self.assertEqual(self.client.get(url).status_code, 200, report.slug)
            csv = self.client.get(url + "?exportar=csv")
            self.assertTrue(csv["Content-Disposition"].endswith('.csv"'), report.slug)
            pdf = self.client.get(url + "?exportar=pdf")
            self.assertEqual(pdf["Content-Type"], "application/pdf", report.slug)
            self.assertTrue(pdf.content.startswith(b"%PDF"), report.slug)

    def test_operating_result_report_values(self):
        response = self.client.get(reverse("reports:report", args=["resultado"]) + "?period=today")
        rows = response.context["report"].rows
        self.assertEqual(rows[0][1], D("200.00"))
        self.assertEqual(rows[-1][1], D("54.00"))

    def test_contribution_report_flags_loss_products(self):
        loser = make_product(self.company, sku="PREJ", cost="90", price="80", stock_qty=5)
        save_order(SalesOrder(company=self.company, order_date=timezone.localdate(), status=SalesOrder.Status.PAID),
                   [{"product": loser, "quantity": 1, "unit_price": D("80")}])
        response = self.client.get(reverse("reports:report", args=["margem-contribuicao"]) + "?period=today")
        first = response.context["report"].rows[0]
        self.assertEqual(first[0], "PREJ")
        self.assertLess(first[6], 0)

    def test_csv_export_neutralizes_formulas(self):
        make_product(self.company, sku="X", name="=HYPERLINK(\"http://x\")")
        content = self.client.get(reverse("products:list") + "?exportar=csv").content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", content)

    def test_pdf_requires_plan_feature(self):
        self.company.plan = Plan.objects.create(name="Sem PDF", features=["finance"])
        self.company.save()
        response = self.client.get(reverse("reports:report", args=["faturamento"]) + "?exportar=pdf")
        self.assertEqual(response.status_code, 402)


class HealthTests(TestCase):
    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_security_headers(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")


class NatureSanity(TestCase):
    def test_default_categories_created_per_company(self):
        from .factories import make_company

        a, b = make_company(), make_company()
        self.assertEqual(FinancialCategory.objects.filter(company=a, nature=Nature.FIXED).count(),
                         FinancialCategory.objects.filter(company=b, nature=Nature.FIXED).count())
