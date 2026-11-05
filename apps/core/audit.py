"""Auditoria de operações críticas."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .models import AuditLog

SENSITIVE_KEYS = {"password", "senha", "token", "secret", "password1", "password2", "new_password1", "new_password2"}


def client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() in SENSITIVE_KEYS else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def log_action(action: str, *, request=None, actor=None, company=None, target=None,
               details: dict | None = None, platform: bool = False) -> AuditLog:
    if actor is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        actor = request.user
    if company is None and request is not None and not platform:
        company = getattr(request, "company", None)
    return AuditLog.objects.create(
        action=action,
        actor=actor,
        company=company,
        target_type=target.__class__.__name__ if target is not None else "",
        target_id=str(getattr(target, "pk", "") or "") if target is not None else "",
        target_repr=str(target)[:200] if target is not None else "",
        details=_clean(details or {}),
        ip_address=client_ip(request),
        platform_scope=platform,
    )
