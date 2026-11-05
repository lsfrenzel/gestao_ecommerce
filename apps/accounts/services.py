"""Convites e links seguros para definição de senha."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def password_link(user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return settings.SITE_URL + reverse("accounts:password_reset_confirm", kwargs={"uidb64": uid, "token": token})


def send_access_email(user, *, invited_by=None, company=None, reason: str = "invite") -> None:
    """
    Envia um link de uso único para o usuário definir a senha.
    Nunca envia senha por e-mail e nunca expõe a senha a administradores.
    """
    context = {
        "user": user,
        "link": password_link(user),
        "invited_by": invited_by,
        "company": company,
        "reason": reason,
        "platform": settings.PLATFORM_NAME,
        "days": settings.PASSWORD_RESET_TIMEOUT // 86400,
    }
    subject = (f"Convite para acessar {company or settings.PLATFORM_NAME}" if reason == "invite"
               else f"Redefinição de acesso — {settings.PLATFORM_NAME}")
    body = render_to_string("emails/access_link.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
