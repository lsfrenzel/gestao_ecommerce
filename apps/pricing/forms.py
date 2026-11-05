from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.core.forms import TenantForm, TenantModelForm
from apps.core.money import to_decimal
from apps.products.models import Product

from .models import PricingSimulation

PCT = {"min_value": Decimal("0"), "max_value": Decimal("99.999"), "max_digits": 7, "decimal_places": 3}
MONEY = {"min_value": Decimal("0"), "max_digits": 14, "decimal_places": 4}


class PricingForm(TenantModelForm):
    simulate_prices = forms.CharField(
        label="Preços para simular (R$)", required=False,
        help_text="Separe por ponto e vírgula. Ex.: 79,90; 89,90; 99,90",
        widget=forms.TextInput(attrs={"placeholder": "79,90; 89,90; 99,90"}))

    class Meta:
        model = PricingSimulation
        fields = ["name", "product"] + PricingSimulation.INPUT_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)
        self.fields["product"].required = False
        for name in PricingSimulation.INPUT_FIELDS:
            field = self.fields[name]
            field.required = False
            field.min_value = Decimal("0")
            field.widget.attrs.update({"min": "0", "step": "any"})
            if name.endswith("_pct"):
                field.widget.attrs["max"] = "99.999"

    def clean(self):
        cleaned = super().clean()
        for name in PricingSimulation.INPUT_FIELDS:
            value = cleaned.get(name)
            if value is None:
                cleaned[name] = Decimal("0")
            elif value < 0:
                self.add_error(name, "Não pode ser negativo.")
            elif name.endswith("_pct") and value >= 100:
                self.add_error(name, "Deve ser menor que 100%.")
        prices = []
        for raw in (cleaned.get("simulate_prices") or "").replace("\n", ";").split(";"):
            if not raw.strip():
                continue
            value = to_decimal(raw, None)
            if value is None or value <= 0:
                self.add_error("simulate_prices", f"Preço inválido: “{raw.strip()}”.")
                break
            prices.append(value)
        cleaned["price_list"] = prices[:12]
        return cleaned


class ApplyPriceForm(TenantForm):
    product = forms.ModelChoiceField(label="Produto", queryset=Product.objects.all())
    price = forms.DecimalField(label="Novo preço de venda (R$)", min_value=Decimal("0.01"), max_digits=14,
                               decimal_places=2)


class BreakevenForm(TenantForm):
    MODES = [("single", "Produto individual"), ("mix", "Vários produtos (mix de vendas)")]

    mode = forms.ChoiceField(label="Cenário", choices=MODES, initial="single")
    fixed_costs = forms.DecimalField(label="Despesas fixas mensais (R$)", **MONEY)
    variable_pct = forms.DecimalField(label="Despesas variáveis sobre o preço (%)", required=False, **PCT,
                                      help_text="Impostos, comissões e taxas sobre cada venda.")
    per_unit_costs = forms.DecimalField(label="Despesas variáveis por unidade (R$)", required=False, **MONEY,
                                        help_text="Embalagem, frete subsidiado, separação…")
    product = forms.ModelChoiceField(label="Produto", queryset=Product.objects.all(), required=False)
    manual_price = forms.DecimalField(label="Preço de venda (R$)", required=False, **MONEY,
                                      help_text="Opcional: substitui o preço cadastrado.")
    manual_unit_cost = forms.DecimalField(label="Custo unitário (R$)", required=False, **MONEY,
                                          help_text="Opcional: substitui o custo cadastrado.")
    current_revenue = forms.DecimalField(label="Faturamento mensal atual (R$)", required=False, **MONEY)
    target_profit = forms.DecimalField(label="Meta de resultado mensal (R$)", required=False, max_digits=14,
                                       decimal_places=2)
    fixed_increase_pct = forms.DecimalField(label="Simular aumento das despesas fixas (%)", required=False,
                                            min_value=Decimal("0"), max_value=Decimal("1000"), max_digits=7,
                                            decimal_places=2)
    cm_improvement_pp = forms.DecimalField(label="Simular melhora da margem de contribuição (p.p.)",
                                           required=False, min_value=Decimal("-99"), max_value=Decimal("99"),
                                           max_digits=5, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)
        for f in self.fields.values():
            if isinstance(f, forms.DecimalField):
                f.widget.attrs["step"] = "any"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == "single" and not cleaned.get("product") and not cleaned.get("manual_price"):
            self.add_error("product", "Selecione um produto ou informe preço e custo manualmente.")
        return cleaned


class MixRowForm(TenantForm):
    product = forms.ModelChoiceField(label="Produto", queryset=Product.objects.all(), required=False)
    price = forms.DecimalField(label="Preço (R$)", required=False, min_value=Decimal("0"), max_digits=14,
                               decimal_places=2)
    share_pct = forms.DecimalField(label="Participação (%)", required=False, min_value=Decimal("0"),
                                   max_value=Decimal("100"), max_digits=6, decimal_places=2)


MixFormSet = forms.formset_factory(MixRowForm, extra=0, max_num=30, validate_max=True)
