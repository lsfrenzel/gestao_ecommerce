from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.core.forms import DateInput, StyledForm, TenantModelForm

from .models import FinancialCategory, FinancialEntry, FixedExpense, MonthlyGoal, Nature


class EntryForm(TenantModelForm):
    class Meta:
        model = FinancialEntry
        fields = ["type", "category", "description", "amount", "competence_date", "due_date", "counterparty",
                  "notes"]
        widgets = {"competence_date": DateInput(), "due_date": DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        cleaned = super().clean()
        entry_type, category = cleaned.get("type"), cleaned.get("category")
        if entry_type and category:
            if entry_type == FinancialEntry.Type.RECEIVABLE and category.nature != Nature.REVENUE:
                self.add_error("category", "Contas a receber devem usar uma categoria de receita.")
            if entry_type == FinancialEntry.Type.PAYABLE and category.nature == Nature.REVENUE:
                self.add_error("category", "Contas a pagar não podem usar uma categoria de receita.")
        return cleaned


class SettleForm(StyledForm):
    settled_at = forms.DateField(label="Data do pagamento/recebimento", widget=DateInput())
    amount = forms.DecimalField(label="Valor pago/recebido (R$)", min_value=Decimal("0.01"), max_digits=14,
                                decimal_places=2)


class CategoryForm(TenantModelForm):
    class Meta:
        model = FinancialCategory
        fields = ["name", "nature"]


class FixedExpenseForm(TenantModelForm):
    class Meta:
        model = FixedExpense
        fields = ["name", "category", "monthly_amount", "due_day", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = self.fields["category"].queryset.filter(nature=Nature.FIXED)
        self.fields["due_day"].widget.attrs.update({"min": 1, "max": 31})

    def clean_due_day(self):
        day = self.cleaned_data["due_day"]
        if not 1 <= day <= 31:
            raise forms.ValidationError("Informe um dia entre 1 e 31.")
        return day


class GoalForm(TenantModelForm):
    month = forms.DateField(label="Mês", widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
                            input_formats=["%Y-%m", "%Y-%m-%d"])

    class Meta:
        model = MonthlyGoal
        fields = ["month", "revenue_target", "profit_target"]

    def clean_month(self):
        month = self.cleaned_data["month"].replace(day=1)
        qs = MonthlyGoal.objects.for_company(self.company).filter(month=month)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Já existe meta para este mês.")
        return month


class GenerateMonthForm(StyledForm):
    month = forms.DateField(label="Mês de competência", widget=forms.DateInput(attrs={"type": "month"},
                                                                                format="%Y-%m"),
                            input_formats=["%Y-%m", "%Y-%m-%d"], initial=lambda: timezone.localdate())
