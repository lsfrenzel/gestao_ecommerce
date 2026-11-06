"""Assinaturas pelo Mercado Pago (API simulada — nenhuma chamada externa)."""

import hashlib
import hmac
import io
import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import gateway
from apps.billing.models import Subscription, SubscriptionPayment, WebhookEvent
from apps.billing.services import apply_coupon_expirations
from apps.companies.models import ApiToken, Company, Coupon, Plan
from apps.companies.services import redeem_coupon

from .factories import add_member, make_company, make_user

MP = {"MERCADOPAGO_ACCESS_TOKEN": "TEST-123-token", "MERCADOPAGO_PUBLIC_KEY": "TEST-pk",
      "MERCADOPAGO_WEBHOOK_SECRET": "", "SITE_URL": "https://gestao.exemplo.test"}
GW = "apps.billing.gateway."


def preapproval(pid="pre-1", status="authorized", sub=None, **extra):
    data = {"id": pid, "status": status, "init_point": f"https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id={pid}",
            "next_payment_date": (timezone.now() + timedelta(days=30)).isoformat()}
    if sub is not None:
        data["external_reference"] = sub.external_reference
    data.update(extra)
    return data


@override_settings(**MP)
class BillingTestCase(TestCase):
    def setUp(self):
        self.basic = Plan.objects.create(name="Essencial", monthly_price=D("49.90"), features=[], is_active=True)
        self.pro = Plan.objects.create(name="Profissional", monthly_price=D("129.90"), features=[], is_active=True,
                                       is_default=True)


class OnboardingPlanTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.client.force_login(self.user)

    def form(self, choice):
        return {"trade_name": "Loja Nova", "segment": "moda", "plan_choice": choice}

    def test_page_lists_plans_and_free_trial(self):
        response = self.client.get(reverse("companies:onboarding"))
        self.assertContains(response, "Teste grátis")
        self.assertContains(response, "14 dias")
        self.assertContains(response, "Essencial")
        self.assertContains(response, "129,90")

    def test_free_trial_without_card(self):
        with mock.patch(GW + "create_preapproval") as create:
            response = self.client.post(reverse("companies:onboarding"), self.form("trial"))
        create.assert_not_called()
        self.assertRedirects(response, reverse("dashboard:home"))
        company = Company.objects.get()
        self.assertEqual(company.status, "trial")
        self.assertEqual(company.trial_ends_at, timezone.localdate() + timedelta(days=14))
        self.assertEqual(company.plan, self.pro)  # recursos do plano padrão durante o teste
        self.assertTrue(company.has_access)

    def test_paid_plan_goes_to_mercado_pago_checkout(self):
        with mock.patch(GW + "create_preapproval", return_value=preapproval(status="pending")) as create:
            response = self.client.post(reverse("companies:onboarding"), self.form(str(self.basic.pk)))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://www.mercadopago.com.br/subscriptions/checkout"))
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["amount"], D("49.90"))
        self.assertEqual(kwargs["payer_email"], self.user.email)
        self.assertEqual(kwargs["back_url"], "https://gestao.exemplo.test/assinatura/retorno/")
        self.assertIsNone(kwargs["start_date"])  # sem teste: cobrança imediata
        company = Company.objects.get()
        self.assertEqual((company.status, company.plan), ("pending", self.basic))
        self.assertFalse(company.has_access)
        sub = Subscription.objects.get()
        self.assertEqual((sub.status, sub.mp_preapproval_id), ("pending", "pre-1"))
        # sem pagamento confirmado, a gestão fica bloqueada e só a assinatura abre
        self.assertEqual(self.client.get(reverse("products:list")).status_code, 402)
        self.assertEqual(self.client.get(reverse("companies:subscription")).status_code, 200)

    def test_gateway_failure_starts_free_trial_instead_of_blocking(self):
        with mock.patch(GW + "create_preapproval", side_effect=gateway.GatewayError("recusado")):
            response = self.client.post(reverse("companies:onboarding"), self.form(str(self.basic.pk)), follow=True)
        self.assertContains(response, "recusado")
        self.assertContains(response, "teste grátis")
        self.assertFalse(Subscription.objects.exists())
        company = Company.objects.get()
        self.assertEqual(company.status, "trial")
        self.assertTrue(company.has_access)

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="")
    def test_paid_plans_selectable_without_credentials_fall_back_to_trial(self):
        response = self.client.get(reverse("companies:onboarding"))
        self.assertNotContains(response, 'disabled>')  # nenhum plano bloqueado
        response = self.client.post(reverse("companies:onboarding"), self.form(str(self.basic.pk)), follow=True)
        company = Company.objects.get()
        self.assertEqual(company.status, "trial")
        self.assertContains(response, "Não foi possível abrir o pagamento")

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="")
    def test_credential_aliases(self):
        import os

        from django.conf import settings as dj

        from config.settings import _credential

        os.environ["MP_ACCESS_TOKEN"] = '"Bearer APP_USR-123"'
        try:
            self.assertEqual(_credential("MERCADOPAGO_ACCESS_TOKEN_INEXISTENTE", "MP_ACCESS_TOKEN"), "APP_USR-123")
        finally:
            del os.environ["MP_ACCESS_TOKEN"]
        self.assertEqual(dj.MERCADOPAGO_ACCESS_TOKEN, "")


class AccessTests(BillingTestCase):
    def test_trial_expired_blocks_everything_but_subscription(self):
        company = make_company(plan=self.pro)
        company.start_trial(self.pro)
        company.trial_ends_at = timezone.localdate() - timedelta(days=1)
        company.save()
        self.client.force_login(company.owner)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 402)
        self.assertContains(response, "teste grátis terminou", status_code=402)
        self.assertEqual(self.client.get(reverse("companies:subscription")).status_code, 200)
        # operador vê a orientação, mas não a tela de assinatura
        self.client.force_login(add_member(company))
        self.assertContains(self.client.get(reverse("sales:list")), "administrador", status_code=402)
        # API também é bloqueada
        _, raw = ApiToken.issue(company, "x", company.owner)
        self.assertEqual(self.client.get("/api/v1/produtos/", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 401)


class CheckoutTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.company = make_company(plan=self.pro)
        self.company.start_trial(self.pro)
        self.company.save()
        self.admin = self.company.owner
        self.client.force_login(self.admin)

    def subscribe(self, plan, data=None):
        with mock.patch(GW + "create_preapproval", return_value=data or preapproval(status="pending")) as create:
            response = self.client.post(reverse("billing:subscribe"), {"plan": plan.pk})
        return response, create

    def test_subscribing_during_trial_keeps_remaining_days(self):
        response, create = self.subscribe(self.basic)
        self.assertEqual(response.status_code, 302)
        start = create.call_args.kwargs["start_date"]
        self.assertEqual(timezone.localtime(start).date(), self.company.trial_ends_at + timedelta(days=1))

    def test_return_confirms_via_api_and_activates(self):
        self.subscribe(self.basic)
        sub = Subscription.objects.get()
        with mock.patch(GW + "get_preapproval", return_value=preapproval(status="authorized", sub=sub)):
            response = self.client.get(reverse("billing:return") + "?preapproval_id=pre-1")
        self.assertRedirects(response, reverse("dashboard:home"), fetch_redirect_response=False)
        self.company.refresh_from_db()
        sub.refresh_from_db()
        self.assertEqual((self.company.status, self.company.plan), ("active", self.basic))
        self.assertEqual(sub.status, "authorized")
        self.assertIsNotNone(sub.next_payment_date)

    def test_return_pending_does_not_activate(self):
        self.subscribe(self.basic)
        with mock.patch(GW + "get_preapproval", return_value=preapproval(status="pending")):
            self.client.get(reverse("billing:return") + "?preapproval_id=pre-1")
        self.company.refresh_from_db()
        self.assertEqual(self.company.status, "trial")

    def test_coupon_50_halves_amount(self):
        Coupon.objects.create(code="METADE", discount_percent=50)
        redeem_coupon(self.company, "METADE")
        _, create = self.subscribe(self.pro)
        self.assertEqual(create.call_args.kwargs["amount"], D("64.95"))
        self.assertEqual(Subscription.objects.get().discount_percent, 50)

    def test_coupon_100_permanent_is_free_without_mercado_pago(self):
        Coupon.objects.create(code="GRATIS", discount_percent=100)
        redeem_coupon(self.company, "GRATIS")
        response, create = self.subscribe(self.pro)
        create.assert_not_called()
        self.assertRedirects(response, reverse("dashboard:home"), fetch_redirect_response=False)
        self.company.refresh_from_db()
        self.assertEqual(self.company.status, "active")
        self.assertEqual(Subscription.objects.get().status, "free")

    def test_coupon_100_with_duration_delays_first_charge(self):
        Coupon.objects.create(code="DOISMESES", discount_percent=100, duration_months=2)
        redemption = redeem_coupon(self.company, "DOISMESES")
        _, create = self.subscribe(self.pro)
        self.assertEqual(create.call_args.kwargs["amount"], D("129.90"))
        self.assertEqual(timezone.localtime(create.call_args.kwargs["start_date"]).date(),
                         redemption.expires_at + timedelta(days=1))

    def test_cancel_keeps_access_until_paid_period(self):
        self.subscribe(self.basic)
        sub = Subscription.objects.get()
        with mock.patch(GW + "get_preapproval", return_value=preapproval(status="authorized", sub=sub)):
            self.client.get(reverse("billing:return") + "?preapproval_id=pre-1")
        next_payment = timezone.now() + timedelta(days=20)
        with mock.patch(GW + "cancel_preapproval",
                        return_value=preapproval(status="cancelled", next_payment_date=next_payment.isoformat())) as cancel:
            self.client.post(reverse("billing:cancel"))
        cancel.assert_called_once_with("pre-1")
        self.company.refresh_from_db()
        self.assertEqual(self.company.status, "pending")
        self.assertEqual(self.company.access_until, timezone.localtime(next_payment).date())
        self.assertTrue(self.company.has_access)
        self.company.access_until = timezone.localdate() - timedelta(days=1)
        self.company.save()
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 402)

    def test_plan_change_cancels_previous_subscription(self):
        self.subscribe(self.basic)
        first = Subscription.objects.get()
        with mock.patch(GW + "get_preapproval", return_value=preapproval(status="authorized", sub=first)):
            self.client.get(reverse("billing:return") + "?preapproval_id=pre-1")
        self.subscribe(self.pro, preapproval(pid="pre-2", status="pending"))
        second = Subscription.objects.get(mp_preapproval_id="pre-2")
        with mock.patch(GW + "get_preapproval", return_value=preapproval(pid="pre-2", status="authorized", sub=second)), \
                mock.patch(GW + "cancel_preapproval") as cancel:
            self.client.get(reverse("billing:return") + "?preapproval_id=pre-2")
        cancel.assert_called_once_with("pre-1")
        first.refresh_from_db()
        self.company.refresh_from_db()
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(self.company.plan, self.pro)

    def test_operator_cannot_subscribe(self):
        self.client.force_login(add_member(self.company))
        response, create = self.subscribe(self.basic)
        self.assertEqual(response.status_code, 403)
        create.assert_not_called()

    def test_subscription_page_shows_plans_with_quotes(self):
        response = self.client.get(reverse("companies:subscription"))
        self.assertContains(response, "Assinar com Mercado Pago")
        self.assertContains(response, "49,90")
        self.assertContains(response, "modo de teste")

    def test_coupon_expiration_restores_full_price(self):
        sub = Subscription.objects.create(company=self.company, plan=self.pro, status="authorized", full_amount=D("129.90"),
                                          amount=D("64.95"), discount_percent=50, mp_preapproval_id="pre-9",
                                          discount_until=timezone.localdate() - timedelta(days=1))
        with mock.patch(GW + "update_amount") as update:
            self.assertEqual(apply_coupon_expirations(), 1)
        update.assert_called_once_with("pre-9", D("129.90"))
        sub.refresh_from_db()
        self.assertEqual((sub.amount, sub.discount_percent), (D("129.90"), 0))


@override_settings(**{**MP, "MERCADOPAGO_WEBHOOK_SECRET": "segredo-webhook"})
class WebhookTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.company = make_company(plan=self.pro)
        self.company.status = Company.Status.PENDING
        self.company.save()
        self.sub = Subscription.objects.create(company=self.company, plan=self.basic, full_amount=D("49.90"),
                                               amount=D("49.90"), mp_preapproval_id="pre-7")

    def post(self, body, data_id, secret="segredo-webhook", ts="1700000000", request_id="req-1"):
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        sig = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return self.client.post(f"/assinatura/webhook/mercadopago/?data.id={data_id}&type={body['type']}",
                                json.dumps(body), content_type="application/json",
                                HTTP_X_SIGNATURE=f"ts={ts},v1={sig}", HTTP_X_REQUEST_ID=request_id)

    def test_preapproval_authorized_activates_company(self):
        body = {"type": "subscription_preapproval", "data": {"id": "pre-7"}}
        with mock.patch(GW + "get_preapproval", return_value=preapproval(pid="pre-7", status="authorized")):
            response = self.post(body, "pre-7")
        self.assertEqual(response.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual((self.company.status, self.company.plan), ("active", self.basic))
        self.assertIsNotNone(WebhookEvent.objects.get().processed_at)
        # notificação repetida é inofensiva
        with mock.patch(GW + "get_preapproval", return_value=preapproval(pid="pre-7", status="authorized")):
            self.assertEqual(self.post(body, "pre-7").status_code, 200)

    def test_invalid_signature_is_rejected(self):
        body = {"type": "subscription_preapproval", "data": {"id": "pre-7"}}
        with mock.patch(GW + "get_preapproval") as get:
            response = self.post(body, "pre-7", secret="outro")
        self.assertEqual(response.status_code, 401)
        get.assert_not_called()
        self.company.refresh_from_db()
        self.assertEqual(self.company.status, "pending")

    def test_authorized_payment_is_recorded(self):
        body = {"type": "subscription_authorized_payment", "data": {"id": "6114264375"}}
        payment = {"id": 6114264375, "preapproval_id": "pre-7", "transaction_amount": 49.9, "status": "processed",
                   "debit_date": timezone.now().isoformat(), "payment": {"status": "approved"}}
        with mock.patch(GW + "get_authorized_payment", return_value=payment), \
                mock.patch(GW + "get_preapproval", return_value=preapproval(pid="pre-7", status="authorized")):
            self.assertEqual(self.post(body, "6114264375").status_code, 200)
        p = SubscriptionPayment.objects.get()
        self.assertEqual((p.amount, p.status, p.status_label), (D("49.9"), "approved", "Paga"))

    def test_gateway_error_asks_for_retry(self):
        body = {"type": "subscription_preapproval", "data": {"id": "pre-7"}}
        with mock.patch(GW + "get_preapproval", side_effect=gateway.GatewayError("fora do ar")):
            self.assertEqual(self.post(body, "pre-7").status_code, 502)

    def test_unknown_topic_is_ignored(self):
        self.assertEqual(self.client.post("/assinatura/webhook/mercadopago/", "{}", content_type="application/json")
                         .status_code, 200)

    def test_sync_command(self):
        with mock.patch(GW + "get_preapproval", return_value=preapproval(pid="pre-7", status="authorized")):
            call_command("sync_subscriptions", stdout=io.StringIO())
        self.company.refresh_from_db()
        self.assertEqual(self.company.status, "active")


class GatewayUnitTests(TestCase):
    @override_settings(MERCADOPAGO_WEBHOOK_SECRET="abc")
    def test_signature_validation(self):
        manifest = "id:123;request-id:r1;ts:99;"
        sig = hmac.new(b"abc", manifest.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(gateway.valid_signature(signature_header=f"ts=99,v1={sig}", request_id="r1", data_id="123"))
        self.assertFalse(gateway.valid_signature(signature_header="ts=99,v1=errado", request_id="r1", data_id="123"))
        self.assertFalse(gateway.valid_signature(signature_header="", request_id="r1", data_id="123"))

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="")
    def test_not_configured_raises(self):
        with self.assertRaises(gateway.GatewayError):
            gateway.get_preapproval("x")

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="TEST-1")
    def test_create_preapproval_payload(self):
        with mock.patch(GW + "_request", return_value={"id": "p"}) as req:
            gateway.create_preapproval(reason="Plano", amount=D("49.90"), payer_email="a@b.test",
                                       back_url="https://x.test/r", external_reference="subscription:1")
        method, path, payload = req.call_args.args
        self.assertEqual((method, path), ("POST", "/preapproval"))
        self.assertEqual(payload["auto_recurring"], {"frequency": 1, "frequency_type": "months",
                                                     "transaction_amount": 49.9, "currency_id": "BRL"})
        self.assertEqual(payload["status"], "pending")


class FixedPriceCouponTests(BillingTestCase):
    """Cupom de preço fixo (ex.: R$ 1,00/mês)."""

    def setUp(self):
        super().setUp()
        self.root = make_user(email="root-fixo@exemplo.test", is_superuser=True, is_staff=True)
        self.company = make_company(plan=self.pro)
        self.company.start_trial(self.pro)
        self.company.trial_ends_at = None
        self.company.status = Company.Status.PENDING
        self.company.save()

    def test_create_one_real_coupon_in_panel(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse("platform:coupon_new"), {
            "code": "UMREAL", "discount_percent": "0", "fixed_price": "1,00", "is_active": "on"})
        coupon = Coupon.objects.get(code="UMREAL")
        self.assertRedirects(response, reverse("platform:coupon_detail", args=[coupon.pk]))
        self.assertTrue(coupon.is_fixed)
        self.assertEqual(coupon.fixed_price, D("1.00"))
        self.assertEqual(coupon.short_label, "R$ 1,00/mês")
        self.assertContains(self.client.get(reverse("platform:coupon_detail", args=[coupon.pk])), "R$ 1,00/mês")
        # valor obrigatório e mínimo de R$ 1,00
        response = self.client.post(reverse("platform:coupon_new"), {"code": "SEMVALOR", "discount_percent": "0",
                                                                     "is_active": "on"})
        self.assertIn("fixed_price", response.context["form"].errors)
        response = self.client.post(reverse("platform:coupon_new"), {"code": "CENTAVOS", "discount_percent": "0",
                                                                     "fixed_price": "0,50", "is_active": "on"})
        self.assertIn("fixed_price", response.context["form"].errors)
        # em cupons percentuais o valor fixo é ignorado
        self.client.post(reverse("platform:coupon_new"), {"code": "METADE2", "discount_percent": "50",
                                                          "fixed_price": "1,00", "is_active": "on"})
        self.assertIsNone(Coupon.objects.get(code="METADE2").fixed_price)

    def test_price_is_one_real_for_any_plan(self):
        coupon = Coupon.objects.create(code="UMREAL", discount_percent=0, fixed_price=D("1.00"))
        self.assertEqual(coupon.price_for(D("129.90")), D("1.00"))
        self.assertEqual(coupon.price_for(D("0.50")), D("0.50"))  # nunca acima do preço cheio
        redeem_coupon(self.company, "umreal")
        self.assertEqual(self.company.effective_monthly_price, D("1.00"))
        self.assertEqual(self.company.discount_percent, 99)

    def test_checkout_charges_one_real_and_restores_after_period(self):
        Coupon.objects.create(code="UMREAL3", discount_percent=0, fixed_price=D("1.00"), duration_months=3)
        redemption = redeem_coupon(self.company, "UMREAL3")
        self.client.force_login(self.company.owner)
        with mock.patch(GW + "create_preapproval", return_value=preapproval(status="pending")) as create:
            response = self.client.post(reverse("billing:subscribe"), {"plan": self.pro.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(create.call_args.kwargs["amount"], D("1.00"))
        sub = Subscription.objects.get()
        self.assertEqual((sub.amount, sub.full_amount, sub.discount_until), (D("1.00"), D("129.90"),
                                                                            redemption.expires_at))
        # depois do prazo do cupom, o valor volta ao preço cheio no Mercado Pago
        Subscription.objects.filter(pk=sub.pk).update(status="authorized",
                                                      discount_until=timezone.localdate() - timedelta(days=1))
        with mock.patch(GW + "update_amount") as update:
            self.assertEqual(apply_coupon_expirations(), 1)
        update.assert_called_once_with("pre-1", D("129.90"))

    def test_subscription_page_shows_fixed_price(self):
        Coupon.objects.create(code="UMREAL", discount_percent=0, fixed_price=D("1.00"))
        redeem_coupon(self.company, "UMREAL")
        self.client.force_login(self.company.owner)
        page = self.client.get(reverse("companies:subscription"))
        self.assertContains(page, "preço fixo de R$ 1,00/mês")
        self.assertContains(page, "R$ 1,00")
