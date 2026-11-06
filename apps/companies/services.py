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


class CouponError(Exception):
    """Cupom inválido para o resgate (mensagem amigável)."""


def _add_months(start, months: int):
    import calendar

    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


@transaction.atomic
def redeem_coupon(company: Company, code: str, *, user=None, request=None, by_platform: bool = False,
                  replace: bool = False):
    """
    Aplica um cupom à empresa. Uma empresa tem no máximo um cupom em vigor; com `replace=True`
    (apenas pela plataforma) o cupom atual é substituído.
    """
    from django.utils import timezone

    from .models import Coupon, CouponRedemption

    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Informe o código do cupom.")
    coupon = Coupon.objects.select_for_update().filter(code=code).first()
    if coupon is None:
        raise CouponError("Cupom não encontrado.")
    reason = coupon.unavailable_reason(company)
    if reason:
        raise CouponError(reason)
    today = timezone.localdate()
    # Encerra resgates cujo prazo de desconto já terminou.
    CouponRedemption.objects.filter(company=company, is_active=True, expires_at__lt=today).update(
        is_active=False, removed_at=timezone.now())
    current = CouponRedemption.objects.filter(company=company, is_active=True).first()
    if current is not None:
        if not replace:
            raise CouponError(f"A empresa já tem o cupom {current.coupon.code} em vigor.")
        current.is_active = False
        current.removed_at = timezone.now()
        current.save(update_fields=["is_active", "removed_at", "updated_at"])
    redemption = CouponRedemption.objects.create(
        coupon=coupon, company=company, redeemed_by=user, applied_by_platform=by_platform,
        expires_at=_add_months(today, coupon.duration_months) if coupon.duration_months else None)
    log_action("coupon.redeem", request=request, actor=user, company=company, target=company,
               platform=by_platform, details={"cupom": coupon.code, "desconto": coupon.discount_percent,
                                              "valido_ate": str(redemption.expires_at or "permanente")})
    return redemption


@transaction.atomic
def remove_coupon(company: Company, *, user=None, request=None) -> bool:
    from django.utils import timezone

    from .models import CouponRedemption

    current = CouponRedemption.objects.select_for_update().filter(company=company, is_active=True).first()
    if current is None:
        return False
    current.is_active = False
    current.removed_at = timezone.now()
    current.save(update_fields=["is_active", "removed_at", "updated_at"])
    log_action("coupon.remove", request=request, actor=user, company=company, target=company, platform=True,
               details={"cupom": current.coupon.code})
    return True


def check_product_limit(company: Company) -> None:
    from apps.products.models import Product

    plan = company.plan
    if plan and plan.max_products is not None:
        if Product.objects.for_company(company).count() >= plan.max_products:
            raise PlanLimitError(f"O plano {plan.name} permite até {plan.max_products} produto(s).")
