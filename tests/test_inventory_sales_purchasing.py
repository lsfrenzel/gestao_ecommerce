"""Estoque, vendas (reserva, pagamento, cancelamento, devolução) e compras."""

from decimal import Decimal as D

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Warehouse
from apps.finance.models import FinancialEntry
from apps.inventory import services as stock
from apps.inventory.models import StockMovement
from apps.products.models import PriceHistory, Product
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem
from apps.sales.models import SalesOrder
from apps.sales.services import SalesError, change_status, save_order

from .factories import CompanyTestCase, make_product, make_supplier

K = StockMovement.Kind
S = SalesOrder.Status


def refreshed(p: Product) -> Product:
    p.refresh_from_db()
    return p


class StockMovementTests(CompanyTestCase):
    def test_entry_and_exit_generate_history(self):
        p = make_product(self.company)
        stock.move(p, K.IN, 10, user=self.admin, note="entrada")
        stock.move(p, K.OUT, 3, user=self.admin)
        p = refreshed(p)
        self.assertEqual(p.stock_on_hand, D("7"))
        movements = list(StockMovement.objects.filter(product=p).order_by("id"))
        self.assertEqual([m.kind for m in movements], [K.IN, K.OUT])
        self.assertEqual(movements[-1].balance_after, D("7"))
        self.assertEqual(movements[-1].on_hand_delta, D("-3"))
        balance = p.balances.get(warehouse=Warehouse.default_for(self.company))
        self.assertEqual(balance.on_hand, D("7"))

    def test_exit_above_balance_is_rejected(self):
        p = make_product(self.company, stock_qty=2)
        with self.assertRaises(stock.StockError):
            stock.move(p, K.OUT, 5)
        self.assertEqual(refreshed(p).stock_on_hand, D("2"))

    def test_invalid_quantities(self):
        p = make_product(self.company, stock_qty=2)
        for qty in (0, -1, "abc", None):
            with self.assertRaises(stock.StockError):
                stock.move(p, K.IN, qty)

    def test_exit_cannot_consume_reserved_units(self):
        p = make_product(self.company, stock_qty=5)
        stock.reserve(p, 4)
        with self.assertRaises(stock.StockError):
            stock.move(p, K.LOSS, 2, note="avaria")
        stock.move(p, K.LOSS, 1, note="avaria")
        self.assertEqual(refreshed(p).available_stock, D("0"))

    def test_adjust_to_count_and_loss(self):
        p = make_product(self.company, stock_qty=10)
        movement = stock.adjust_to(p, 7, note="inventário")
        self.assertEqual(movement.kind, K.ADJUST_OUT)
        self.assertIsNone(stock.adjust_to(p, 7))
        stock.adjust_to(p, 12)
        self.assertEqual(refreshed(p).stock_on_hand, D("12"))

    def test_transfer_requires_feature_enabled(self):
        p = make_product(self.company, stock_qty=10)
        main = Warehouse.default_for(self.company)
        other = Warehouse.objects.create(company=self.company, name="Loja física")
        with self.assertRaises(stock.StockError):
            stock.transfer(p, 2, main, other)
        self.company.multi_warehouse_enabled = True
        self.company.save()
        p = refreshed(p)
        stock.transfer(p, 4, main, other)
        p = refreshed(p)
        self.assertEqual(p.stock_on_hand, D("10"))
        self.assertEqual(p.balances.get(warehouse=other).on_hand, D("4"))
        self.assertEqual(p.balances.get(warehouse=main).on_hand, D("6"))

    def test_database_rejects_negative_stock(self):
        p = make_product(self.company)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=p.pk).update(stock_on_hand=D("-1"))

    def test_product_form_cannot_change_stock_directly(self):
        p = make_product(self.company, stock_qty=5)
        data = {"name": p.name, "sku": p.sku, "unit": "un", "acquisition_cost": "10", "purchase_freight": "0",
                "other_costs": "0", "sale_price": "25", "min_stock": "0", "lead_time_days": "7", "safety_stock": "0",
                "is_active": "on", "stock_on_hand": "999"}
        self.client.post(reverse("products:edit", args=[p.pk]), data)
        self.assertEqual(refreshed(p).stock_on_hand, D("5"))

    def test_manual_movement_view(self):
        p = make_product(self.company)
        response = self.client.post(reverse("inventory:move"), {"product": p.pk, "kind": K.IN, "quantity": "8"})
        self.assertRedirects(response, reverse("inventory:history"))
        self.assertEqual(refreshed(p).stock_on_hand, D("8"))
        response = self.client.post(reverse("inventory:move"), {"product": p.pk, "kind": K.OUT, "quantity": "50"},
                                    follow=True)
        self.assertContains(response, "Saldo insuficiente")


class SalesFlowTests(CompanyTestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(self.company, cost="10", price="30", stock_qty=10)

    def order(self, status=S.PENDING, qty=2, **extra):
        order = SalesOrder(company=self.company, order_date=timezone.localdate(), status=status, **extra)
        return save_order(order, [{"product": self.product, "quantity": qty, "unit_price": D("30")}], user=self.admin)

    def test_pending_order_reserves_stock_and_is_not_revenue(self):
        order = self.order()
        p = refreshed(self.product)
        self.assertEqual(order.stock_state, SalesOrder.StockState.RESERVED)
        self.assertEqual((p.stock_on_hand, p.stock_reserved, p.available_stock), (D("10"), D("2"), D("8")))
        self.assertFalse(FinancialEntry.objects.filter(sales_order=order).exists())
        self.assertEqual(order.total, D("60.00"))
        self.assertEqual(order.cogs, D("20.00"))

    def test_payment_deducts_stock_and_records_revenue(self):
        order = self.order(commission=D("5"), fees=D("2"))
        change_status(order, S.PAID, user=self.admin)
        p = refreshed(self.product)
        self.assertEqual((p.stock_on_hand, p.stock_reserved), (D("8"), D("0")))
        order.refresh_from_db()
        self.assertEqual(order.stock_state, SalesOrder.StockState.DEDUCTED)
        receivable = FinancialEntry.objects.get(sales_order=order, type=FinancialEntry.Type.RECEIVABLE)
        self.assertEqual(receivable.amount, D("60.00"))
        self.assertEqual(receivable.status, FinancialEntry.Status.SETTLED)
        self.assertTrue(FinancialEntry.objects.filter(sales_order=order, amount=D("7")).exists())
        self.assertEqual(order.contribution_margin, D("33.00"))

    def test_order_created_as_paid(self):
        self.order(status=S.PAID, qty=3)
        self.assertEqual(refreshed(self.product).stock_on_hand, D("7"))

    def test_cancel_pending_releases_reservation(self):
        order = self.order()
        change_status(order, S.CANCELLED, user=self.admin)
        p = refreshed(self.product)
        self.assertEqual((p.stock_on_hand, p.stock_reserved), (D("10"), D("0")))
        self.assertTrue(StockMovement.objects.filter(sales_order=order, kind=K.RELEASE).exists())

    def test_cancel_paid_restores_stock_and_registers_refund(self):
        order = self.order(status=S.PAID)
        change_status(order, S.CANCELLED, user=self.admin)
        self.assertEqual(refreshed(self.product).stock_on_hand, D("10"))
        self.assertTrue(StockMovement.objects.filter(sales_order=order, kind=K.SALE_CANCEL).exists())
        refund = FinancialEntry.objects.get(sales_order=order, category__name__startswith="Estornos")
        self.assertEqual(refund.amount, D("60.00"))

    def test_return_with_and_without_restock(self):
        order = self.order(status=S.PAID)
        change_status(order, S.DELIVERED)
        change_status(order, S.RETURNED, restock=True)
        self.assertEqual(refreshed(self.product).stock_on_hand, D("10"))
        order2 = self.order(status=S.PAID)
        change_status(order2, S.RETURNED, restock=False)
        self.assertEqual(refreshed(self.product).stock_on_hand, D("8"))

    def test_invalid_transitions(self):
        order = self.order()
        with self.assertRaises(SalesError):
            change_status(order, S.DELIVERED)
        change_status(order, S.CANCELLED)
        with self.assertRaises(SalesError):
            change_status(order, S.PAID)

    def test_insufficient_stock_blocks_order(self):
        with self.assertRaises(SalesError):
            self.order(qty=50)
        self.assertFalse(SalesOrder.objects.exists())
        self.assertEqual(refreshed(self.product).stock_reserved, D("0"))

    def test_reserved_units_are_not_sold_twice(self):
        self.order(qty=8)
        with self.assertRaises(SalesError):
            self.order(qty=3)

    def test_editing_items_rebalances_reservation(self):
        order = self.order(qty=2)
        save_order(order, [{"product": self.product, "quantity": 5, "unit_price": D("30")}])
        self.assertEqual(refreshed(self.product).stock_reserved, D("5"))

    def test_paid_order_items_cannot_change(self):
        order = self.order(status=S.PAID)
        with self.assertRaises(SalesError):
            save_order(order, [{"product": self.product, "quantity": 1, "unit_price": D("30")}])

    def test_discount_above_total_is_rejected(self):
        order = SalesOrder(company=self.company, order_date=timezone.localdate(), discount=D("500"))
        with self.assertRaises(SalesError):
            save_order(order, [{"product": self.product, "quantity": 1, "unit_price": D("30")}])

    def test_order_numbers_are_sequential_per_company(self):
        a, b = self.order(qty=1), self.order(qty=1)
        self.assertEqual(b.number, a.number + 1)

    def test_create_order_via_view(self):
        data = {"order_date": timezone.localdate().isoformat(), "channel": "shopee", "payment_method": "pix",
                "initial_status": "paid", "discount": "0", "shipping_charged": "0", "shipping_cost": "0",
                "commission": "0", "fees": "0", "taxes": "0",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "100", "items-0-product": self.product.pk, "items-0-quantity": "1",
                "items-0-unit_price": "", "items-0-discount": ""}
        response = self.client.post(reverse("sales:create"), data)
        order = SalesOrder.objects.get()
        self.assertRedirects(response, reverse("sales:detail", args=[order.pk]))
        self.assertEqual(order.status, S.PAID)
        self.assertEqual(order.total, D("30.00"))  # preço cadastrado usado quando vazio

    def test_csv_import_orders(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        csv = ("pedido;data;canal;status;cliente;email;sku;quantidade;preco_unitario\n"
               f"ML-1;01/10/2026;mercado livre;pago;Ana;ana@exemplo.test;{self.product.sku};2;29,90\n"
               f"ML-1;01/10/2026;mercado livre;pago;Ana;ana@exemplo.test;{self.product.sku};1;29,90\n"
               "ML-2;01/10/2026;shopee;pago;Bia;;INEXISTENTE;1;10\n").encode()
        response = self.client.post(reverse("sales:import"), {"file": SimpleUploadedFile("p.csv", csv)})
        result = response.context["result"]
        self.assertEqual(result["created"], 0)  # ML-1 tem o mesmo SKU duas vezes → rejeitado
        self.assertEqual(len(result["errors"]), 2)
        csv_ok = ("pedido;data;canal;status;sku;quantidade;preco_unitario\n"
                  f"ML-3;01/10/2026;mercado livre;entregue;{self.product.sku};2;29,90\n").encode()
        response = self.client.post(reverse("sales:import"), {"file": SimpleUploadedFile("p.csv", csv_ok)})
        self.assertEqual(response.context["result"]["created"], 1)
        order = SalesOrder.objects.get(external_reference="ML-3")
        self.assertEqual(order.status, S.DELIVERED)
        self.assertEqual(refreshed(self.product).stock_on_hand, D("8"))
        # reimportação do mesmo pedido é ignorada
        response = self.client.post(reverse("sales:import"), {"file": SimpleUploadedFile("p.csv", csv_ok)})
        self.assertEqual(response.context["result"]["created"], 0)


class PurchasingTests(CompanyTestCase):
    def test_partial_and_total_receipt_with_weighted_cost(self):
        supplier = make_supplier(self.company)
        product = make_product(self.company, cost="10", stock_qty=10)
        order = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=1,
                                             order_date=timezone.localdate(), freight=D("20"))
        item = PurchaseOrderItem.objects.create(order=order, product=product, quantity=20, unit_cost=D("14"))
        with self.assertRaises(purchasing.PurchaseError):
            purchasing.receive(order, {item.pk: 5})  # ainda em rascunho
        purchasing.mark_ordered(order)
        purchasing.receive(order, {item.pk: 10}, user=self.admin)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.PARTIAL)
        product = refreshed(product)
        self.assertEqual(product.stock_on_hand, D("20"))
        # custo médio: (10×10 + 10×14) / 20 = 12; frete rateado 20/280 × 14 = 1 por unidade → (0 + 10×1)/20 = 0,5
        self.assertEqual(product.acquisition_cost, D("12.0000"))
        self.assertEqual(product.purchase_freight, D("0.5000"))
        self.assertTrue(PriceHistory.objects.filter(product=product, reason__icontains="custo médio").exists())
        payable = FinancialEntry.objects.get(purchase_order=order)
        self.assertEqual(payable.amount, D("150.00"))  # 10 × (14 + 1)
        with self.assertRaises(purchasing.PurchaseError):
            purchasing.receive(order, {item.pk: 11})  # acima do pendente
        purchasing.receive(order, {item.pk: 10})
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(StockMovement.objects.filter(purchase_order=order, kind=K.PURCHASE).count(), 2)

    def test_cancel_rules(self):
        supplier = make_supplier(self.company)
        product = make_product(self.company)
        order = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=1,
                                             order_date=timezone.localdate())
        item = PurchaseOrderItem.objects.create(order=order, product=product, quantity=5, unit_cost=D("1"))
        purchasing.mark_ordered(order)
        purchasing.receive(order, {item.pk: 1})
        with self.assertRaises(purchasing.PurchaseError):
            purchasing.cancel(order)
