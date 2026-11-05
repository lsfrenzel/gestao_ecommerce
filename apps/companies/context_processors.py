from .models import MODULES


def company(request):
    membership = getattr(request, "membership", None)
    if not membership:
        return {"current_company": None, "current_membership": None, "allowed_modules": []}
    from apps.inventory.alerts import alert_counts

    try:
        alerts = alert_counts(membership.company)
    except Exception:  # noqa: BLE001 — o menu nunca deve derrubar a página
        alerts = {}
    return {
        "current_company": membership.company,
        "current_membership": membership,
        "allowed_modules": membership.allowed_modules(),
        "all_modules": MODULES,
        "alerts": alerts,
        "user_companies": [m.company for m in request.user.memberships.filter(is_active=True)
                           .select_related("company")],
    }
