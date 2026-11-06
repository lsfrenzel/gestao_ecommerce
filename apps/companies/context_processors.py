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
    # O sino só conta alertas dos módulos que o usuário pode abrir.
    allowed = membership.allowed_modules()
    if "inventory" not in allowed:
        alerts.update(out=0, low=0, excess=0, idle=0)
    if "finance" not in allowed:
        alerts["overdue"] = 0
    alerts["total"] = alerts.get("out", 0) + alerts.get("low", 0) + alerts.get("overdue", 0)
    return {
        "current_company": membership.company,
        "current_membership": membership,
        "allowed_modules": membership.allowed_modules(),
        "all_modules": MODULES,
        "alerts": alerts,
        "user_companies": [m.company for m in request.user.memberships.filter(is_active=True)
                           .select_related("company")],
    }
