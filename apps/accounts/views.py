from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.audit import client_ip, log_action

from .forms import (LoginForm, ProfileForm, SignupForm, StyledPasswordChangeForm, StyledPasswordResetForm,
                    StyledSetPasswordForm)
from .models import LoginEvent

logger = logging.getLogger(__name__)


def _safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return fallback


def _after_login_url(user) -> str:
    if user.memberships.filter(is_active=True).exists():
        return reverse_lazy("dashboard:home")
    if user.is_superuser:
        return reverse_lazy("platform:home")
    return reverse_lazy("companies:onboarding")


@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_after_login_url(request.user))
    form = LoginForm(request.POST or None)
    ip = client_ip(request) or "?"
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        idents = [f"ip:{ip}", f"email:{email}"]
        limit = settings.LOGIN_RATE_LIMIT_ATTEMPTS
        if any(ratelimit.is_limited("login", i, limit * (3 if i.startswith("ip") else 1)) for i in idents):
            messages.error(request, "Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente.")
            return render(request, "accounts/login.html", {"form": form}, status=429)
        if form.is_valid():
            user = authenticate(request, email=form.cleaned_data["email"], password=form.cleaned_data["password"])
            if user is not None:
                login(request, user)
                request.session.cycle_key()
                for ident in idents:
                    ratelimit.reset("login", ident)
                LoginEvent.objects.create(user=user, ip_address=client_ip(request))
                return redirect(_safe_next(request, _after_login_url(user)))
        for ident in idents:
            ratelimit.hit("login", ident, settings.LOGIN_RATE_LIMIT_WINDOW)
        logger.info("Falha de login (ip=%s)", ip)
        messages.error(request, "E-mail ou senha inválidos, ou conta bloqueada.")
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "Você saiu da sua conta.")
    return redirect("accounts:login")


@sensitive_post_parameters("password1", "password2")
def signup_view(request):
    if request.user.is_authenticated:
        return redirect(_after_login_url(request.user))
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        ip = client_ip(request) or "?"
        if ratelimit.is_limited("signup", ip, 10):
            messages.error(request, "Muitos cadastros a partir desta rede. Tente novamente mais tarde.")
            return render(request, "accounts/signup.html", {"form": form}, status=429)
        if form.is_valid():
            ratelimit.hit("signup", ip, 60 * 60)
            user = form.save()
            login(request, user, backend="apps.accounts.backends.EmailBackend")
            LoginEvent.objects.create(user=user, ip_address=client_ip(request))
            log_action("user.signup", request=request, target=user)
            messages.success(request, "Conta criada! Agora cadastre sua empresa para começar.")
            return redirect("companies:onboarding")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def profile_view(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Perfil atualizado.")
        return redirect("accounts:profile")
    memberships = request.user.memberships.select_related("company").filter(is_active=True)
    return render(request, "accounts/profile.html", {"form": form, "memberships": memberships})


@login_required
@sensitive_post_parameters()
def password_change_view(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    forced = request.user.must_change_password
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.must_change_password = False
        user.save()
        update_session_auth_hash(request, user)
        log_action("user.password_change", request=request, target=user, details={"provisoria": forced})
        messages.success(request, "Senha alterada com sucesso.")
        return redirect("dashboard:home" if forced else "accounts:profile")
    if forced:
        form.fields["old_password"].label = "Senha provisória"
    return render(request, "accounts/password_change.html", {"form": form, "forced": forced})


class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset.txt"
    subject_template_name = "emails/password_reset_subject.txt"
    form_class = StyledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def post(self, request, *args, **kwargs):
        ip = client_ip(request) or "?"
        email = (request.POST.get("email") or "").strip().lower()
        limit = settings.PASSWORD_RESET_RATE_LIMIT
        window = settings.PASSWORD_RESET_RATE_WINDOW
        if ratelimit.is_limited("pwreset", f"ip:{ip}", limit * 2) or ratelimit.is_limited("pwreset", email, limit):
            messages.error(request, "Muitas solicitações de recuperação. Tente novamente mais tarde.")
            return render(request, self.template_name, {"form": self.get_form()}, status=429)
        ratelimit.hit("pwreset", f"ip:{ip}", window)
        ratelimit.hit("pwreset", email, window)
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        return ctx

    def form_valid(self, form):
        form.save(
            domain_override=None,
            subject_template_name=self.subject_template_name,
            email_template_name=self.email_template_name,
            use_https=self.request.is_secure(),
            from_email=settings.DEFAULT_FROM_EMAIL,
            request=self.request,
            extra_email_context={"platform": settings.PLATFORM_NAME, "site_url": settings.SITE_URL},
        )
        return redirect(self.success_url)


class PasswordResetConfirm(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        form.user.must_change_password = False  # a pessoa definiu a própria senha
        response = super().form_valid(form)
        log_action("user.password_reset", request=self.request, actor=form.user, target=form.user)
        return response
