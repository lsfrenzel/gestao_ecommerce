"""Cadastro, login, recuperação de senha, perfis/permissões e painel SUPER ADMIN."""

import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import LoginEvent, User
from apps.companies.models import Company, Membership, Plan
from apps.core.models import AuditLog

from .factories import PASSWORD, add_member, make_company, make_product, make_user

STRONG = "Outra-Senha#2026"


class SignupLoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_signup_then_company_onboarding(self):
        response = self.client.post(reverse("accounts:signup"), {
            "full_name": "Maria Lojista", "email": "Maria@Exemplo.test", "password1": STRONG, "password2": STRONG,
            "accept_terms": "on"})
        self.assertRedirects(response, reverse("companies:onboarding"))
        user = User.objects.get(email="maria@exemplo.test")
        self.assertNotEqual(user.password, STRONG)  # hash, nunca texto puro
        self.assertTrue(user.check_password(STRONG))
        # Sem empresa: gestão bloqueada até o cadastro da empresa
        self.assertRedirects(self.client.get(reverse("products:list")), reverse("companies:onboarding"))
        response = self.client.post(reverse("companies:onboarding"), {"trade_name": "Loja da Maria",
                                                                      "segment": "moda", "cnpj": ""})
        self.assertRedirects(response, reverse("dashboard:home"))
        membership = Membership.objects.get(user=user)
        self.assertEqual(membership.role, Membership.Role.ADMIN)
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 200)

    def test_signup_validation(self):
        make_user(email="existe@exemplo.test")
        response = self.client.post(reverse("accounts:signup"), {
            "full_name": "X", "email": "existe@exemplo.test", "password1": "123", "password2": "456"})
        form = response.context["form"]
        self.assertIn("email", form.errors)
        self.assertIn("password2", form.errors)
        self.assertIn("accept_terms", form.errors)

    def test_invalid_cnpj_rejected(self):
        self.client.force_login(make_user())
        response = self.client.post(reverse("companies:onboarding"), {"trade_name": "X", "segment": "outros",
                                                                      "cnpj": "11.111.111/1111-11"})
        self.assertIn("cnpj", response.context["form"].errors)
        self.assertFalse(Company.objects.exists())

    def test_login_logout(self):
        user = make_user(email="joao@exemplo.test")
        make_company(user)
        response = self.client.post(reverse("accounts:login"), {"email": "JOAO@exemplo.test", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertEqual(LoginEvent.objects.filter(user=user).count(), 1)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 302)

    def test_wrong_password_and_blocked_user(self):
        user = make_user(email="ana@exemplo.test")
        response = self.client.post(reverse("accounts:login"), {"email": "ana@exemplo.test", "password": "errada"})
        self.assertContains(response, "inválidos")
        user.is_active = False
        user.save()
        response = self.client.post(reverse("accounts:login"), {"email": "ana@exemplo.test", "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_RATE_LIMIT_ATTEMPTS=3)
    def test_login_rate_limit(self):
        make_user(email="alvo@exemplo.test")
        for _ in range(3):
            self.client.post(reverse("accounts:login"), {"email": "alvo@exemplo.test", "password": "x"})
        response = self.client.post(reverse("accounts:login"), {"email": "alvo@exemplo.test", "password": PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_open_redirect_is_blocked(self):
        user = make_user(email="r@exemplo.test")
        make_company(user)
        response = self.client.post(reverse("accounts:login") + "?next=https://malicioso.example",
                                    {"email": "r@exemplo.test", "password": PASSWORD,
                                     "next": "https://malicioso.example"})
        self.assertRedirects(response, reverse("dashboard:home"))


class PasswordRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_reset_flow(self):
        user = make_user(email="reset@exemplo.test")
        response = self.client.post(reverse("accounts:password_reset"), {"email": "reset@exemplo.test"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"https?://\S+/conta/senha/redefinir/\S+/", mail.outbox[0].body).group(0)
        path = link.split("://", 1)[1].split("/", 1)[1]
        response = self.client.get("/" + path, follow=True)
        response = self.client.post(response.redirect_chain[-1][0],
                                    {"new_password1": STRONG, "new_password2": STRONG})
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))
        self.assertNotIn(STRONG, mail.outbox[0].body)

    def test_unknown_email_does_not_reveal_existence(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": "ninguem@exemplo.test"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(PASSWORD_RESET_RATE_LIMIT=2)
    def test_reset_rate_limit(self):
        make_user(email="rl@exemplo.test")
        for _ in range(2):
            self.client.post(reverse("accounts:password_reset"), {"email": "rl@exemplo.test"})
        response = self.client.post(reverse("accounts:password_reset"), {"email": "rl@exemplo.test"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 2)

    def test_password_change(self):
        user = make_user()
        make_company(user)
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": PASSWORD, "new_password1": STRONG, "new_password2": STRONG})
        self.assertRedirects(response, reverse("accounts:profile"))
        user.refresh_from_db()
        self.assertTrue(user.check_password(STRONG))

    def test_invalid_reset_token(self):
        response = self.client.get("/conta/senha/redefinir/MQ/token-invalido/")
        self.assertContains(response, "Link inválido")


class PermissionTests(TestCase):
    def setUp(self):
        self.company = make_company()
        self.product = make_product(self.company)

    def login(self, role, modules=None):
        user = add_member(self.company, role=role, modules=modules)
        self.client.force_login(user)
        return user

    def test_operator_default_modules(self):
        self.login(Membership.Role.OPERATOR)
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sales:list")).status_code, 200)
        for name in ("finance:overview", "reports:index", "companies:settings", "purchasing:orders", "pricing:calculator"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_operator_cannot_delete_or_archive(self):
        self.login(Membership.Role.OPERATOR)
        self.assertEqual(self.client.post(reverse("products:delete", args=[self.product.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("products:toggle_active", args=[self.product.pk])).status_code, 403)
        self.assertTrue(type(self.product).objects.filter(pk=self.product.pk, is_active=True).exists())

    def test_admin_grants_custom_modules(self):
        self.login(Membership.Role.OPERATOR, modules=["finance", "dashboard"])
        self.assertEqual(self.client.get(reverse("finance:overview")).status_code, 200)
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 403)

    def test_manager_access_but_not_settings(self):
        self.login(Membership.Role.MANAGER)
        self.assertEqual(self.client.get(reverse("finance:overview")).status_code, 200)
        self.assertEqual(self.client.get(reverse("companies:settings")).status_code, 403)
        self.assertEqual(self.client.get(reverse("companies:team")).status_code, 403)
        # módulo de configurações nunca é concedido a não-administradores, mesmo se marcado
        Membership.objects.filter(company=self.company, role="manager").update(modules=["settings"])
        self.assertEqual(self.client.get(reverse("companies:settings")).status_code, 403)

    def test_admin_invites_member_and_changes_permissions_with_audit(self):
        admin = self.company.owner
        self.client.force_login(admin)
        response = self.client.post(reverse("companies:invite"), {"full_name": "Novo", "email": "novo@exemplo.test",
                                                                  "role": "operator", "modules": ["sales"]})
        self.assertRedirects(response, reverse("companies:team"))
        invited = User.objects.get(email="novo@exemplo.test")
        self.assertFalse(invited.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        membership = Membership.objects.get(user=invited)
        self.client.post(reverse("companies:edit_member", args=[membership.pk]),
                         {"role": "manager", "modules": [], "is_active": "on"})
        membership.refresh_from_db()
        self.assertEqual(membership.role, "manager")
        self.assertTrue(AuditLog.objects.filter(action="member.permissions_change").exists())

    def test_admin_cannot_demote_self(self):
        admin = self.company.owner
        self.client.force_login(admin)
        own = Membership.objects.get(user=admin)
        self.client.post(reverse("companies:edit_member", args=[own.pk]), {"role": "operator", "is_active": "on"})
        own.refresh_from_db()
        self.assertEqual(own.role, "admin")

    def test_plan_user_limit(self):
        plan = Plan.objects.create(name="Mini", max_users=1, features=[])
        self.company.plan = plan
        self.company.save()
        self.client.force_login(self.company.owner)
        self.client.post(reverse("companies:invite"), {"full_name": "X", "email": "x@exemplo.test", "role": "operator"})
        self.assertFalse(User.objects.filter(email="x@exemplo.test").exists())

    def test_plan_feature_gating(self):
        self.company.plan = Plan.objects.create(name="Básico", features=["inventory"])
        self.company.save()
        self.client.force_login(self.company.owner)
        self.assertEqual(self.client.get(reverse("finance:overview")).status_code, 402)
        self.assertEqual(self.client.get(reverse("inventory:overview")).status_code, 200)

    def test_suspended_company_is_blocked(self):
        self.company.status = Company.Status.SUSPENDED
        self.company.save()
        self.client.force_login(self.company.owner)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "suspens", status_code=403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("products:list"))
        self.assertTrue(response["Location"].startswith(reverse("accounts:login")))


class SuperAdminTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@exemplo.test", password=PASSWORD, full_name="Root")
        self.owner = make_user()
        self.company = make_company(self.owner)

    def test_only_superadmin_accesses_platform(self):
        self.client.force_login(self.owner)
        for name in ("platform:home", "platform:users", "platform:companies", "platform:plans", "platform:audit"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("platform:home")).status_code, 302)
        self.client.force_login(self.root)
        for name in ("platform:home", "platform:users", "platform:companies", "platform:plans", "platform:audit"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_superadmin_without_company_goes_to_platform(self):
        self.client.force_login(self.root)
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("platform:home"))

    def test_dashboard_counts_and_no_password_exposure(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse("platform:home"))
        self.assertEqual(response.context["stats"]["users"], 2)
        self.assertEqual(response.context["stats"]["companies"], 1)
        detail = self.client.get(reverse("platform:user_detail", args=[self.owner.pk]))
        self.assertNotContains(detail, self.owner.password)
        self.assertNotContains(detail, "pbkdf2")
        self.assertNotContains(detail, "md5$")

    def test_block_and_unblock_user_with_audit(self):
        self.client.force_login(self.root)
        self.client.post(reverse("platform:user_block", args=[self.owner.pk]), {"reason": "Fraude"})
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)
        self.assertTrue(AuditLog.objects.filter(action="platform.user_block", platform_scope=True).exists())
        other = self.client_class()
        other.post(reverse("accounts:login"), {"email": self.owner.email, "password": PASSWORD})
        self.assertNotIn("_auth_user_id", other.session)
        self.client.post(reverse("platform:user_block", args=[self.owner.pk]))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_cannot_block_self(self):
        self.client.force_login(self.root)
        self.client.post(reverse("platform:user_block", args=[self.root.pk]), {"reason": "x"})
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_active)

    def test_change_plan_and_suspend_company(self):
        plan = Plan.objects.create(name="Pro", features=[])
        self.client.force_login(self.root)
        response = self.client.post(reverse("platform:company_update", args=[self.company.pk]),
                                    {"plan": plan.pk, "status": "suspended", "suspended_reason": "Inadimplência"})
        self.assertRedirects(response, reverse("platform:company_detail", args=[self.company.pk]))
        self.company.refresh_from_db()
        self.assertEqual((self.company.plan, self.company.status), (plan, "suspended"))
        log = AuditLog.objects.get(action="platform.company_update")
        self.assertEqual(log.details["depois"]["plano"], "Pro")
        # suspensão sem motivo é rejeitada
        self.client.post(reverse("platform:company_update", args=[self.company.pk]), {"status": "suspended"})
        self.assertEqual(AuditLog.objects.filter(action="platform.company_update").count(), 1)

    def test_company_detail_view_is_audited_and_shows_only_aggregates(self):
        make_product(self.company, name="Produto Secreto")
        self.client.force_login(self.root)
        response = self.client.get(reverse("platform:company_detail", args=[self.company.pk]))
        self.assertEqual(response.context["products_n"], 1)
        self.assertNotContains(response, "Produto Secreto")
        self.assertTrue(AuditLog.objects.filter(action="platform.company_view").exists())

    def test_access_link_and_invite(self):
        self.client.force_login(self.root)
        self.client.post(reverse("platform:user_access_link", args=[self.owner.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/conta/senha/redefinir/", mail.outbox[0].body)
        self.client.post(reverse("platform:user_invite"), {"full_name": "Conv", "email": "conv@exemplo.test",
                                                           "company": self.company.pk, "role": "manager"})
        self.assertTrue(Membership.objects.filter(user__email="conv@exemplo.test", role="manager").exists())
        self.assertEqual(len(mail.outbox), 2)

    def test_plan_crud(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse("platform:plan_new"), {
            "name": "Start", "description": "", "monthly_price": "39.90", "max_users": "2", "max_products": "",
            "features": ["pricing", "inventory"], "trial_days": "7", "is_active": "on", "is_default": "on"})
        self.assertRedirects(response, reverse("platform:plans"))
        plan = Plan.objects.get(name="Start")
        self.assertEqual(plan.features, ["pricing", "inventory"])
        self.assertIsNone(plan.max_products)
        self.assertEqual(Plan.default(), plan)
