from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.companies.models import Warehouse
from apps.core.forms import TenantForm
from apps.products.models import Product

from .models import StockMovement

K = StockMovement.Kind
MANUAL_CHOICES = [
    (K.IN, "Entrada de produtos"),
    (K.OUT, "Saída de produtos"),
    ("count", "Ajuste por contagem (inventário)"),
    (K.ADJUST_IN, "Ajuste (+) manual"),
    (K.ADJUST_OUT, "Ajuste (−) manual"),
    (K.RETURN, "Devolução de cliente (sem pedido)"),
    (K.LOSS, "Registro de perda/avaria"),
]


class MovementForm(TenantForm):
    product = forms.ModelChoiceField(label="Produto", queryset=Product.objects.all())
    kind = forms.ChoiceField(label="Tipo de movimentação", choices=MANUAL_CHOICES)
    quantity = forms.DecimalField(label="Quantidade (ou contagem física, no ajuste por contagem)",
                                  min_value=Decimal("0"), max_digits=14, decimal_places=3)
    unit_cost = forms.DecimalField(label="Custo unitário (R$)", required=False, min_value=Decimal("0"),
                                   max_digits=14, decimal_places=4,
                                   help_text="Opcional. Vazio = custo atual do produto.")
    warehouse = forms.ModelChoiceField(label="Depósito", queryset=Warehouse.objects.all(), required=False)
    note = forms.CharField(label="Observação / motivo", max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)
        self.fields["warehouse"].queryset = self.fields["warehouse"].queryset.filter(is_active=True)
        if not (self.company and self.company.multi_warehouse_enabled):
            del self.fields["warehouse"]
        for f in ("quantity", "unit_cost"):
            self.fields[f].widget.attrs["step"] = "any"

    def clean(self):
        cleaned = super().clean()
        kind, qty = cleaned.get("kind"), cleaned.get("quantity")
        if qty is not None and kind != "count" and qty <= 0:
            self.add_error("quantity", "A quantidade deve ser maior que zero.")
        if kind in (K.LOSS, K.ADJUST_IN, K.ADJUST_OUT) and not cleaned.get("note"):
            self.add_error("note", "Informe o motivo para ajustes e perdas.")
        return cleaned


class TransferForm(TenantForm):
    product = forms.ModelChoiceField(label="Produto", queryset=Product.objects.all())
    origin = forms.ModelChoiceField(label="Depósito de origem", queryset=Warehouse.objects.all())
    destination = forms.ModelChoiceField(label="Depósito de destino", queryset=Warehouse.objects.all())
    quantity = forms.DecimalField(label="Quantidade", min_value=Decimal("0.001"), max_digits=14, decimal_places=3)
    note = forms.CharField(label="Observação", max_length=255, required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("origin") and cleaned.get("origin") == cleaned.get("destination"):
            self.add_error("destination", "Origem e destino devem ser diferentes.")
        return cleaned


class ReorderSettingsForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["lead_time_days", "safety_stock", "manual_daily_consumption", "min_stock", "max_stock"]
