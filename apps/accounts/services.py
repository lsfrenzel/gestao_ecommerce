"""Convites e links seguros para definição de senha."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def generate_provisional_password() -> str:
    """
    Senha provisória forte e legível (ex.: Kx7m-Qp3w-Tz9b): 12 caracteres aleatórios em 3 grupos,
    com maiúsculas, minúsculas e números, sem caracteres ambíguos (0/O, 1/l/I).
    """
    import secrets
    import string

    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0OolI1")
    while True:
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        if any(c.isupper() for c in raw) and any(c.islower() for c in raw) and any(c.isdigit() for c in raw):
            return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def set_provisional_password(user) -> str:
    """Define uma senha provisória (hash) e exige a troca no próximo acesso. Retorna a senha em texto
    apenas para ser exibida UMA vez ao administrador — ela nunca é armazenada nem registrada em log."""
    password = generate_provisional_password()
    user.set_password(password)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password"])
    return password


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
