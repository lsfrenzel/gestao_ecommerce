"""Financeiro simplificado: categorias, lançamentos (contas a pagar/receber) e despesas fixas mensais."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel

ZERO = Decimal("0")


class Nature(models.TextChoices):
    REVENUE = "revenue", "Receita"
    FIXED = "fixed", "Despesa fixa"
    VARIABLE = "variable", "Despesa variável"
    PURCHASE = "purchase", "Compra de mercadorias"
    OTHER = "other", "Outros (não operacional)"


class FinancialCategory(TenantModel):
    name = models.CharField("nome", max_length=80)
    nature = models.CharField("natureza", max_length=10, choices=Nature.choices)
    is_system = models.BooleanField("categoria do sistema", default=False, editable=False)

    class Meta:
        ordering = ["nature", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_fin_category")]
        verbose_name = "categoria financeira"
        verbose_name_plural = "categorias financeiras"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_nature_display()})"


class FinancialEntry(TenantModel):
    class Type(models.TextChoices):
        RECEIVABLE = "receivable", "A receber"
        PAYABLE = "payable", "A pagar"

    class Status(models.TextChoices):
        OPEN = "open", "Em aberto"
        SETTLED = "settled", "Liquidado"
        CANCELLED = "cancelled", "Cancelado"

    type = models.CharField("tipo", max_length=10, choices=Type.choices, db_index=True)
    category = models.ForeignKey(FinancialCategory, on_delete=models.PROTECT, related_name="entries",
                                 verbose_name="categoria")
    description = models.CharField("descrição", max_length=200)
    amount = models.DecimalField("valor (R$)", max_digits=14, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    competence_date = models.DateField("competência", help_text="Mês/dia a que a receita ou despesa se refere.")
    due_date = models.DateField("vencimento", db_index=True)
    status = models.CharField("situação", max_length=10, choices=Status.choices, default=Status.OPEN,
                              db_index=True)
    settled_at = models.DateField("data do pagamento/recebimento", null=True, blank=True)
    settled_amount = models.DecimalField("valor pago/recebido (R$)", max_digits=14, decimal_places=2,
                                         null=True, blank=True)
    counterparty = models.CharField("cliente/fornecedor", max_length=150, blank=True)
    notes = models.TextField("observações", blank=True)
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="financial_entries", editable=False)
    purchase_order = models.ForeignKey("purchasing.PurchaseOrder", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="financial_entries",
                                       editable=False)
    fixed_expense = models.ForeignKey("FixedExpense", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="entries", editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["due_date", "id"]
        indexes = [models.Index(fields=["company", "type", "status", "due_date"]),
                   models.Index(fields=["company", "settled_at"]),
                   models.Index(fields=["company", "competence_date"])]
        verbose_name = "lançamento financeiro"
        verbose_name_plural = "lançamentos financeiros"

    def __str__(self) -> str:
        return f"{self.description} — {self.amount}"

    @property
    def nature(self) -> str:
        return self.category.nature

    @property
    def is_overdue(self) -> bool:
        return self.status == self.Status.OPEN and self.due_date < timezone.localdate()

    @property
    def signed_amount(self) -> Decimal:
        value = self.settled_amount if self.settled_amount is not None else self.amount
        return value if self.type == self.Type.RECEIVABLE else -value


class MonthlyGoal(TenantModel):
    """Meta mensal de faturamento e de resultado operacional (comparativo metas × resultados)."""

    month = models.DateField("mês", help_text="Use o primeiro dia do mês.")
    revenue_target = models.DecimalField("meta de faturamento (R$)", max_digits=14, decimal_places=2,
                                         validators=[MinValueValidator(ZERO)])
    profit_target = models.DecimalField("meta de resultado operacional (R$)", max_digits=14, decimal_places=2,
                                        default=ZERO)

    class Meta:
        ordering = ["-month"]
        constraints = [models.UniqueConstraint(fields=["company", "month"], name="uniq_goal_month")]
        verbose_name = "meta mensal"
        verbose_name_plural = "metas mensais"

    def __str__(self) -> str:
        return f"Meta {self.month:%m/%Y}"

    def save(self, *args, **kwargs):
        self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)


class FixedExpense(TenantModel):
    """Despesa fixa mensal recorrente (base do ponto de equilíbrio e geração de contas a pagar)."""

    name = models.CharField("descrição", max_length=120)
    category = models.ForeignKey(FinancialCategory, on_delete=models.PROTECT, related_name="fixed_expenses",
                                 verbose_name="categoria", limit_choices_to={"nature": Nature.FIXED})
    monthly_amount = models.DecimalField("valor mensal (R$)", max_digits=14, decimal_places=2,
                                         validators=[MinValueValidator(ZERO)])
    due_day = models.PositiveSmallIntegerField("dia de vencimento", default=10,
                                               validators=[MinValueValidator(1)])
    is_active = models.BooleanField("ativa", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "despesa fixa"
        verbose_name_plural = "despesas fixas"

    def __str__(self) -> str:
        return self.name
