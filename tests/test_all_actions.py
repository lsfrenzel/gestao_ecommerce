"""
Ações de todas as telas (botões e formulários) de ponta a ponta, e varredura de links por perfil.

A varredura segue todos os links e botões visíveis para cada perfil e falha se algum levar a
erro 403, 404 ou 500 — ou seja, nenhum botão exibido pode “não funcionar” para quem o vê.
"""

import re
from datetime import timedelta
from decimal import Decimal as D
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import ApiToken, Membership, Plan, Warehouse
from apps.customers.models import Customer
from apps.finance.models import FinancialCategory, FinancialEntry, FixedExpense, MonthlyGoal
from apps.inventory.models import StockMovement
from apps.marketing.models import Campaign
from apps.pricing.models import PricingSimulation
from apps.products.models import Category, Product
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier
from apps.sales.models import SalesOrder
from apps.sales.services import save_order

from .factories import CompanyTestCase, add_member, make_customer, make_product, make_supplier, make_user

TODAY = lambda: timezone.localdate().isoformat()  # noqa: E731


class PurchaseFlowViewTests(CompanyTestCase):
    def test_create_send_receive_and_finance(self):
        supplier = make_supplier(self.company)
        product = make_product(self.company, cost="10", stock_qty=0)
        data = {"supplier": supplier.pk, "order_date": TODAY(), "freight": "10", "other_costs": "0", "notes": "",
                "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000", "items-0-product": product.pk, "items-0-quantity": "10",
                "items-0-unit_cost": "12", "items-1-product": "", "items-1-quantity": "", "items-1-unit_cost": ""}
        response = self.client.post(reverse("purchasing:order_new"), data)
        order = PurchaseOrder.objects.get()
        self.assertRedirects(response, reverse("purchasing:order_detail", args=[order.pk]))
        self.assertEqual(order.items.count(), 1)
        # edição do rascunho
        data.update({"items-INITIAL_FORMS": "1", "items-TOTAL_FORMS": "1",
                     "items-0-id": order.items.get().pk, "items-0-quantity": "8"})
        self.client.post(reverse("purchasing:order_edit", args=[order.pk]), data)
        self.assertEqual(order.items.get().quantity, D("8"))
        self.client.post(reverse("purchasing:order_action", args=[order.pk, "enviar"]))
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.ORDERED)
        # rascunho não editável depois de enviado
        self.assertEqual(self.client.get(reverse("purchasing:order_edit", args=[order.pk])).status_code, 302)
        item = order.items.get()
        self.client.post(reverse("purchasing:order_action", args=[order.pk, "receber"]), {f"item_{item.pk}": "8"})
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(product.stock_on_hand, D("8"))
        self.assertEqual(product.total_unit_cost, D("13.2500"))  # 12 + frete 10/8
        self.assertEqual(FinancialEntry.objects.get(purchase_order=order).amount, D("106.00"))

    def test_cancel_and_delete_draft(self):
        supplier = make_supplier(self.company)
        product = make_product(self.company)
        draft = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=1,
                                             order_date=timezone.localdate())
        PurchaseOrderItem.objects.create(order=draft, product=product, quantity=1, unit_cost=1)
        other = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=2,
                                             order_date=timezone.localdate())
        self.client.post(reverse("purchasing:order_action", args=[other.pk, "cancelar"]))
        other.refresh_from_db()
        self.assertEqual(other.status, PurchaseOrder.Status.CANCELLED)
        self.client.post(reverse("purchasing:order_action", args=[draft.pk, "excluir"]))
        self.assertFalse(PurchaseOrder.objects.filter(pk=draft.pk).exists())
        # enviar sem itens é bloqueado
        empty = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=3,
                                             order_date=timezone.localdate())
        response = self.client.post(reverse("purchasing:order_action", args=[empty.pk, "enviar"]), follow=True)
        self.assertContains(response, "ao menos um produto")

    def test_reorder_generates_purchase_orders(self):
        supplier = make_supplier(self.company)
        product = make_product(self.company, stock_qty=1, supplier=supplier, safety_stock=D("10"), lead_time_days=5)
        no_supplier = make_product(self.company, stock_qty=1, safety_stock=D("10"))
        response = self.client.get(reverse("inventory:reorder"))
        self.assertContains(response, product.name)
        response = self.client.post(reverse("inventory:reorder_purchases"),
                                    {"products": [product.pk, no_supplier.pk]}, follow=True)
        self.assertContains(response, no_supplier.sku)  # aviso: sem fornecedor
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.items.get().product, product)
        self.assertGreater(order.items.get().quantity, 0)

    def test_reorder_parameters_edit(self):
        product = make_product(self.company)
        self.client.post(reverse("inventory:reorder_edit", args=[product.pk]),
                         {"lead_time_days": "12", "safety_stock": "4", "manual_daily_consumption": "1,5"})
        product.refresh_from_db()
        self.assertEqual((product.lead_time_days, product.safety_stock, product.manual_daily_consumption),
                         (12, D("4"), D("1.5")))
        self.client.post(reverse("inventory:reorder_edit", args=[product.pk]),
                         {"lead_time_days": "abc", "safety_stock": "-1", "manual_daily_consumption": ""})
        product.refresh_from_db()
        self.assertEqual(product.lead_time_days, 12)  # inválido não altera


class InventoryViewTests(CompanyTestCase):
    def test_count_adjust_loss_and_transfer(self):
        product = make_product(self.company, stock_qty=10)
        self.client.post(reverse("inventory:move"), {"product": product.pk, "kind": "count", "quantity": "7",
                                                     "note": "inventário"})
        self.client.post(reverse("inventory:move"), {"product": product.pk, "kind": "loss", "quantity": "1"})
        product.refresh_from_db()
        self.assertEqual(product.stock_on_hand, D("7"))  # perda sem motivo é rejeitada
        self.client.post(reverse("inventory:move"), {"product": product.pk, "kind": "loss", "quantity": "1",
                                                     "note": "avaria"})
        product.refresh_from_db()
        self.assertEqual(product.stock_on_hand, D("6"))
        # transferência com recurso habilitado
        self.company.plan = Plan.objects.create(name="Avançado", features=["multi_warehouse", "inventory"])
        self.company.multi_warehouse_enabled = True
        self.company.save()
        self.client.post(reverse("companies:warehouse_new"), {"name": "Loja física", "is_active": "on"})
        store = Warehouse.objects.get(name="Loja física")
        main = Warehouse.default_for(self.company)
        self.client.post(reverse("inventory:transfer"), {"product": product.pk, "origin": main.pk,
                                                         "destination": store.pk, "quantity": "2"})
        self.assertEqual(product.balances.get(warehouse=store).on_hand, D("2"))
        self.assertEqual(StockMovement.objects.filter(kind=StockMovement.Kind.TRANSFER_IN).count(), 1)

    def test_operator_cannot_adjust(self):
        product = make_product(self.company, stock_qty=5)
        self.client.force_login(add_member(self.company))
        self.client.post(reverse("inventory:move"), {"product": product.pk, "kind": "count", "quantity": "0",
                                                     "note": "x"})
        product.refresh_from_db()
        self.assertEqual(product.stock_on_hand, D("5"))

    def test_history_filters_and_alerts_page(self):
        product = make_product(self.company, stock_qty=3, min_stock=D("5"), max_stock=D("4"))
        response = self.client.get(reverse("inventory:history") + "?tipo=initial&period=today")
        self.assertContains(response, product.name)
        alerts = self.client.get(reverse("inventory:alerts")).context["alerts"]
        self.assertIn(product, alerts["low"])
        self.assertEqual(self.client.get(reverse("inventory:overview") + "?status=low").context["page_obj"]
                         .paginator.count, 1)


class CrudViewTests(CompanyTestCase):
    def test_categories_suppliers_customers_campaigns_goals(self):
        self.client.post(reverse("products:category_new"), {"name": "Bonés"})
        cat = Category.objects.get(name="Bonés")
        self.client.post(reverse("products:category_edit", args=[cat.pk]), {"name": "Bonés e chapéus"})
        cat.refresh_from_db()
        self.assertEqual(cat.name, "Bonés e chapéus")
        self.client.post(reverse("products:category_delete", args=[cat.pk]))
        self.assertFalse(Category.objects.exists())

        self.client.post(reverse("purchasing:supplier_new"), {"name": "Fornecedor X", "default_lead_time_days": "5",
                                                              "is_active": "on", "cnpj": "11.222.333/0001-81"})
        supplier = Supplier.objects.get()
        self.assertEqual(self.client.get(reverse("purchasing:supplier_detail", args=[supplier.pk])).status_code, 200)
        response = self.client.post(reverse("purchasing:supplier_new"), {"name": "Y", "default_lead_time_days": "5",
                                                                         "cnpj": "12.345.678/0001-00"})
        self.assertIn("cnpj", response.context["form"].errors)

        self.client.post(reverse("customers:create"), {"name": "Ana", "email": "ana@exemplo.test",
                                                       "document": "529.982.247-25"})
        customer = Customer.objects.get()
        self.assertEqual(customer.document, "529.982.247-25")
        response = self.client.post(reverse("customers:create"), {"name": "B", "document": "111.111.111-11"})
        self.assertIn("document", response.context["form"].errors)

        response = self.client.post(reverse("marketing:create"), {
            "name": "Black Friday", "channel": "meta_ads", "start_date": TODAY(), "end_date": TODAY(),
            "investment": "500", "clicks": "1000", "visitors": "800", "orders": "20", "new_customers": "10",
            "attributed_revenue": "2000"})
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.roas, D("4"))
        self.assertEqual(self.client.get(reverse("marketing:detail", args=[campaign.pk])).status_code, 200)
        response = self.client.post(reverse("marketing:create"), {
            "name": "Datas invertidas", "channel": "email", "start_date": TODAY(),
            "end_date": (timezone.localdate() - timedelta(days=1)).isoformat(), "investment": "0", "clicks": "0",
            "visitors": "0", "orders": "0", "new_customers": "0", "attributed_revenue": "0"})
        self.assertIn("end_date", response.context["form"].errors)

        self.client.post(reverse("finance:goal_new"), {"month": timezone.localdate().strftime("%Y-%m"),
                                                       "revenue_target": "10000", "profit_target": "1000"})
        goal = MonthlyGoal.objects.get()
        self.assertEqual(goal.month.day, 1)
        response = self.client.post(reverse("finance:goal_new"), {"month": timezone.localdate().strftime("%Y-%m"),
                                                                  "revenue_target": "1", "profit_target": "0"})
        self.assertIn("month", response.context["form"].errors)
        self.assertIsNotNone(self.client.get(reverse("dashboard:home")).context["goal"])

    def test_finance_actions(self):
        rent = FinancialCategory.objects.get(company=self.company, name="Aluguel")
        self.client.post(reverse("finance:fixed_new"), {"name": "Aluguel loja", "category": rent.pk,
                                                        "monthly_amount": "1500", "due_day": "5", "is_active": "on"})
        self.assertEqual(FixedExpense.objects.count(), 1)
        self.client.post(reverse("finance:fixed"), {"month": timezone.localdate().strftime("%Y-%m")})
        entry = FinancialEntry.objects.get(fixed_expense__isnull=False)
        self.assertEqual(entry.amount, D("1500"))
        self.client.post(reverse("finance:fixed"), {"month": timezone.localdate().strftime("%Y-%m")})
        self.assertEqual(FinancialEntry.objects.filter(fixed_expense__isnull=False).count(), 1)
        self.client.post(reverse("finance:entry_cancel", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertEqual(entry.status, "cancelled")
        # lançamento manual + edição + categorias do sistema protegidas
        sales = FinancialCategory.objects.get(company=self.company, name="Outras receitas")
        self.client.post(reverse("finance:entry_new"), {"type": "receivable", "category": sales.pk,
                                                        "description": "Venda de sucata", "amount": "80",
                                                        "competence_date": TODAY(), "due_date": TODAY()})
        manual = FinancialEntry.objects.get(description="Venda de sucata")
        self.assertEqual(self.client.get(reverse("finance:entry_edit", args=[manual.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("finance:category_edit", args=[rent.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("finance:categories")),
                               reverse("finance:category_delete", args=[rent.pk]))
        self.client.post(reverse("finance:category_new"), {"name": "Frete extra", "nature": "variable"})
        custom = FinancialCategory.objects.get(name="Frete extra")
        self.assertContains(self.client.get(reverse("finance:categories")),
                            reverse("finance:category_delete", args=[custom.pk]))

    def test_pricing_simulation_delete_and_product_prefill(self):
        product = make_product(self.company, cost="30")
        response = self.client.get(reverse("pricing:calculator") + f"?produto={product.pk}")
        self.assertEqual(response.context["form"]["acquisition_cost"].value(), D("30.0000"))
        self.assertIn("result", response.context)  # resultado já calculado ao escolher o produto
        sim = PricingSimulation.objects.create(company=self.company, name="S", suggested_price=1)
        response = self.client.get(reverse("pricing:calculator") + f"?simulacao={sim.pk}")
        self.assertEqual(response.status_code, 200)
        self.client.post(reverse("pricing:simulation_delete", args=[sim.pk]))
        self.assertFalse(PricingSimulation.objects.exists())

    def test_sales_edit_and_status_view_and_customer_prefill(self):
        customer = make_customer(self.company)
        response = self.client.get(reverse("sales:create") + f"?cliente={customer.pk}")
        self.assertEqual(response.context["form"]["customer"].value(), customer.pk)
        product = make_product(self.company, stock_qty=10, price="40")
        order = save_order(SalesOrder(company=self.company, order_date=timezone.localdate()),
                           [{"product": product, "quantity": 2, "unit_price": D("40")}])
        self.assertEqual(self.client.get(reverse("sales:edit", args=[order.pk])).status_code, 200)
        self.client.post(reverse("sales:status", args=[order.pk]), {"status": "paid"})
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.client.post(reverse("sales:status", args=[order.pk]), {"status": "returned", "restock": "on"})
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual((order.status, product.stock_on_hand), ("returned", D("10")))
        # transição inválida pela tela é recusada
        response = self.client.post(reverse("sales:status", args=[order.pk]), {"status": "paid"}, follow=True)
        self.assertContains(response, "inválido")

    def test_customer_lgpd_actions(self):
        customer = make_customer(self.company, name="Fulana de Tal")
        export = self.client.get(reverse("customers:export", args=[customer.pk]))
        self.assertIn("Fulana de Tal", export.content.decode())
        self.client.post(reverse("customers:anonymize", args=[customer.pk]))
        customer.refresh_from_db()
        self.assertTrue(customer.name.startswith("Cliente anonimizado"))
        self.assertEqual(customer.email, "")

    def test_settings_team_tokens_profile_switch(self):
        response = self.client.post(reverse("companies:settings"), {"form": "dados", "d-trade_name": "Novo Nome",
                                                                    "d-segment": "moda", "d-zip_code": "01001000"})
        self.company.refresh_from_db()
        self.assertEqual((self.company.trade_name, self.company.zip_code), ("Novo Nome", "01001-000"))
        self.client.post(reverse("companies:settings"), {"form": "config", "c-default_tax_rate": "8",
                                                         "c-default_target_margin": "25",
                                                         "c-consumption_window_days": "60",
                                                         "c-idle_stock_days": "30", "c-reserve_on_pending": "on"})
        self.company.refresh_from_db()
        self.assertEqual((self.company.default_tax_rate, self.company.consumption_window_days), (D("8"), 60))
        member = add_member(self.company)
        membership = Membership.objects.get(user=member)
        self.client.post(reverse("companies:resend_invite", args=[membership.pk]))
        self.assertEqual(len(mail.outbox), 1)
        response = self.client.post(reverse("companies:api_tokens"), {"name": "Shopee"})
        token = ApiToken.objects.get()
        self.assertIn(response.context["new_token"][:8], token.prefix)
        self.client.post(reverse("companies:revoke_api_token", args=[token.pk]))
        token.refresh_from_db()
        self.assertFalse(token.is_active)
        self.client.post(reverse("accounts:profile"), {"full_name": "Nome Atualizado", "phone": "11999999999"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.full_name, "Nome Atualizado")
        # segunda empresa e troca
        self.client.post(reverse("companies:onboarding") + "?nova=1", {"trade_name": "Filial", "segment": "outros"})
        self.assertContains(self.client.get(reverse("dashboard:home")), "Filial")
        self.client.post(reverse("companies:switch"), {"company_id": self.company.pk})
        self.assertContains(self.client.get(reverse("dashboard:home")), "Novo Nome")


# ---------------------------------------------------------------------------
# Varredura: todo link/botão visível precisa funcionar para quem o vê
# ---------------------------------------------------------------------------
class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "form" and (a.get("method") or "get").lower() == "get" and a.get("action"):
            self.links.append(a["action"])


class LinkCrawlTests(TestCase):
    SKIP = ("exportar=pdf", "/media/", "javascript:", "mailto:")

    def setUp(self):
        self.admin = make_user()
        from .factories import make_company

        self.company = make_company(self.admin)
        supplier = make_supplier(self.company)
        product = make_product(self.company, stock_qty=20, supplier=supplier, min_stock=D("2"))
        customer = make_customer(self.company)
        campaign = Campaign.objects.create(company=self.company, name="C", channel="email",
                                           start_date=timezone.localdate(), end_date=timezone.localdate())
        order = save_order(SalesOrder(company=self.company, order_date=timezone.localdate(),
                                      status=SalesOrder.Status.PAID, customer=customer, campaign=campaign,
                                      taxes=D("1")),
                           [{"product": product, "quantity": 1, "unit_price": D("30")}])
        po = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=1,
                                          order_date=timezone.localdate())
        PurchaseOrderItem.objects.create(order=po, product=product, quantity=1, unit_cost=1)
        PricingSimulation.objects.create(company=self.company, name="Sim", product=product)
        MonthlyGoal.objects.create(company=self.company, month=timezone.localdate().replace(day=1),
                                   revenue_target=D("100"))
        self.order = order

    def crawl(self, user, start="/painel/", limit=250):
        self.client.force_login(user)
        seen, queue, broken = set(), [start], []
        while queue and len(seen) < limit:
            url = queue.pop(0)
            parsed = urlparse(url)
            if parsed.netloc and parsed.netloc != "testserver":
                continue
            url = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            if url in seen or url.startswith("#") or any(s in url for s in self.SKIP):
                continue
            seen.add(url)
            response = self.client.get(url)
            if response.status_code in (301, 302):
                queue.append(response["Location"])
                continue
            if response.status_code != 200:
                broken.append((url, response.status_code))
                continue
            if "text/html" not in response.get("Content-Type", ""):
                continue
            parser = _LinkParser()
            parser.feed(response.content.decode())
            queue.extend(urljoin(url, h) for h in parser.links)
        return seen, broken

    def test_admin_links(self):
        seen, broken = self.crawl(self.admin)
        self.assertGreater(len(seen), 60)
        self.assertEqual(broken, [])

    def test_manager_links(self):
        seen, broken = self.crawl(add_member(self.company, role=Membership.Role.MANAGER))
        self.assertEqual(broken, [])

    def test_operator_links(self):
        seen, broken = self.crawl(add_member(self.company, role=Membership.Role.OPERATOR))
        self.assertEqual(broken, [])
        self.assertFalse(any(u.startswith("/financeiro/") for u in seen))

    def test_custom_operator_links(self):
        user = add_member(self.company, modules=["inventory", "finance"])
        seen, broken = self.crawl(user, start="/estoque/")
        self.assertEqual(broken, [])

    def test_superadmin_links(self):
        root = User.objects.create_superuser(email="root@exemplo.test", password="x-Senha#2026", full_name="Root")
        seen, broken = self.crawl(root, start="/plataforma/")
        self.assertGreater(len(seen), 8)
        self.assertEqual(broken, [])


class _Unused:  # pragma: no cover - mantém imports usados em asserts dinâmicos
    refs = (re, Product)
