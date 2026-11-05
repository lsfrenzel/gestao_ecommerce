"""Define `request.company` e `request.membership` a partir dos vínculos do usuário autenticado."""

from __future__ import annotations

SESSION_KEY = "active_company_id"


def resolve_membership(request):
    from .models import Membership

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    memberships = (Membership.objects.select_related("company", "company__plan")
                   .filter(user=user, is_active=True))
    wanted = request.session.get(SESSION_KEY)
    membership = memberships.filter(company_id=wanted).first() if wanted else None
    if membership is None:
        membership = memberships.order_by("created_at").first()
        if membership is not None:
            request.session[SESSION_KEY] = membership.company_id
    return membership


class CurrentCompanyMiddleware:
    """
    Resolve a empresa ativa em toda requisição autenticada. Nenhuma view aceita o id da empresa
    vindo da URL ou de formulários: o tenant é sempre o do vínculo do usuário.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = resolve_membership(request)
        request.company = request.membership.company if request.membership else None
        return self.get_response(request)
