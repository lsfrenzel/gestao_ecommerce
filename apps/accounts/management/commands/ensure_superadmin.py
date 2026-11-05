"""
Cria o primeiro SUPER ADMIN a partir de variáveis de ambiente, se ainda não existir.

    DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD, DJANGO_SUPERUSER_FULL_NAME (opcional)

Nunca altera um usuário existente. Após o primeiro acesso, remova DJANGO_SUPERUSER_PASSWORD.
"""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Cria o SUPER ADMIN inicial a partir de variáveis de ambiente (idempotente)."

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip().lower()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        name = os.environ.get("DJANGO_SUPERUSER_FULL_NAME", "").strip() or "Administrador da plataforma"
        if not email or not password:
            self.stdout.write("ensure_superadmin: variáveis não definidas; nada a fazer.")
            return
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write("ensure_superadmin: usuário já existe; nenhuma alteração feita.")
            return
        try:
            validate_password(password, User(email=email, full_name=name))
        except ValidationError as exc:
            self.stderr.write("ensure_superadmin: senha rejeitada: " + " ".join(exc.messages))
            return
        User.objects.create_superuser(email=email, password=password, full_name=name)
        self.stdout.write(self.style.SUCCESS(f"ensure_superadmin: SUPER ADMIN {email} criado."))
