"""
Controle de acesso no backend: autenticação, empresa ativa, módulo, papel e recurso do plano.

Toda view do ambiente das empresas usa `CompanyAccessMixin` (CBV) ou `company_view` (FBV).
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render


def _check(request, module=None, manager=False, admin=False, feature=None, billing_exempt=False):
    """Retorna uma resposta de bloqueio ou None se o acesso for permitido."""
    user = request.user
    if not user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    membership = getattr(request, "membership", None)
    if membership is None:
        if user.is_superuser:
            return redirect("platform:home")
        return redirect("companies:onboarding")
    company = membership.company
    if company.is_suspended:
        return render(request, "companies/suspended.html", {"company": company}, status=403)
    if not billing_exempt and not company.has_access:
        # Teste grátis vencido ou assinatura inativa: só a tela de assinatura fica disponível.
        return render(request, "billing/required.html", {"company": company, "membership": membership}, status=402)
    if module:
        modules = (module,) if isinstance(module, str) else tuple(module)
        if not any(membership.can(m) for m in modules):  # basta acesso a um dos módulos
            raise PermissionDenied("Seu perfil não tem acesso a este módulo.")
    if admin and not membership.is_admin:
        raise PermissionDenied("Ação restrita ao administrador da empresa.")
    if manager and not membership.is_manager_or_admin:
        raise PermissionDenied("Ação restrita a gerentes e administradores.")
    if feature and not company.has_feature(feature):
        return render(request, "companies/feature_unavailable.html", {"feature": feature}, status=402)
    return None


class CompanyAccessMixin(AccessMixin):
    module: str | None = None
    require_manager = False
    require_admin = False
    feature: str | None = None
    billing_exempt = False

    def dispatch(self, request, *args, **kwargs):
        blocked = _check(request, self.module, self.require_manager, self.require_admin, self.feature,
                         self.billing_exempt)
        if blocked is not None:
            return blocked
        self.company = request.company
        self.membership = request.membership
        return super().dispatch(request, *args, **kwargs)


def company_view(module: str | None = None, *, manager: bool = False, admin: bool = False,
                 feature: str | None = None, billing_exempt: bool = False):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            blocked = _check(request, module, manager, admin, feature, billing_exempt)
            if blocked is not None:
                return blocked
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def superadmin_required(view):
    """Painel da plataforma: somente SUPER ADMIN (is_superuser ativo)."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied("Área exclusiva do administrador da plataforma.")
        return view(request, *args, **kwargs)
    return wrapper
