from __future__ import annotations

from django.db import transaction

from apps.core.audit import log_action

from .models import Company, Membership, Plan, Warehouse


class PlanLimitError(Exception):
    pass


@transaction.atomic
def create_company(user, data: dict, request=None) -> Company:
    company = Company(**data, owner=user)
    company.start_trial(Plan.default())
    company.save()
    Membership.objects.create(user=user, company=company, role=Membership.Role.ADMIN)
    Warehouse.objects.create(company=company, name="Principal", is_default=True)

    from apps.finance.services import ensure_default_categories

    ensure_default_categories(company)
    log_action("company.create", request=request, company=company, target=company)
    return company


def check_user_limit(company: Company) -> None:
    plan = company.plan
    if plan and plan.max_users is not None:
        active = Membership.objects.filter(company=company, is_active=True).count()
        if active >= plan.max_users:
            raise PlanLimitError(f"O plano {plan.name} permite até {plan.max_users} usuário(s) ativo(s).")


def check_product_limit(company: Company) -> None:
    from apps.products.models import Product

    plan = company.plan
    if plan and plan.max_products is not None:
        if Product.objects.for_company(company).count() >= plan.max_products:
            raise PlanLimitError(f"O plano {plan.name} permite até {plan.max_products} produto(s).")
