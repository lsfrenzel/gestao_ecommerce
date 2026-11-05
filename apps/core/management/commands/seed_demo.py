"""
Cria uma empresa de DEMONSTRAÇÃO com dados fictícios, claramente identificada.

Uso:  python manage.py seed_demo --email demo@exemplo.com --password "<senha forte>"

- Opcional e explícito: nunca é executado automaticamente no deploy.
- A empresa se chama “[DEMONSTRAÇÃO] Loja Exemplo” e todos os registros usam o prefixo DEMO.
- Todas as movimentações passam pelos serviços reais (estoque, compras, vendas e financeiro).
- Para remover: python manage.py seed_demo --remove
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.companies.models import Company, Membership
from apps.companies.services import create_company
from apps.customers.models import Customer
from apps.finance.models import FinancialCategory, FixedExpense, MonthlyGoal, Nature
from apps.finance.services import generate_fixed_expenses
from apps.inventory import services as stock
from apps.inventory.models import StockMovement
from apps.marketing.models import Campaign
from apps.products.models import Category, Product
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem, Supplier
from apps.purchasing.services import mark_ordered, new_order_number, receive
from apps.sales.models import Channel, SalesOrder
from apps.sales.services import SalesError, change_status, save_order

DEMO_NAME = "[DEMONSTRAÇÃO] Loja Exemplo"

PRODUCTS = [
    ("DEMO-CAM-P", "Camiseta básica preta", "Vestuário", "25.00", "2.00", "79.90", 60, 10),
    ("DEMO-CAM-B", "Camiseta básica branca", "Vestuário", "24.00", "2.00", "79.90", 45, 10),
    ("DEMO-MOL", "Moletom canguru cinza", "Vestuário", "68.00", "4.00", "189.90", 20, 5),
    ("DEMO-BON", "Boné aba curva", "Acessórios", "18.00", "1.50", "59.90", 35, 8),
    ("DEMO-MEI", "Kit 3 meias cano médio", "Acessórios", "12.00", "1.00", "39.90", 80, 15),
    ("DEMO-GAR", "Garrafa térmica 500 ml", "Casa", "32.00", "3.00", "89.90", 12, 6),
    ("DEMO-CAN", "Caneca cerâmica 350 ml", "Casa", "11.00", "2.00", "34.90", 4, 10),
    ("DEMO-ECO", "Ecobag algodão cru", "Acessórios", "9.00", "0.80", "19.90", 150, 20),
]


class Command(BaseCommand):
    help = "Cria (ou remove) uma empresa de demonstração com dados fictícios identificados."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="E-mail do administrador da empresa demo.")
        parser.add_argument("--password", help="Senha do administrador demo (não é armazenada em texto).")
        parser.add_argument("--remove", action="store_true", help="Remove a empresa de demonstração.")

    def handle(self, *args, **opts):
        if opts["remove"]:
            deleted = Company.objects.filter(trade_name=DEMO_NAME)
            count = deleted.count()
            for company in deleted:
                self._delete_company(company)
            self.stdout.write(self.style.SUCCESS(f"{count} empresa(s) de demonstração removida(s)."))
            return
        if Company.objects.filter(trade_name=DEMO_NAME).exists():
            raise CommandError("A empresa de demonstração já existe. Use --remove antes de recriar.")
        email, password = opts.get("email"), opts.get("password")
        if not email or not password:
            raise CommandError("Informe --email e --password para o administrador da empresa de demonstração.")
        User = get_user_model()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            try:
                validate_password(password, User(email=email, full_name="Usuário Demonstração"))
            except ValidationError as exc:
                raise CommandError("Senha fraca: " + " ".join(exc.messages)) from exc
            user = User.objects.create_user(email=email, password=password, full_name="Usuário Demonstração")
        random.seed(42)
        with transaction.atomic():
            self._build(user)
        self.stdout.write(self.style.SUCCESS(f"Empresa “{DEMO_NAME}” criada para {email}."))

    def _delete_company(self, company):
        from apps.core.models import AuditLog
        from apps.finance.models import FinancialEntry
        from apps.pricing.models import PricingSimulation
        from apps.products.models import PriceHistory
        from apps.sales.models import SalesOrderItem

        with transaction.atomic():
            AuditLog.objects.filter(company=company).delete()
            FinancialEntry.objects.filter(company=company).delete()
            StockMovement.objects.filter(company=company).delete()
            SalesOrderItem.objects.filter(order__company=company).delete()
            SalesOrder.objects.filter(company=company).delete()
            PurchaseOrderItem.objects.filter(order__company=company).delete()
            PurchaseOrder.objects.filter(company=company).delete()
            PricingSimulation.objects.filter(company=company).delete()
            PriceHistory.objects.filter(product__company=company).delete()
            from apps.inventory.models import StockBalance
            StockBalance.objects.filter(product__company=company).delete()
            Product.objects.filter(company=company).delete()
            company.warehouses.all().delete()
            company.delete()

    def _build(self, user):
        company = create_company(user, {"trade_name": DEMO_NAME, "segment": "moda",
                                        "email": "contato@exemplo.com"})
        company.default_tax_rate = Decimal("6")
        company.save()
        today = timezone.localdate()

        supplier = Supplier.objects.create(company=company, name="DEMO Fornecedor Têxtil Ltda.",
                                           contact_name="Contato Exemplo", default_lead_time_days=10)
        supplier2 = Supplier.objects.create(company=company, name="DEMO Distribuidora Utilidades",
                                            default_lead_time_days=7)
        categories = {}
        products = []
        for sku, name, cat, cost, freight, price, initial, min_stock in PRODUCTS:
            if cat not in categories:
                categories[cat] = Category.objects.create(company=company, name=cat)
            p = Product.objects.create(
                company=company, sku=sku, name=name, category=categories[cat], brand="Marca Demo",
                supplier=supplier if cat == "Vestuário" else supplier2, acquisition_cost=Decimal(cost),
                purchase_freight=Decimal(freight), sale_price=Decimal(price), min_stock=min_stock,
                max_stock=min_stock * 12, lead_time_days=10, safety_stock=min_stock // 2,
                description="Produto fictício de demonstração.")
            stock.move(p, StockMovement.Kind.INITIAL, initial, user=user, note="DEMO saldo inicial")
            products.append(p)

        customers = [Customer.objects.create(company=company, name=f"Cliente Demo {i:02d}",
                                             email=f"cliente{i:02d}@exemplo.com") for i in range(1, 16)]
        campaign = Campaign.objects.create(
            company=company, name="DEMO Lançamento coleção", channel="meta_ads", investment=Decimal("600"),
            start_date=today - timedelta(days=45), end_date=today - timedelta(days=15), clicks=2400,
            visitors=1900, orders=38, new_customers=22, attributed_revenue=Decimal("3150"))

        # Compra recebida há 60 dias
        po = PurchaseOrder.objects.create(company=company, supplier=supplier, number=new_order_number(company),
                                          order_date=today - timedelta(days=62), freight=Decimal("120"),
                                          created_by=user)
        PurchaseOrderItem.objects.create(order=po, product=products[0], quantity=40, unit_cost=Decimal("24.50"))
        PurchaseOrderItem.objects.create(order=po, product=products[1], quantity=40, unit_cost=Decimal("23.50"))
        mark_ordered(po)
        receive(po, {i.pk: i.quantity for i in po.items.all()}, user=user)

        channels = [Channel.OWN_STORE, Channel.MERCADO_LIVRE, Channel.SHOPEE, Channel.INSTAGRAM, Channel.WHATSAPP]
        for day in range(90, 0, -1):
            for _ in range(random.randint(0, 3)):
                product = random.choice(products)
                product.refresh_from_db()
                if product.available_stock < 3:
                    continue
                channel = random.choice(channels)
                qty = random.randint(1, 2)
                total = product.sale_price * qty
                marketplace = channel in (Channel.MERCADO_LIVRE, Channel.SHOPEE)
                order = SalesOrder(company=company, order_date=today - timedelta(days=day),
                                   customer=random.choice(customers), channel=channel,
                                   status=SalesOrder.Status.PAID,
                                   commission=(total * Decimal("0.14")).quantize(Decimal("0.01")) if marketplace else 0,
                                   fees=(total * Decimal("0.0399")).quantize(Decimal("0.01")) if not marketplace else 0,
                                   shipping_cost=Decimal("12.90") if marketplace else 0,
                                   campaign=campaign if 15 <= day <= 45 and random.random() < .4 else None)
                try:
                    save_order(order, [{"product": product, "quantity": qty, "unit_price": product.sale_price}],
                               user=user, auto_taxes=True)
                    if day > 5:
                        for step in (SalesOrder.Status.SEPARATING, SalesOrder.Status.SHIPPED, SalesOrder.Status.DELIVERED):
                            change_status(order, step, user=user)
                except SalesError:
                    continue
        # Pedidos em aberto (reservam estoque) e um cancelado
        top = list(Product.objects.filter(company=company).order_by("-stock_on_hand")[:2])
        for status in (SalesOrder.Status.PENDING, SalesOrder.Status.AWAITING_PAYMENT):
            order = SalesOrder(company=company, order_date=today, customer=customers[0], status=status,
                               channel=Channel.OWN_STORE)
            save_order(order, [{"product": top[0], "quantity": 1, "unit_price": top[0].sale_price}],
                       user=user, auto_taxes=True)
        cancelled = SalesOrder(company=company, order_date=today - timedelta(days=3), customer=customers[1],
                               status=SalesOrder.Status.PAID, channel=Channel.WHATSAPP)
        save_order(cancelled, [{"product": top[1], "quantity": 1, "unit_price": top[1].sale_price}],
                   user=user, auto_taxes=True)
        change_status(cancelled, SalesOrder.Status.CANCELLED, user=user, note="DEMO cancelamento")

        fixed_cat = {c.name: c for c in FinancialCategory.objects.filter(company=company, nature=Nature.FIXED)}
        for name, cat, value in [("Aluguel do estoque", "Aluguel", "1200"), ("Internet", "Internet e telefone", "120"),
                                 ("Energia", "Energia", "180"), ("Pró-labore", "Pró-labore", "2500"),
                                 ("ERP e ferramentas", "Sistemas e assinaturas", "150"),
                                 ("Contador", "Contabilidade", "400")]:
            FixedExpense.objects.create(company=company, name=name, category=fixed_cat[cat],
                                        monthly_amount=Decimal(value))
        for back in (2, 1, 0):
            month = (today.replace(day=1) - timedelta(days=28 * back)).replace(day=1)
            generate_fixed_expenses(company, month, user=user)
        MonthlyGoal.objects.create(company=company, month=today.replace(day=1), revenue_target=Decimal("6000"),
                                   profit_target=Decimal("800"))
        Membership.objects.filter(company=company, user=user).update(role=Membership.Role.ADMIN)

        # Datas históricas coerentes: liquidação financeira e movimentações na data da venda.
        from datetime import datetime, time

        from apps.finance.models import FinancialEntry

        for order in SalesOrder.objects.filter(company=company).exclude(order_date=today):
            moment = timezone.make_aware(datetime.combine(order.order_date, time(12)))
            FinancialEntry.objects.filter(sales_order=order, status=FinancialEntry.Status.SETTLED).update(
                settled_at=order.order_date, due_date=order.order_date)
            StockMovement.objects.filter(sales_order=order).update(created_at=moment)
            SalesOrder.objects.filter(pk=order.pk).update(paid_at=moment)
        # Impostos de meses anteriores já recolhidos (evita alertas artificiais de vencidos na demonstração)
        for entry in FinancialEntry.objects.filter(company=company, status=FinancialEntry.Status.OPEN,
                                                   sales_order__isnull=False, due_date__lt=today):
            entry.status, entry.settled_at, entry.settled_amount = (FinancialEntry.Status.SETTLED, entry.due_date,
                                                                    entry.amount)
            entry.save()
        moment = timezone.make_aware(datetime.combine(po.order_date + timedelta(days=8), time(10)))
        StockMovement.objects.filter(purchase_order=po).update(created_at=moment)
        FinancialEntry.objects.filter(purchase_order=po).update(
            settled_at=po.order_date + timedelta(days=8), status=FinancialEntry.Status.SETTLED,
            settled_amount=po.total, due_date=po.order_date + timedelta(days=8))
