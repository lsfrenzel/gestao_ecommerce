"""Construtores simples para os testes (sem dependências externas)."""

from __future__ import annotations

import itertools
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.companies.models import Company, Membership, Plan
from apps.companies.services import create_company
from apps.customers.models import Customer
from apps.inventory import services as stock
from apps.inventory.models import StockMovement
from apps.products.models import Product
from apps.purchasing.models import Supplier

_seq = itertools.count(1)
PASSWORD = "Senha-de-Teste#2026"


def make_user(email=None, password=PASSWORD, **extra) -> User:
    n = next(_seq)
    return User.objects.create_user(email=email or f"user{n}@exemplo.test", password=password,
                                    full_name=extra.pop("full_name", f"Usuário {n}"), **extra)


def make_company(owner=None, name=None, plan=None, **settings) -> Company:
    owner = owner or make_user()
    company = create_company(owner, {"trade_name": name or f"Empresa {next(_seq)}"})
    if plan is not None:
        company.plan = plan
    for key, value in settings.items():
        setattr(company, key, value)
    company.save()
    return company


def add_member(company, role=Membership.Role.OPERATOR, modules=None, user=None) -> User:
    user = user or make_user()
    Membership.objects.create(user=user, company=company, role=role, modules=modules or [])
    return user


def make_product(company, sku=None, cost="10.00", price="25.00", stock_qty=0, **extra) -> Product:
    product = Product.objects.create(company=company, sku=sku or f"SKU-{next(_seq)}", name=extra.pop("name", "Produto"),
                                     acquisition_cost=Decimal(cost), sale_price=Decimal(price), **extra)
    if stock_qty:
        stock.move(product, StockMovement.Kind.INITIAL, stock_qty)
        product.refresh_from_db()
    return product


def make_supplier(company, name="Fornecedor") -> Supplier:
    return Supplier.objects.create(company=company, name=name)


def make_customer(company, name="Cliente") -> Customer:
    return Customer.objects.create(company=company, name=name, email=f"c{next(_seq)}@exemplo.test")


def full_plan(**extra) -> Plan:
    from apps.companies.models import PLAN_FEATURES

    return Plan.objects.create(name=extra.pop("name", f"Plano {next(_seq)}"),
                               features=[k for k, _ in PLAN_FEATURES], **extra)


class CompanyTestCase(TestCase):
    """Empresa com administrador logado."""

    def setUp(self):
        self.admin = make_user()
        self.company = make_company(self.admin)
        self.client.force_login(self.admin)
