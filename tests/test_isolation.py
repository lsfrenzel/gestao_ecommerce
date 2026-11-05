"""Isolamento de dados entre empresas (multi-tenant) — sempre garantido no backend."""

import json
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import ApiToken, Membership
from apps.customers.models import Customer
from apps.products.models import Product
from apps.purchasing.models import PurchaseOrder
from apps.sales.models import SalesOrder
from apps.sales.services import save_order

from .factories import add_member, make_company, make_customer, make_product, make_supplier, make_user


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.a = make_company(name="Loja A")
        self.b = make_company(name="Loja B")
        self.product_b = make_product(self.b, sku="B-1", name="Produto da Loja B", stock_qty=5)
        self.customer_b = make_customer(self.b, name="Cliente da Loja B")
        self.supplier_b = make_supplier(self.b, name="Fornecedor B")
        self.order_b = save_order(SalesOrder(company=self.b, order_date=timezone.localdate()),
                                  [{"product": self.product_b, "quantity": 1, "unit_price": D("10")}])
        self.client.force_login(self.a.owner)

    def test_detail_edit_delete_of_other_company_return_404(self):
        urls = [
            reverse("products:detail", args=[self.product_b.pk]), reverse("products:edit", args=[self.product_b.pk]),
            reverse("products:delete", args=[self.product_b.pk]),
            reverse("customers:detail", args=[self.customer_b.pk]), reverse("customers:edit", args=[self.customer_b.pk]),
            reverse("customers:export", args=[self.customer_b.pk]),
            reverse("sales:detail", args=[self.order_b.pk]), reverse("sales:edit", args=[self.order_b.pk]),
            reverse("purchasing:supplier_detail", args=[self.supplier_b.pk]),
            reverse("purchasing:supplier_edit", args=[self.supplier_b.pk]),
            reverse("inventory:reorder_edit", args=[self.product_b.pk]),
            reverse("pricing:calculator") + f"?produto={self.product_b.pk}",
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_mutations_on_other_company_are_rejected(self):
        self.client.post(reverse("products:delete", args=[self.product_b.pk]))
        self.client.post(reverse("products:toggle_active", args=[self.product_b.pk]))
        self.client.post(reverse("sales:status", args=[self.order_b.pk]), {"status": "cancelled"})
        self.client.post(reverse("customers:anonymize", args=[self.customer_b.pk]))
        self.assertTrue(Product.objects.filter(pk=self.product_b.pk, is_active=True).exists())
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_b.status, SalesOrder.Status.PENDING)
        self.assertEqual(Customer.objects.get(pk=self.customer_b.pk).name, "Cliente da Loja B")

    def test_lists_search_and_dashboard_show_only_own_data(self):
        make_product(self.a, sku="A-1", name="Produto da Loja A")
        for url in (reverse("products:list"), reverse("customers:list"), reverse("sales:list"),
                    reverse("inventory:overview"), reverse("dashboard:search") + "?q=Loja",
                    reverse("reports:report", args=["estoque"])):
            response = self.client.get(url)
            self.assertNotContains(response, "Loja B", msg_prefix=url)
        self.assertContains(self.client.get(reverse("products:list")), "Produto da Loja A")
        kpis = self.client.get(reverse("dashboard:home")).context["stock"]
        self.assertEqual(kpis["value"], D("0"))

    def test_csv_export_contains_only_own_data(self):
        response = self.client.get(reverse("products:list") + "?exportar=csv")
        self.assertNotIn("B-1", response.content.decode("utf-8"))

    def test_cannot_reference_other_company_objects_in_forms(self):
        own = make_product(self.a, sku="A-2", stock_qty=3)
        # produto com fornecedor de outra empresa
        response = self.client.post(reverse("products:create"), {
            "name": "Novo", "sku": "NOVO", "unit": "un", "acquisition_cost": "1", "purchase_freight": "0",
            "other_costs": "0", "sale_price": "2", "min_stock": "0", "lead_time_days": "1", "safety_stock": "0",
            "supplier": self.supplier_b.pk, "is_active": "on"})
        self.assertIn("supplier", response.context["form"].errors)
        # venda com produto de outra empresa
        data = {"order_date": timezone.localdate().isoformat(), "channel": "other", "payment_method": "pix",
                "discount": "0", "shipping_charged": "0", "shipping_cost": "0", "commission": "0", "fees": "0",
                "taxes": "0", "customer": self.customer_b.pk, "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "100", "items-0-product": self.product_b.pk,
                "items-0-quantity": "1"}
        response = self.client.post(reverse("sales:create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesOrder.objects.filter(company=self.a).exists())
        # movimentação de estoque com produto de outra empresa
        self.client.post(reverse("inventory:move"), {"product": self.product_b.pk, "kind": "in", "quantity": "5"})
        self.product_b.refresh_from_db()
        self.assertEqual(self.product_b.stock_on_hand, D("5"))
        self.assertEqual(own.stock_on_hand, D("3"))

    def test_same_sku_allowed_in_different_companies(self):
        response = self.client.post(reverse("products:create"), {
            "name": "Mesmo SKU", "sku": "B-1", "unit": "un", "acquisition_cost": "1", "purchase_freight": "0",
            "other_costs": "0", "sale_price": "2", "min_stock": "0", "lead_time_days": "1", "safety_stock": "0",
            "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Product.objects.filter(sku="B-1").count(), 2)

    def test_switching_to_company_without_membership_is_denied(self):
        response = self.client.post(reverse("companies:switch"), {"company_id": self.b.pk})
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(reverse("products:list")), "Produto da Loja B")

    def test_user_with_two_companies_switches_explicitly(self):
        user = make_user()
        Membership.objects.create(user=user, company=self.a, role="admin")
        add_member(self.b, role="admin", user=user)
        self.client.force_login(user)
        self.client.post(reverse("companies:switch"), {"company_id": self.b.pk})
        self.assertContains(self.client.get(reverse("products:list")), "Produto da Loja B")
        self.client.post(reverse("companies:switch"), {"company_id": self.a.pk})
        self.assertNotContains(self.client.get(reverse("products:list")), "Produto da Loja B")

    def test_purchase_and_team_scoping(self):
        self.assertEqual(self.client.get(reverse("purchasing:order_detail", args=[999999])).status_code, 404)
        member_b = Membership.objects.get(company=self.b)
        self.assertEqual(self.client.get(reverse("companies:edit_member", args=[member_b.pk])).status_code, 404)
        self.assertFalse(PurchaseOrder.objects.filter(company=self.a).exists())


class ApiIsolationTests(TestCase):
    def setUp(self):
        self.a = make_company(name="Loja A")
        self.b = make_company(name="Loja B")
        make_product(self.a, sku="A-1", stock_qty=4)
        make_product(self.b, sku="B-1", stock_qty=4)
        _, self.token = ApiToken.issue(self.a, "teste", self.a.owner)

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def test_api_requires_valid_token(self):
        self.assertEqual(self.client.get("/api/v1/produtos/").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/produtos/", HTTP_AUTHORIZATION="Bearer errado").status_code, 401)

    def test_api_returns_only_token_company_data(self):
        data = self.client.get("/api/v1/produtos/", **self.auth()).json()
        self.assertEqual([p["sku"] for p in data["resultados"]], ["A-1"])
        self.assertEqual(self.client.get("/api/v1/produtos/B-1/", **self.auth()).status_code, 404)

    def test_api_creates_order_and_rejects_foreign_sku(self):
        body = {"canal": "shopee", "status": "paid", "referencia": "SH-1",
                "itens": [{"sku": "A-1", "quantidade": "1", "preco_unitario": "20"}]}
        response = self.client.post("/api/v1/pedidos/", json.dumps(body), content_type="application/json", **self.auth())
        self.assertEqual(response.status_code, 201)
        dup = self.client.post("/api/v1/pedidos/", json.dumps(body), content_type="application/json", **self.auth())
        self.assertEqual(dup.status_code, 409)
        body["referencia"] = "SH-2"
        body["itens"][0]["sku"] = "B-1"
        response = self.client.post("/api/v1/pedidos/", json.dumps(body), content_type="application/json", **self.auth())
        self.assertEqual(response.status_code, 400)

    def test_revoked_token_stops_working(self):
        ApiToken.objects.update(is_active=False)
        self.assertEqual(self.client.get("/api/v1/produtos/", **self.auth()).status_code, 401)
        self.assertFalse(ApiToken.objects.filter(key_hash=self.token).exists())  # só o hash é armazenado
