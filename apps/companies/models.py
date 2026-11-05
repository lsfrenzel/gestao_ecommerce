"""Empresas (tenants), planos SaaS e vínculos de usuários com papéis."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import TimeStampedModel

# ---------------------------------------------------------------------------
# Módulos e papéis
# ---------------------------------------------------------------------------
MODULES = [
    ("dashboard", "Dashboard"),
    ("products", "Produtos"),
    ("pricing", "Precificação"),
    ("inventory", "Estoque"),
    ("reorder", "Ponto de pedido"),
    ("suppliers", "Fornecedores"),
    ("purchasing", "Compras"),
    ("sales", "Vendas"),
    ("customers", "Clientes"),
    ("finance", "Financeiro"),
    ("marketing", "Marketing"),
    ("reports", "Relatórios"),
    ("settings", "Configurações"),
]
MODULE_KEYS = [key for key, _ in MODULES]
MODULE_LABELS = dict(MODULES)

DEFAULT_MODULES = {
    "admin": MODULE_KEYS,
    "manager": [m for m in MODULE_KEYS if m != "settings"],
    "operator": ["dashboard", "products", "inventory", "sales", "customers"],
}

PLAN_FEATURES = [
    ("pricing", "Precificação inteligente"),
    ("breakeven", "Ponto de equilíbrio"),
    ("inventory", "Estoque e ponto de pedido"),
    ("purchasing", "Fornecedores e compras"),
    ("finance", "Financeiro"),
    ("marketing", "Marketing"),
    ("reports_pdf", "Relatórios em PDF"),
    ("csv_import", "Importação CSV"),
    ("multi_warehouse", "Múltiplos depósitos"),
]


class Plan(TimeStampedModel):
    name = models.CharField("nome", max_length=80, unique=True)
    description = models.TextField("descrição", blank=True)
    monthly_price = models.DecimalField("preço mensal (R$)", max_digits=10, decimal_places=2, default=Decimal("0"),
                                        validators=[MinValueValidator(Decimal("0"))])
    max_users = models.PositiveIntegerField("limite de usuários", null=True, blank=True,
                                            help_text="Vazio = ilimitado.")
    max_products = models.PositiveIntegerField("limite de produtos", null=True, blank=True,
                                               help_text="Vazio = ilimitado.")
    features = models.JSONField("recursos disponíveis", default=list, blank=True)
    trial_days = models.PositiveIntegerField("período de teste (dias)", default=14)
    is_active = models.BooleanField("ativo", default=True)
    is_default = models.BooleanField("plano padrão para novos cadastros", default=False)

    class Meta:
        ordering = ["monthly_price", "name"]
        verbose_name = "plano"
        verbose_name_plural = "planos"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_default:
                Plan.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
            super().save(*args, **kwargs)

    @property
    def feature_labels(self) -> list[str]:
        labels = dict(PLAN_FEATURES)
        return [labels.get(f, f) for f in self.features]

    @classmethod
    def default(cls) -> "Plan | None":
        return cls.objects.filter(is_active=True, is_default=True).first() or cls.objects.filter(is_active=True).first()


class Company(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "trial", "Em teste"
        ACTIVE = "active", "Ativa"
        SUSPENDED = "suspended", "Suspensa"

    SEGMENTS = [
        ("moda", "Moda e vestuário"),
        ("eletronicos", "Eletrônicos"),
        ("casa", "Casa e decoração"),
        ("beleza", "Beleza e cuidados"),
        ("alimentos", "Alimentos e bebidas"),
        ("esportes", "Esportes e lazer"),
        ("pet", "Pet"),
        ("infantil", "Infantil"),
        ("papelaria", "Papelaria e presentes"),
        ("servicos", "Serviços"),
        ("outros", "Outros"),
    ]

    trade_name = models.CharField("nome fantasia", max_length=120)
    legal_name = models.CharField("razão social", max_length=160, blank=True)
    cnpj = models.CharField("CNPJ", max_length=18, blank=True)
    segment = models.CharField("segmento de atuação", max_length=30, choices=SEGMENTS, default="outros")
    phone = models.CharField("telefone", max_length=20, blank=True)
    email = models.EmailField("e-mail comercial", blank=True)
    zip_code = models.CharField("CEP", max_length=9, blank=True)
    street = models.CharField("logradouro", max_length=160, blank=True)
    number = models.CharField("número", max_length=20, blank=True)
    complement = models.CharField("complemento", max_length=80, blank=True)
    district = models.CharField("bairro", max_length=80, blank=True)
    city = models.CharField("cidade", max_length=80, blank=True)
    state = models.CharField("UF", max_length=2, blank=True)

    # Configurações da empresa
    default_tax_rate = models.DecimalField(
        "impostos sobre vendas (%)", max_digits=6, decimal_places=3, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Aplicado automaticamente às novas vendas e às simulações de preço.")
    default_target_margin = models.DecimalField(
        "margem de contribuição desejada padrão (%)", max_digits=6, decimal_places=3, default=Decimal("30"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("99"))])
    consumption_window_days = models.PositiveIntegerField(
        "janela para consumo médio (dias)", default=90,
        validators=[MinValueValidator(7), MaxValueValidator(730)],
        help_text="Período do histórico de vendas usado para estimar o consumo médio diário.")
    idle_stock_days = models.PositiveIntegerField(
        "alerta de produto sem movimentação (dias)", default=60, validators=[MinValueValidator(7)])
    multi_warehouse_enabled = models.BooleanField("habilitar múltiplos depósitos", default=False)
    reserve_on_pending = models.BooleanField(
        "reservar estoque em pedidos pendentes", default=True,
        help_text="Pedidos pendentes/aguardando pagamento reservam o estoque disponível.")

    # Conta SaaS
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.SET_NULL, related_name="companies",
                             verbose_name="plano")
    status = models.CharField("situação", max_length=12, choices=Status.choices, default=Status.TRIAL,
                              db_index=True)
    trial_ends_at = models.DateField("fim do período de teste", null=True, blank=True)
    suspended_reason = models.CharField("motivo da suspensão", max_length=200, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="owned_companies", verbose_name="responsável")

    # Sequências por empresa (numeração de documentos)
    next_order_number = models.PositiveIntegerField(default=1, editable=False)
    next_purchase_number = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["trade_name"]
        verbose_name = "empresa"
        verbose_name_plural = "empresas"

    def __str__(self) -> str:
        return self.trade_name

    @property
    def is_suspended(self) -> bool:
        return self.status == self.Status.SUSPENDED

    @property
    def address_display(self) -> str:
        parts = [self.street, self.number, self.complement, self.district, self.city, self.state]
        return ", ".join(p for p in parts if p)

    def has_feature(self, feature: str) -> bool:
        if self.plan is None:
            return True  # sem plano atribuído: sem restrição de recursos
        return feature in (self.plan.features or [])

    def start_trial(self, plan: Plan | None) -> None:
        self.plan = plan
        self.status = self.Status.TRIAL
        days = plan.trial_days if plan else 14
        self.trial_ends_at = timezone.localdate() + timedelta(days=days)

    def take_sequence(self, field: str) -> int:
        """Reserva o próximo número de documento de forma atômica (sem duplicidade sob concorrência)."""
        with transaction.atomic():
            locked = Company.objects.select_for_update().get(pk=self.pk)
            value = getattr(locked, field)
            setattr(locked, field, value + 1)
            locked.save(update_fields=[field])
        setattr(self, field, value + 1)
        return value


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrador da empresa"
        MANAGER = "manager", "Gerente"
        OPERATOR = "operator", "Operador"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships",
                             verbose_name="usuário")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships",
                                verbose_name="empresa")
    role = models.CharField("perfil", max_length=10, choices=Role.choices, default=Role.OPERATOR)
    modules = models.JSONField("módulos permitidos", default=list, blank=True)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "company"], name="uniq_membership_user_company")]
        ordering = ["company__trade_name"]
        verbose_name = "vínculo"
        verbose_name_plural = "vínculos"

    def __str__(self) -> str:
        return f"{self.user} @ {self.company} ({self.get_role_display()})"

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    @property
    def is_manager_or_admin(self) -> bool:
        return self.role in (self.Role.ADMIN, self.Role.MANAGER)

    def allowed_modules(self) -> list[str]:
        if self.role == self.Role.ADMIN:
            return list(MODULE_KEYS)
        allowed = self.modules if self.modules else DEFAULT_MODULES.get(self.role, [])
        if self.role != self.Role.ADMIN:
            allowed = [m for m in allowed if m != "settings"]
        return [m for m in MODULE_KEYS if m in allowed]

    def can(self, module: str) -> bool:
        return self.is_active and module in self.allowed_modules()


class ApiToken(TimeStampedModel):
    """
    Token de integração (API REST) de uma empresa. Somente o hash SHA-256 é armazenado;
    o valor é exibido uma única vez na criação.
    """

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField("identificação", max_length=80)
    prefix = models.CharField(max_length=8, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "token de API"
        verbose_name_plural = "tokens de API"

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @staticmethod
    def hash(raw: str) -> str:
        import hashlib

        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, company: Company, name: str, user) -> tuple["ApiToken", str]:
        import secrets

        raw = "gs_" + secrets.token_urlsafe(32)
        token = cls.objects.create(company=company, name=name, prefix=raw[:8], key_hash=cls.hash(raw),
                                   created_by=user)
        return token, raw


class Warehouse(TimeStampedModel):
    """Depósito. Toda empresa possui um depósito principal; outros só com o recurso habilitado."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="warehouses",
                                verbose_name="empresa")
    name = models.CharField("nome", max_length=80)
    is_default = models.BooleanField("principal", default=False)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        ordering = ["-is_default", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_warehouse_name"),
            models.UniqueConstraint(fields=["company"], condition=models.Q(is_default=True),
                                    name="uniq_default_warehouse"),
        ]
        verbose_name = "depósito"
        verbose_name_plural = "depósitos"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def default_for(cls, company: Company) -> "Warehouse":
        warehouse = cls.objects.filter(company=company, is_default=True).first()
        if warehouse is None:
            warehouse, _ = cls.objects.get_or_create(company=company, name="Principal",
                                                     defaults={"is_default": True})
        return warehouse
