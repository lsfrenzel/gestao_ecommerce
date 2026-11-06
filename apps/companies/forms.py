from __future__ import annotations

import re

from django import forms

from apps.core.forms import StyledFormMixin

from .models import MODULES, Company, Membership, Plan, Warehouse

UF_CHOICES = [("", "—")] + [(uf, uf) for uf in (
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())]


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def valid_cnpj(value: str) -> bool:
    digits = only_digits(value)
    if len(digits) != 14 or digits == digits[0] * 14:
        return False

    def calc(base: str, weights: list[int]) -> str:
        total = sum(int(d) * w for d, w in zip(base, weights))
        rest = total % 11
        return "0" if rest < 2 else str(11 - rest)

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1
    d1 = calc(digits[:12], w1)
    d2 = calc(digits[:12] + d1, w2)
    return digits[-2:] == d1 + d2


def format_cnpj(value: str) -> str:
    d = only_digits(value)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else value


class CompanyForm(StyledFormMixin, forms.ModelForm):
    state = forms.ChoiceField(label="UF", choices=UF_CHOICES, required=False)

    class Meta:
        model = Company
        fields = ["trade_name", "legal_name", "cnpj", "segment", "phone", "email",
                  "zip_code", "street", "number", "complement", "district", "city", "state"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["cnpj"].widget.attrs["placeholder"] = "00.000.000/0000-00"
        self.fields["zip_code"].widget.attrs["placeholder"] = "00000-000"

    def clean_cnpj(self):
        value = self.cleaned_data.get("cnpj", "").strip()
        if not value:
            return ""
        if not valid_cnpj(value):
            raise forms.ValidationError("CNPJ inválido.")
        return format_cnpj(value)

    def clean_zip_code(self):
        value = only_digits(self.cleaned_data.get("zip_code", ""))
        if value and len(value) != 8:
            raise forms.ValidationError("CEP deve ter 8 dígitos.")
        return f"{value[:5]}-{value[5:]}" if value else ""


class OnboardingForm(CompanyForm):
    TRIAL = "trial"

    plan_choice = forms.ChoiceField(label="Escolha como começar", initial=TRIAL, widget=forms.RadioSelect,
                                    required=False)  # vazio = teste grátis
    coupon_code = forms.CharField(label="Cupom de desconto (opcional)", max_length=30, required=False)

    def __init__(self, *args, **kwargs):
        from apps.billing import gateway

        super().__init__(*args, **kwargs)
        self.payments_enabled = gateway.is_configured()
        self.plans = list(Plan.objects.filter(is_active=True).order_by("monthly_price", "name"))
        # Todos os planos ativos podem ser escolhidos; o pagamento é feito em seguida no checkout do Mercado Pago.
        self.fields["plan_choice"].choices = [(self.TRIAL, "Teste grátis")] + [(str(p.pk), p.name) for p in self.plans]

    def selected_plan(self):
        value = self.cleaned_data.get("plan_choice")
        if value in (None, "", self.TRIAL):
            return None
        return next((p for p in self.plans if str(p.pk) == value), None)

    def clean_coupon_code(self):
        from .models import Coupon

        code = (self.cleaned_data.get("coupon_code") or "").strip().upper()
        if not code:
            return ""
        coupon = Coupon.objects.filter(code=code).first()
        if coupon is None:
            raise forms.ValidationError("Cupom não encontrado.")
        reason = coupon.unavailable_reason()
        if reason:
            raise forms.ValidationError(reason)
        return code


class RedeemCouponForm(StyledFormMixin, forms.Form):
    code = forms.CharField(label="Código do cupom", max_length=30)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["code"].widget.attrs.update({"style": "text-transform:uppercase", "placeholder": "Ex.: BEMVINDO50"})


class CompanySettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = ["default_tax_rate", "default_target_margin", "consumption_window_days", "idle_stock_days",
                  "reserve_on_pending", "multi_warehouse_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        if not self.instance.has_feature("multi_warehouse"):
            self.fields["multi_warehouse_enabled"].disabled = True
            self.fields["multi_warehouse_enabled"].help_text = "Recurso não incluído no plano atual."


class InviteMemberForm(StyledFormMixin, forms.Form):
    full_name = forms.CharField(label="Nome completo", max_length=150)
    email = forms.EmailField(label="E-mail")
    role = forms.ChoiceField(label="Perfil", choices=Membership.Role.choices)
    modules = forms.MultipleChoiceField(label="Módulos permitidos", choices=MODULES, required=False,
                                        widget=forms.CheckboxSelectMultiple,
                                        help_text="Vazio = padrão do perfil. Administradores acessam tudo.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class MembershipForm(StyledFormMixin, forms.ModelForm):
    modules = forms.MultipleChoiceField(label="Módulos permitidos", choices=MODULES, required=False,
                                        widget=forms.CheckboxSelectMultiple,
                                        help_text="Vazio = padrão do perfil. Administradores acessam tudo.")

    class Meta:
        model = Membership
        fields = ["role", "modules", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class WarehouseForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["name", "is_active"]

    def __init__(self, *args, company=None, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Warehouse.objects.filter(company=self.company, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Já existe um depósito com este nome.")
        return name
