"""
Painel da plataforma — exclusivo para SUPER ADMIN.

Princípio de minimização: o painel exibe apenas dados cadastrais e indicadores agregados das empresas
(quantidade de produtos, volume de vendas), nunca o conteúdo operacional (clientes, pedidos, preços).
Toda ação administrativa e toda consulta de detalhes de uma empresa é registrada na auditoria.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import LoginEvent
from apps.accounts.services import send_access_email, set_provisional_password
from apps.billing.services import current_subscription
from apps.companies.models import Company, Coupon, CouponRedemption, Membership, Plan
from apps.companies.services import CouponError, PlanLimitError, check_user_limit, redeem_coupon, remove_coupon
from apps.core.audit import log_action
from apps.core.models import AuditLog
from apps.core.money import format_brl
from apps.core.permissions import superadmin_required
from apps.products.models import Product
from apps.sales.models import SalesOrder

from .forms import ApplyCouponForm, BlockForm, CompanyPlanForm, CouponForm, CreateUserForm, InviteUserForm, PlanForm

User = get_user_model()


@superadmin_required
def home(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    since_30 = timezone.now() - timedelta(days=30)
    users = User.objects.all()
    stats = {
        "users": users.count(),
        "active_users": users.filter(is_active=True).count(),
        "blocked_users": users.filter(is_active=False).count(),
        "new_users_month": users.filter(date_joined__date__gte=month_start).count(),
        "active_30d": users.filter(last_login__gte=since_30).count(),
        "companies": Company.objects.count(),
        "companies_active": Company.objects.filter(status=Company.Status.ACTIVE).count(),
        "companies_trial": Company.objects.filter(status=Company.Status.TRIAL).count(),
        "companies_suspended": Company.objects.filter(status=Company.Status.SUSPENDED).count(),
        "logins_30d": LoginEvent.objects.filter(created_at__gte=since_30).count(),
        "logins_total": LoginEvent.objects.count(),
    }
    by_plan = list(Plan.objects.annotate(
        companies_n=Count("companies", distinct=True),
        users_n=Count("companies__memberships", filter=Q(companies__memberships__is_active=True), distinct=True))
        .values("name", "companies_n", "users_n"))
    no_plan = Company.objects.filter(plan__isnull=True).count()
    if no_plan:
        by_plan.append({"name": "Sem plano", "companies_n": no_plan,
                        "users_n": Membership.objects.filter(company__plan__isnull=True, is_active=True).count()})
    first = (today.replace(day=1) - timedelta(days=330)).replace(day=1)
    signups = {(r["m"].year, r["m"].month): r["n"] for r in User.objects.filter(date_joined__date__gte=first)
               .annotate(m=TruncMonth("date_joined")).values("m").annotate(n=Count("id"))}
    months, cursor = [], first
    while cursor <= today:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    logins = {r["d"]: r["n"] for r in LoginEvent.objects.filter(created_at__gte=since_30)
              .annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("id"))}
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    chart = json.dumps({
        "signups": {"labels": [m.strftime("%m/%Y") for m in months],
                    "values": [signups.get((m.year, m.month), 0) for m in months]},
        "logins": {"labels": [d.strftime("%d/%m") for d in days], "values": [logins.get(d, 0) for d in days]},
        "plans": {"labels": [p["name"] for p in by_plan], "values": [p["users_n"] for p in by_plan]},
    })
    recent = AuditLog.objects.filter(platform_scope=True).select_related("actor")[:8]
    return render(request, "platform/home.html", {"stats": stats, "by_plan": by_plan, "chart": chart,
                                                  "recent": recent})


@superadmin_required
def users(request):
    qs = User.objects.annotate(companies_n=Count("memberships", filter=Q(memberships__is_active=True)))
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(full_name__icontains=term) | Q(email__icontains=term))
    status = request.GET.get("status", "")
    if status == "ativos":
        qs = qs.filter(is_active=True)
    elif status == "bloqueados":
        qs = qs.filter(is_active=False)
    elif status == "superadmin":
        qs = qs.filter(is_superuser=True)
    page = Paginator(qs.order_by("-date_joined"), 30).get_page(request.GET.get("page"))
    return render(request, "platform/users.html", {"page_obj": page, "q": term, "status": status})


@superadmin_required
def user_detail(request, pk):
    user = get_object_or_404(User, pk=pk)
    return render(request, "platform/user_detail.html", {
        "obj": user, "memberships": user.memberships.select_related("company", "company__plan"),
        "logins": user.login_events.all()[:10], "block_form": BlockForm(),
        "logs": AuditLog.objects.filter(Q(actor=user) | Q(target_type="User", target_id=str(user.pk)))[:15],
    })


@superadmin_required
@require_POST
def user_block(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "Você não pode bloquear a própria conta.")
        return redirect("platform:user_detail", pk=pk)
    if user.is_active:
        form = BlockForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Informe o motivo do bloqueio.")
            return redirect("platform:user_detail", pk=pk)
        user.is_active = False
        user.blocked_reason = form.cleaned_data["reason"]
        action = "platform.user_block"
    else:
        user.is_active = True
        user.blocked_reason = ""
        action = "platform.user_unblock"
    user.save(update_fields=["is_active", "blocked_reason"])
    log_action(action, request=request, target=user, platform=True, details={"motivo": user.blocked_reason})
    messages.success(request, "Conta bloqueada: o acesso e as sessões abertas foram encerrados." if not user.is_active
                     else "Conta desbloqueada.")
    return redirect("platform:user_detail", pk=pk)


@superadmin_required
@require_POST
def user_access_link(request, pk):
    """Envia link seguro de definição/redefinição de senha. A senha nunca é exibida ao administrador."""
    user = get_object_or_404(User, pk=pk, is_active=True)
    send_access_email(user, reason="invite" if not user.has_usable_password() else "reset")
    log_action("platform.user_access_link", request=request, target=user, platform=True)
    messages.success(request, f"Link de acesso enviado para {user.email}.")
    return redirect("platform:user_detail", pk=pk)


@superadmin_required
def user_invite(request):
    form = InviteUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "Já existe um usuário com este e-mail.")
        else:
            with transaction.atomic():
                user = User.objects.create_user(email=email, full_name=form.cleaned_data["full_name"])
                company = form.cleaned_data.get("company")
                if company:
                    Membership.objects.create(user=user, company=company,
                                              role=form.cleaned_data.get("role") or Membership.Role.OPERATOR)
                log_action("platform.user_invite", request=request, target=user, platform=True,
                           details={"empresa": str(company or "")})
            send_access_email(user, invited_by=request.user, company=company)
            messages.success(request, f"Convite enviado para {email}.")
            return redirect("platform:user_detail", pk=user.pk)
    return render(request, "platform/form.html", {"form": form, "title": "Convidar usuário",
                                                  "back": "platform:users"})


@superadmin_required
def companies(request):
    qs = Company.objects.select_related("plan", "owner").annotate(
        users_n=Count("memberships", filter=Q(memberships__is_active=True), distinct=True))
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(trade_name__icontains=term) | Q(legal_name__icontains=term) | Q(cnpj__icontains=term))
    status = request.GET.get("status", "")
    if status in Company.Status.values:
        qs = qs.filter(status=status)
    plan = request.GET.get("plano", "")
    if plan.isdigit():
        qs = qs.filter(plan_id=int(plan))
    page = Paginator(qs.order_by("-created_at"), 30).get_page(request.GET.get("page"))
    return render(request, "platform/companies.html", {"page_obj": page, "q": term, "status": status,
                                                       "statuses": Company.Status.choices,
                                                       "plans": Plan.objects.all(), "plan": plan})


@superadmin_required
def company_detail(request, pk):
    company = get_object_or_404(Company.objects.select_related("plan", "owner"), pk=pk)
    log_action("platform.company_view", request=request, company=company, target=company, platform=True)
    since = timezone.localdate() - timedelta(days=30)
    sales = SalesOrder.objects.filter(company=company, status__in=SalesOrder.REVENUE_STATUSES)
    agg_total = sales.aggregate(n=Count("id"), total=Sum("total"))
    agg_30 = sales.filter(order_date__gte=since).aggregate(n=Count("id"), total=Sum("total"))
    form = CompanyPlanForm(initial={"plan": company.plan_id, "status": company.status,
                                    "trial_ends_at": company.trial_ends_at,
                                    "suspended_reason": company.suspended_reason})
    return render(request, "platform/company_detail.html", {
        "company": company, "members": company.memberships.select_related("user"),
        "products_n": Product.objects.filter(company=company).count(),
        "sales_total": agg_total, "sales_30": agg_30, "form": form,
        "logs": AuditLog.objects.filter(company=company, platform_scope=True)[:15],
        "redemption": company.active_redemption, "coupon_form": ApplyCouponForm(),
        "coupon_history": company.coupon_redemptions.select_related("coupon")[:10],
        "subscription": current_subscription(company),
    })


@superadmin_required
@require_POST
def company_update(request, pk):
    company = get_object_or_404(Company, pk=pk)
    form = CompanyPlanForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for e in errors:
                messages.error(request, e)
        return redirect("platform:company_detail", pk=pk)
    before = {"plano": str(company.plan or ""), "situacao": company.status,
              "fim_teste": str(company.trial_ends_at or "")}
    company.plan = form.cleaned_data["plan"]
    company.status = form.cleaned_data["status"]
    company.trial_ends_at = form.cleaned_data["trial_ends_at"]
    company.suspended_reason = form.cleaned_data["suspended_reason"] if company.is_suspended else ""
    company.save()
    after = {"plano": str(company.plan or ""), "situacao": company.status,
             "fim_teste": str(company.trial_ends_at or "")}
    log_action("platform.company_update", request=request, company=company, target=company, platform=True,
               details={"antes": before, "depois": after, "motivo": company.suspended_reason})
    messages.success(request, "Empresa atualizada.")
    return redirect("platform:company_detail", pk=pk)


@superadmin_required
def plans(request):
    qs = Plan.objects.annotate(companies_n=Count("companies"))
    return render(request, "platform/plans.html", {"plans": qs})


@superadmin_required
def plan_form(request, pk=None):
    plan = get_object_or_404(Plan, pk=pk) if pk else None
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        log_action("platform.plan_save", request=request, target=obj, platform=True,
                   details={"campos": form.changed_data})
        messages.success(request, "Plano salvo.")
        return redirect("platform:plans")
    return render(request, "platform/form.html", {"form": form, "title": "Plano" if plan else "Novo plano",
                                                  "back": "platform:plans"})


@superadmin_required
def audit(request):
    qs = AuditLog.objects.select_related("actor", "company")
    scope = request.GET.get("escopo", "plataforma")
    if scope == "plataforma":
        qs = qs.filter(platform_scope=True)
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(action__icontains=term) | Q(target_repr__icontains=term) | Q(actor__email__icontains=term)
                       | Q(company__trade_name__icontains=term))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "platform/audit.html", {"page_obj": page, "q": term, "scope": scope})


# ---------------------------------------------------------------------------
# Cadastro direto de usuário com senha provisória
# ---------------------------------------------------------------------------
def _password_page(request, user, password, created: bool):
    """Exibe a senha provisória UMA única vez (resposta direta, sem redirect nem armazenamento)."""
    response = render(request, "platform/provisional_password.html",
                      {"obj": user, "password": password, "created": created,
                       "login_url": request.build_absolute_uri("/conta/entrar/")})
    response["Cache-Control"] = "no-store"
    return response


@superadmin_required
def user_create(request):
    form = CreateUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        company = cd.get("company")
        if company:
            try:
                check_user_limit(company)
            except PlanLimitError as exc:
                form.add_error("company", str(exc))
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.create_user(email=cd["email"], full_name=cd["full_name"],
                                                phone=cd.get("phone", ""))
                password = set_provisional_password(user)
                if company:
                    Membership.objects.create(user=user, company=company,
                                              role=cd.get("role") or Membership.Role.OPERATOR)
                log_action("platform.user_create", request=request, target=user, platform=True,
                           details={"empresa": str(company or ""), "perfil": cd.get("role") or "",
                                    "senha_provisoria": True})
            return _password_page(request, user, password, created=True)
    return render(request, "platform/form.html", {
        "form": form, "title": "Cadastrar usuário", "back": "platform:users",
        "intro": "O usuário é criado já ativo, com uma senha provisória gerada pelo sistema e exibida uma única "
                 "vez. No primeiro acesso ele será obrigado a definir uma senha pessoal."})


@superadmin_required
@require_POST
def user_provisional_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "Para a sua própria conta, use “Alterar senha”.")
        return redirect("platform:user_detail", pk=pk)
    if not user.is_active:
        messages.error(request, "Desbloqueie a conta antes de gerar uma senha provisória.")
        return redirect("platform:user_detail", pk=pk)
    password = set_provisional_password(user)
    log_action("platform.user_provisional_password", request=request, target=user, platform=True)
    return _password_page(request, user, password, created=False)


# ---------------------------------------------------------------------------
# Cupons de desconto na assinatura
# ---------------------------------------------------------------------------
@superadmin_required
def coupons(request):
    qs = Coupon.objects.annotate(uses=Count("redemptions", distinct=True),
                                 active_uses=Count("redemptions", filter=Q(redemptions__is_active=True),
                                                   distinct=True)).prefetch_related("plans").order_by("-created_at")
    status = request.GET.get("status", "")
    if status == "ativos":
        qs = qs.filter(is_active=True)
    elif status == "inativos":
        qs = qs.filter(is_active=False)
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(code__icontains=term) | Q(description__icontains=term))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    stats = {"active": Coupon.objects.filter(is_active=True).count(),
             "companies": CouponRedemption.objects.filter(is_active=True).values("company").distinct().count()}
    return render(request, "platform/coupons.html", {"page_obj": page, "status": status, "q": term, "stats": stats})


@superadmin_required
def coupon_form(request, pk=None):
    coupon = get_object_or_404(Coupon, pk=pk) if pk else None
    form = CouponForm(request.POST or None, instance=coupon)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.pk is None:
            obj.created_by = request.user
        obj.save()
        form.save_m2m()
        log_action("platform.coupon_save", request=request, target=obj, platform=True,
                   details={"codigo": obj.code, "desconto": obj.discount_percent, "campos": form.changed_data})
        messages.success(request, f"Cupom {obj.code} salvo ({obj.discount_percent}% de desconto).")
        return redirect("platform:coupon_detail", pk=obj.pk)
    return render(request, "platform/form.html", {
        "form": form, "title": f"Editar cupom {coupon.code}" if coupon else "Novo cupom", "back": "platform:coupons",
        "intro": "O desconto incide sobre o valor mensal cobrado pelo Mercado Pago quando a empresa assina com o "
                 "cupom aplicado. 100% permanente ativa o plano sem cobrança; 100% com prazo adia a 1ª cobrança."})


@superadmin_required
def coupon_detail(request, pk):
    coupon = get_object_or_404(Coupon.objects.prefetch_related("plans"), pk=pk)
    redemptions = coupon.redemptions.select_related("company", "company__plan", "redeemed_by")
    return render(request, "platform/coupon_detail.html", {"coupon": coupon, "redemptions": redemptions})


@superadmin_required
@require_POST
def coupon_toggle(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    coupon.is_active = not coupon.is_active
    coupon.save(update_fields=["is_active", "updated_at"])
    log_action("platform.coupon_toggle", request=request, target=coupon, platform=True,
               details={"ativo": coupon.is_active})
    messages.success(request, f"Cupom {coupon.code} {'ativado' if coupon.is_active else 'desativado'}. "
                              "Descontos já aplicados às empresas não são alterados.")
    return redirect("platform:coupon_detail", pk=pk)


@superadmin_required
@require_POST
def company_coupon(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if request.POST.get("action") == "remover":
        if remove_coupon(company, user=request.user, request=request):
            messages.success(request, "Cupom removido. A assinatura volta ao preço cheio do plano.")
        else:
            messages.info(request, "A empresa não tem cupom em vigor.")
        return redirect("platform:company_detail", pk=pk)
    form = ApplyCouponForm(request.POST)
    if form.is_valid():
        try:
            redemption = redeem_coupon(company, form.cleaned_data["code"], user=request.user, request=request,
                                       by_platform=True, replace=form.cleaned_data["replace"])
        except CouponError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Cupom {redemption.coupon.code} aplicado: {redemption.coupon.discount_percent}% "
                                      f"de desconto — assinatura de {format_brl(company.effective_monthly_price)} "
                                      "por mês.")
    else:
        messages.error(request, "Informe o código do cupom.")
    return redirect("platform:company_detail", pk=pk)
