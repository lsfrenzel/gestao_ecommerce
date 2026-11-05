"""Usuário com login por e-mail e registro de acessos."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superusuário precisa de is_staff=True e is_superuser=True.")
        return self._create_user(email, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(email__iexact=username)


class User(AbstractBaseUser, PermissionsMixin):
    """
    `is_superuser` = SUPER ADMIN da plataforma.
    `is_active=False` = conta bloqueada (não consegue entrar).
    """

    email = models.EmailField("e-mail", unique=True)
    full_name = models.CharField("nome completo", max_length=150)
    phone = models.CharField("telefone", max_length=20, blank=True)
    is_active = models.BooleanField("ativo", default=True)
    is_staff = models.BooleanField("acesso ao admin técnico", default=False)
    date_joined = models.DateTimeField("cadastrado em", default=timezone.now)
    blocked_reason = models.CharField("motivo do bloqueio", max_length=200, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name"]
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self) -> str:
        return self.full_name or self.email

    @property
    def first_name(self) -> str:
        return (self.full_name or self.email).split(" ")[0]

    @property
    def initials(self) -> str:
        parts = (self.full_name or self.email).split()
        return ((parts[0][0] if parts else "") + (parts[-1][0] if len(parts) > 1 else "")).upper()


class LoginEvent(models.Model):
    """Acessos bem-sucedidos (para indicadores do painel da plataforma)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_events")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "acesso"
        verbose_name_plural = "acessos"
