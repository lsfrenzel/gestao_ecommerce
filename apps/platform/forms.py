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


class CreateUserForm(StyledForm):
    """Cadastro direto (sem convite): a senha provisória é gerada pelo sistema."""

    full_name = forms.CharField(label="Nome completo", max_length=150)
    email = forms.EmailField(label="E-mail (login)")
    phone = forms.CharField(label="Telefone", max_length=20, required=False)
    company = forms.ModelChoiceField(label="Vincular à empresa (opcional)", queryset=Company.objects.all(),
                                     required=False)
    role = forms.ChoiceField(label="Perfil na empresa", choices=Membership.Role.choices, required=False,
                             initial=Membership.Role.ADMIN)

    def clean_email(self):
        from django.contrib.auth import get_user_model

        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe um usuário com este e-mail.")
        return email


class CouponForm(StyledFormMixin, forms.ModelForm):
    discount_percent = forms.TypedChoiceField(label="Desconto na assinatura", coerce=int,
                                              choices=[(50, "50% de desconto"), (100, "100% de desconto (gratuito)")],
                                              widget=forms.RadioSelect)
    plans = forms.ModelMultipleChoiceField(label="Planos aceitos", queryset=Plan.objects.all(), required=False,
                                           widget=forms.CheckboxSelectMultiple,
                                           help_text="Nenhum marcado = vale para qualquer plano.")

    class Meta:
        from apps.companies.models import Coupon

        model = Coupon
        fields = ["code", "description", "discount_percent", "duration_months", "valid_until", "max_redemptions",
                  "plans", "is_active"]
        widgets = {"valid_until": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["code"].widget.attrs.update({"style": "text-transform:uppercase", "placeholder": "EX.: BEMVINDO50"})
        if not self.instance.pk:
            self.fields["code"].help_text += " Deixe em branco para gerar automaticamente."
            self.fields["code"].required = False

    def clean_code(self):
        import re

        from apps.companies.models import Coupon

        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            import secrets

            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            code = "FT-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,29}", code):
            raise forms.ValidationError("Use de 3 a 30 caracteres: letras, números e hífen.")
        qs = Coupon.objects.filter(code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Já existe um cupom com este código.")
        return code


class ApplyCouponForm(StyledForm):
    code = forms.CharField(label="Código do cupom", max_length=30)
    replace = forms.BooleanField(label="Substituir o cupom atual, se houver", required=False)
