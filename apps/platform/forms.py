from __future__ import annotations

from django import forms

from apps.companies.models import PLAN_FEATURES, Company, Membership, Plan
from apps.core.forms import DateInput, StyledForm, StyledFormMixin


class PlanForm(StyledFormMixin, forms.ModelForm):
    features = forms.MultipleChoiceField(label="Recursos disponíveis", choices=PLAN_FEATURES, required=False,
                                         widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Plan
        fields = ["name", "description", "monthly_price", "max_users", "max_products", "features", "trial_days",
                  "is_active", "is_default"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class CompanyPlanForm(StyledForm):
    plan = forms.ModelChoiceField(label="Plano", queryset=Plan.objects.filter(is_active=True), required=False)
    status = forms.ChoiceField(label="Situação", choices=Company.Status.choices)
    trial_ends_at = forms.DateField(label="Fim do período de teste", required=False, widget=DateInput())
    suspended_reason = forms.CharField(label="Motivo da suspensão", max_length=200, required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == Company.Status.SUSPENDED and not cleaned.get("suspended_reason"):
            self.add_error("suspended_reason", "Informe o motivo da suspensão.")
        return cleaned


class BlockForm(StyledForm):
    reason = forms.CharField(label="Motivo do bloqueio", max_length=200)


class InviteUserForm(StyledForm):
    full_name = forms.CharField(label="Nome completo", max_length=150)
    email = forms.EmailField(label="E-mail")
    company = forms.ModelChoiceField(label="Vincular à empresa (opcional)", queryset=Company.objects.all(),
                                     required=False)
    role = forms.ChoiceField(label="Perfil na empresa", choices=Membership.Role.choices, required=False)
