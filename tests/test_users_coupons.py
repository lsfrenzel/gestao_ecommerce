"""Cadastro direto com senha provisória e cupons de desconto na assinatura."""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.password_validation import validate_password
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import generate_provisional_password
from apps.companies.models import Company, Coupon, CouponRedemption, Membership, Plan
from apps.companies.services import CouponError, redeem_coupon
from apps.core.models import AuditLog

from .factories import PASSWORD, make_company, make_user

NEW_PASSWORD = "Minha-Senha#2026"


def provisional_from(response) -> str:
    return re.search(r'id="provisional-password" value="([^"]+)"', response.content.decode()).group(1)


class ProvisionalPasswordTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@exemplo.test", password=PASSWORD, full_name="Root")
        self.company = make_company()
        self.client.force_login(self.root)

    def test_generated_password_is_strong(self):
        for _ in range(20):
            pwd = generate_provisional_password()
            self.assertRegex(pwd, r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$")
            validate_password(pwd, User(email="x@exemplo.test", full_name="X"))

    def test_create_user_directly_and_force_password_change(self):
        response = self.client.post(reverse("platform:user_create"), {
            "full_name": "Carla Souza", "email": "Carla@Exemplo.test", "company": self.company.pk, "role": "manager"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        password = provisional_from(response)
        user = User.objects.get(email="carla@exemplo.test")
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(password))
        self.assertNotEqual(user.password, password)
        self.assertEqual(Membership.objects.get(user=user).role, "manager")
        log = AuditLog.objects.get(action="platform.user_create")
        self.assertNotIn(password, str(log.details))
        # a senha não aparece novamente na ficha do usuário
        self.assertNotContains(self.client.get(reverse("platform:user_detail", args=[user.pk])), password)

        # primeiro acesso: login funciona, mas tudo leva à troca de senha
        other = self.client_class()
        other.post(reverse("accounts:login"), {"email": "carla@exemplo.test", "password": password})
        self.assertRedirects(other.get(reverse("products:list")), reverse("accounts:password_change"))
        self.assertContains(other.get(reverse("accounts:password_change")), "Senha provisória")
        response = other.post(reverse("accounts:password_change"), {
            "old_password": password, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertEqual(other.get(reverse("products:list")).status_code, 200)

    def test_validation_duplicate_email_and_plan_limit(self):
        make_user(email="existe@exemplo.test")
        response = self.client.post(reverse("platform:user_create"), {"full_name": "X", "email": "existe@exemplo.test"})
        self.assertIn("email", response.context["form"].errors)
        self.company.plan = Plan.objects.create(name="Mini", max_users=1, features=[])
        self.company.save()
        response = self.client.post(reverse("platform:user_create"), {"full_name": "Y", "email": "y@exemplo.test",
                                                                      "company": self.company.pk, "role": "operator"})
        self.assertIn("company", response.context["form"].errors)
        self.assertFalse(User.objects.filter(email="y@exemplo.test").exists())

    def test_create_without_company(self):
        response = self.client.post(reverse("platform:user_create"), {"full_name": "Sem Empresa",
                                                                      "email": "solo@exemplo.test"})
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="solo@exemplo.test")
        self.assertFalse(user.memberships.exists())

    def test_regenerate_provisional_password(self):
        user = make_user(email="esqueceu@exemplo.test")
        response = self.client.post(reverse("platform:user_provisional_password", args=[user.pk]))
        password = provisional_from(response)
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))
        self.assertFalse(user.check_password(PASSWORD))
        self.assertTrue(user.must_change_password)
        # não vale para a própria conta nem para conta bloqueada
        self.client.post(reverse("platform:user_provisional_password", args=[self.root.pk]))
        self.root.refresh_from_db()
        self.assertFalse(self.root.must_change_password)
        user.is_active = False
        user.save()
        response = self.client.post(reverse("platform:user_provisional_password", args=[user.pk]))
        self.assertEqual(response.status_code, 302)

    def test_password_reset_link_clears_flag(self):
        user = make_user(email="link@exemplo.test")
        user.must_change_password = True
        user.save()
        from apps.accounts.services import password_link

        path = "/" + password_link(user).split("://", 1)[1].split("/", 1)[1]
        client = self.client_class()
        response = client.get(path, follow=True)
        client.post(response.redirect_chain[-1][0], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)

    def test_only_superadmin(self):
        owner = self.company.owner
        self.client.force_login(owner)
        for name in ("platform:user_create", "platform:coupons", "platform:coupon_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.post(reverse("platform:user_provisional_password", args=[owner.pk])).status_code,
                         403)


class CouponTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@exemplo.test", password=PASSWORD, full_name="Root")
        self.plan = Plan.objects.create(name="Pro", monthly_price=D("129.90"), features=[])
        self.company = make_company(plan=self.plan)

    def coupon(self, **extra):
        data = {"code": "METADE", "discount_percent": 50}
        data.update(extra)
        return Coupon.objects.create(**data)

    def test_create_coupon_via_panel(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse("platform:coupon_new"), {
            "code": "bemvindo50", "description": "Lançamento", "discount_percent": "50", "duration_months": "3",
            "valid_until": "", "max_redemptions": "10", "plans": [self.plan.pk], "is_active": "on"})
        coupon = Coupon.objects.get()
        self.assertRedirects(response, reverse("platform:coupon_detail", args=[coupon.pk]))
        self.assertEqual((coupon.code, coupon.discount_percent, coupon.duration_months), ("BEMVINDO50", 50, 3))
        self.assertEqual(list(coupon.plans.all()), [self.plan])
        # código automático e somente 50% ou 100%
        self.client.post(reverse("platform:coupon_new"), {"code": "", "discount_percent": "100", "is_active": "on"})
        self.assertTrue(Coupon.objects.filter(code__startswith="FT-", discount_percent=100).exists())
        response = self.client.post(reverse("platform:coupon_new"), {"code": "TRINTA", "discount_percent": "30",
                                                                     "is_active": "on"})
        self.assertIn("discount_percent", response.context["form"].errors)
        response = self.client.post(reverse("platform:coupon_new"), {"code": "BemVindo50", "discount_percent": "50",
                                                                     "is_active": "on"})
        self.assertIn("code", response.context["form"].errors)
        self.assertEqual(self.client.get(reverse("platform:coupons")).status_code, 200)
        self.assertEqual(self.client.get(reverse("platform:coupon_detail", args=[coupon.pk])).status_code, 200)

    def test_prices_with_50_and_100_percent(self):
        self.assertEqual(self.company.effective_monthly_price, D("129.90"))
        self.coupon()
        redeem_coupon(self.company, "metade")
        self.assertEqual(self.company.effective_monthly_price, D("64.95"))
        self.assertEqual(self.company.discount_percent, 50)
        other = make_company(plan=self.plan)
        redeem_coupon(other, self.coupon(code="GRATIS", discount_percent=100).code)
        self.assertEqual(other.effective_monthly_price, D("0.00"))

    def test_redeem_rules(self):
        coupon = self.coupon(max_redemptions=1)
        redeem_coupon(self.company, coupon.code)
        with self.assertRaisesMessage(CouponError, "já tem o cupom"):
            redeem_coupon(self.company, self.coupon(code="OUTRO").code)
        with self.assertRaisesMessage(CouponError, "limite de usos"):
            redeem_coupon(make_company(plan=self.plan), coupon.code)
        with self.assertRaisesMessage(CouponError, "não encontrado"):
            redeem_coupon(make_company(), "NAOEXISTE")
        with self.assertRaisesMessage(CouponError, "expirou"):
            redeem_coupon(make_company(), self.coupon(code="VELHO", valid_until=timezone.localdate()
                                                      - timedelta(days=1)).code)
        with self.assertRaisesMessage(CouponError, "desativado"):
            redeem_coupon(make_company(), self.coupon(code="OFF", is_active=False).code)
        restricted = self.coupon(code="SOPRO")
        restricted.plans.add(self.plan)
        with self.assertRaisesMessage(CouponError, "plano"):
            redeem_coupon(make_company(plan=Plan.objects.create(name="Outro", features=[])), restricted.code)

    def test_duration_ends_discount(self):
        redemption = redeem_coupon(self.company, self.coupon(duration_months=2).code)
        today = timezone.localdate()
        self.assertGreater(redemption.expires_at, today + timedelta(days=55))
        CouponRedemption.objects.filter(pk=redemption.pk).update(expires_at=today - timedelta(days=1))
        self.assertEqual(self.company.discount_percent, 0)
        self.assertEqual(self.company.effective_monthly_price, D("129.90"))
        # expirado não bloqueia um novo cupom
        redeem_coupon(self.company, self.coupon(code="NOVO").code)
        self.assertEqual(self.company.discount_percent, 50)

    def test_company_redeems_on_subscription_page(self):
        self.coupon()
        self.client.force_login(self.company.owner)
        page = self.client.get(reverse("companies:subscription"))
        self.assertContains(page, "129,90")
        response = self.client.post(reverse("companies:subscription"), {"code": "metade"})
        self.assertRedirects(response, reverse("companies:subscription"))
        self.assertContains(self.client.get(reverse("companies:subscription")), "64,95")
        response = self.client.post(reverse("companies:subscription"), {"code": "XYZ"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CouponRedemption.objects.filter(company=self.company).count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="coupon.redeem", company=self.company).exists())

    def test_operator_cannot_redeem(self):
        from .factories import add_member

        self.coupon()
        self.client.force_login(add_member(self.company))
        self.assertEqual(self.client.post(reverse("companies:subscription"), {"code": "METADE"}).status_code, 403)
        self.assertFalse(CouponRedemption.objects.exists())

    def test_platform_apply_replace_and_remove(self):
        self.client.force_login(self.root)
        self.coupon()
        self.coupon(code="GRATIS", discount_percent=100)
        url = reverse("platform:company_coupon", args=[self.company.pk])
        self.client.post(url, {"code": "METADE"})
        self.assertEqual(self.company.discount_percent, 50)
        self.client.post(url, {"code": "GRATIS"})  # sem substituir: mantém o atual
        self.assertEqual(self.company.discount_percent, 50)
        self.client.post(url, {"code": "GRATIS", "replace": "on"})
        self.assertEqual(self.company.discount_percent, 100)
        self.assertTrue(CouponRedemption.objects.get(coupon__code="GRATIS").applied_by_platform)
        detail = self.client.get(reverse("platform:company_detail", args=[self.company.pk]))
        self.assertContains(detail, "GRATIS")
        self.client.post(url, {"action": "remover"})
        self.assertEqual(self.company.discount_percent, 0)
        self.assertTrue(AuditLog.objects.filter(action="coupon.remove").exists())
        self.assertContains(self.client.get(reverse("platform:companies")), "129,90")

    def test_coupon_toggle_keeps_existing_discounts(self):
        coupon = self.coupon()
        redeem_coupon(self.company, coupon.code)
        self.client.force_login(self.root)
        self.client.post(reverse("platform:coupon_toggle", args=[coupon.pk]))
        coupon.refresh_from_db()
        self.assertFalse(coupon.is_active)
        self.assertEqual(self.company.discount_percent, 50)
        with self.assertRaises(CouponError):
            redeem_coupon(make_company(), coupon.code)

    def test_onboarding_with_coupon(self):
        Plan.objects.filter(pk=self.plan.pk).update(is_default=True)
        self.coupon(code="ENTRADA", discount_percent=100)
        user = make_user()
        self.client.force_login(user)
        response = self.client.post(reverse("companies:onboarding"), {"trade_name": "Nova Loja", "segment": "moda",
                                                                      "coupon_code": "naoexiste"})
        self.assertIn("coupon_code", response.context["form"].errors)
        self.assertFalse(Company.objects.filter(trade_name="Nova Loja").exists())
        self.client.post(reverse("companies:onboarding"), {"trade_name": "Nova Loja", "segment": "moda",
                                                           "coupon_code": "entrada"})
        company = Company.objects.get(trade_name="Nova Loja")
        self.assertEqual(company.discount_percent, 100)
        self.assertEqual(company.effective_monthly_price, D("0.00"))
