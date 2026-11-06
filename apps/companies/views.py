from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.services import send_access_email
from apps.core.audit import log_action
from apps.core.permissions import company_view

from .forms import (CompanyForm, CompanySettingsForm, InviteMemberForm, MembershipForm, OnboardingForm,
                    RedeemCouponForm, WarehouseForm)
from .middleware import SESSION_KEY
from .models import ApiToken, Company, Membership, Plan, Warehouse
from .services import CouponError, PlanLimitError, check_user_limit, create_company, redeem_coupon

User = get_user_model()


@login_required
def onboarding(request):
    """Cadastro da empresa: obrigatório antes de acessar a gestão."""
    if request.membership is not None and request.GET.get("nova") != "1":
        return redirect("dashboard:home")
    form = OnboardingForm(request.POST or None, initial={"plan_choice": request.GET.get("plano", "trial")})
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        coupon_code = data.pop("coupon_code", "")
        plan = form.selected_plan()
        data.pop("plan_choice", None)
        company = create_company(request.user, data, request=request)  # começa no teste grátis
        request.session[SESSION_KEY] = company.pk
        messages.success(request, f"Empresa “{company}” cadastrada. Bem-vindo(a)!")
        if coupon_code:
            try:
                redemption = redeem_coupon(company, coupon_code, user=request.user, request=request)
            except CouponError as exc:
                messages.warning(request, f"A empresa foi criada, mas o cupom não pôde ser aplicado: {exc}")
            else:
                messages.success(request, f"Cupom {redemption.coupon.code} aplicado: "
                                          f"{redemption.coupon.discount_percent}% de desconto na assinatura.")
        if plan is None:
            messages.info(request, f"Seu teste grátis de {settings.FREE_TRIAL_DAYS} dias começou. "
                                   "Assine um plano quando quiser em Configurações → Assinatura.")
            return redirect("dashboard:home")
        # Plano escolhido: sem teste grátis — segue direto para o pagamento.
        company.status, company.trial_ends_at, company.plan = Company.Status.PENDING, None, plan
        company.save(update_fields=["status", "trial_ends_at", "plan", "updated_at"])
        return checkout_redirect(request, company, plan)
    return render(request, "companies/onboarding.html", {"form": form, "trial_days": settings.FREE_TRIAL_DAYS})


def checkout_redirect(request, company, plan):
    """Cria a assinatura e envia ao checkout do Mercado Pago (ou ativa direto, se gratuita)."""
    from apps.billing import gateway
    from apps.billing.services import BillingError, start_checkout

    try:
        sub = start_checkout(company, plan, user=request.user, request=request)
    except (BillingError, gateway.GatewayError) as exc:
        messages.error(request, str(exc))
        return redirect("companies:subscription")
    if sub.is_active:
        messages.success(request, f"Plano {plan.name} ativado sem custo (cupom de 100%).")
        return redirect("dashboard:home")
    return redirect(sub.init_point)


@company_view("settings", admin=True, billing_exempt=True)
def subscription(request):
    """Plano, assinatura no Mercado Pago, cobranças e resgate de cupom pela própria empresa."""
    from apps.billing import gateway
    from apps.billing.services import current_subscription, quote

    company = request.company
    form = RedeemCouponForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            redemption = redeem_coupon(company, form.cleaned_data["code"], user=request.user, request=request)
        except CouponError as exc:
            form.add_error("code", str(exc))
        else:
            messages.success(request, f"Cupom {redemption.coupon.code} aplicado: "
                                      f"{redemption.coupon.discount_percent}% de desconto na assinatura.")
            return redirect("companies:subscription")
    sub = current_subscription(company)
    plans = [quote(company, p) for p in Plan.objects.filter(is_active=True).order_by("monthly_price", "name")]
    return render(request, "companies/subscription.html", {
        "company": company, "form": form, "redemption": company.active_redemption, "subscription": sub,
        "plans": plans, "payments_enabled": gateway.is_configured(), "test_mode": gateway.is_test_mode(),
        "payments": sub.payments.all()[:12] if sub else [],
        "history": company.coupon_redemptions.select_related("coupon")[:10], "tab": "assinatura",
    })


@login_required
@require_POST
def switch_company(request):
    company_id = request.POST.get("company_id")
    membership = Membership.objects.filter(user=request.user, company_id=company_id, is_active=True).first()
    if membership is None:
        raise Http404
    request.session[SESSION_KEY] = membership.company_id
    messages.info(request, f"Empresa ativa: {membership.company}.")
    return redirect("dashboard:home")


@company_view("settings", admin=True)
def settings_view(request):
    company = request.company
    tab = request.GET.get("aba", "dados")
    data_form = CompanyForm(request.POST if request.POST.get("form") == "dados" else None, instance=company,
                            prefix="d")
    settings_form = CompanySettingsForm(request.POST if request.POST.get("form") == "config" else None,
                                        instance=company, prefix="c")
    if request.method == "POST":
        form = data_form if request.POST.get("form") == "dados" else settings_form
        if form.is_valid():
            form.save()
            log_action("company.settings_update", request=request, target=company,
                       details={"campos": form.changed_data})
            messages.success(request, "Configurações salvas.")
            return redirect(f"{request.path}?aba={'dados' if form is data_form else 'config'}")
        tab = "dados" if form is data_form else "config"
    return render(request, "companies/settings.html", {
        "data_form": data_form, "settings_form": settings_form, "tab": tab, "company": company,
        "warehouses": Warehouse.objects.filter(company=company),
    })


@company_view("settings", admin=True)
def team(request):
    members = (Membership.objects.filter(company=request.company).select_related("user")
               .order_by("-is_active", "user__full_name"))
    return render(request, "companies/team.html", {"members": members})


@company_view("settings", admin=True)
def invite_member(request):
    company = request.company
    form = InviteMemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        try:
            check_user_limit(company)
        except PlanLimitError as exc:
            messages.error(request, str(exc))
            return redirect("companies:team")
        with transaction.atomic():
            user = User.objects.filter(email__iexact=email).first()
            created = user is None
            if created:
                user = User.objects.create_user(email=email, full_name=form.cleaned_data["full_name"])
            membership, new = Membership.objects.get_or_create(
                user=user, company=company,
                defaults={"role": form.cleaned_data["role"], "modules": form.cleaned_data["modules"]})
            if not new:
                if membership.is_active:
                    messages.warning(request, "Este usuário já faz parte da equipe.")
                    return redirect("companies:team")
                membership.is_active = True
                membership.role = form.cleaned_data["role"]
                membership.modules = form.cleaned_data["modules"]
                membership.save()
            log_action("member.invite", request=request, target=user,
                       details={"perfil": membership.role, "novo_usuario": created})
        if created or not user.has_usable_password():
            send_access_email(user, invited_by=request.user, company=company)
            messages.success(request, f"Convite enviado para {email}. O link permite definir a senha.")
        else:
            messages.success(request, f"{user} foi adicionado(a) à equipe. Ele(a) já possui senha.")
        return redirect("companies:team")
    return render(request, "companies/invite.html", {"form": form})


@company_view("settings", admin=True)
def edit_member(request, pk):
    membership = get_object_or_404(Membership, pk=pk, company=request.company)
    form = MembershipForm(request.POST or None, instance=membership)
    if request.method == "POST" and form.is_valid():
        if membership.user_id == request.user.id and (
                form.cleaned_data["role"] != Membership.Role.ADMIN or not form.cleaned_data["is_active"]):
            messages.error(request, "Você não pode remover seu próprio perfil de administrador.")
            return redirect("companies:team")
        if form.cleaned_data["is_active"] and not membership.is_active:
            try:
                check_user_limit(request.company)
            except PlanLimitError as exc:
                messages.error(request, str(exc))
                return redirect("companies:team")
        before = {"perfil": membership.role, "modulos": list(membership.modules), "ativo": membership.is_active}
        form.save()
        log_action("member.permissions_change", request=request, target=membership.user,
                   details={"antes": before, "depois": {"perfil": membership.role,
                                                        "modulos": membership.modules,
                                                        "ativo": membership.is_active}})
        messages.success(request, "Permissões atualizadas.")
        return redirect("companies:team")
    return render(request, "companies/member_form.html", {"form": form, "membership": membership})


@company_view("settings", admin=True)
@require_POST
def resend_invite(request, pk):
    membership = get_object_or_404(Membership, pk=pk, company=request.company, is_active=True)
    send_access_email(membership.user, invited_by=request.user, company=request.company,
                      reason="invite" if not membership.user.has_usable_password() else "reset")
    log_action("member.access_link", request=request, target=membership.user)
    messages.success(request, f"Link de acesso enviado para {membership.user.email}.")
    return redirect("companies:team")


@company_view("settings", admin=True)
def api_tokens(request):
    company = request.company
    new_token = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:80]
        if not name:
            messages.error(request, "Informe uma identificação para o token (ex.: “Integração Mercado Livre”).")
        else:
            token, new_token = ApiToken.issue(company, name, request.user)
            log_action("api_token.create", request=request, target=token)
            messages.success(request, "Token criado. Copie agora: ele não será exibido novamente.")
    tokens = ApiToken.objects.filter(company=company)
    return render(request, "companies/api_tokens.html", {"tokens": tokens, "new_token": new_token})


@company_view("settings", admin=True)
@require_POST
def revoke_api_token(request, pk):
    token = get_object_or_404(ApiToken, pk=pk, company=request.company)
    token.is_active = False
    token.save(update_fields=["is_active", "updated_at"])
    log_action("api_token.revoke", request=request, target=token)
    messages.success(request, "Token revogado.")
    return redirect("companies:api_tokens")


@company_view("settings", admin=True)
def warehouse_form(request, pk=None):
    company = request.company
    if not company.multi_warehouse_enabled:
        messages.error(request, "Habilite “múltiplos depósitos” nas configurações para cadastrar depósitos.")
        return redirect("companies:settings")
    instance = get_object_or_404(Warehouse, pk=pk, company=company) if pk else None
    form = WarehouseForm(request.POST or None, instance=instance, company=company)
    if request.method == "POST" and form.is_valid():
        warehouse = form.save(commit=False)
        warehouse.company = company
        if warehouse.is_default and not warehouse.is_active:
            messages.error(request, "O depósito principal não pode ser inativado.")
        else:
            warehouse.save()
            log_action("warehouse.save", request=request, target=warehouse)
            messages.success(request, "Depósito salvo.")
            return redirect("/configuracoes/?aba=depositos")
    return render(request, "core/crud_form.html", {"form": form, "title": "Depósito",
                                                   "cancel_url": "/configuracoes/?aba=depositos"})
