from __future__ import annotations

from django import forms

from apps.companies.forms import format_cnpj, valid_cnpj
from apps.core.forms import DateInput, TenantForm, TenantModelForm
from apps.products.models import Product

from .models import PurchaseOrder, PurchaseOrderItem, Supplier


class SupplierForm(TenantModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "cnpj", "contact_name", "phone", "email", "address", "default_lead_time_days",
                  "notes", "is_active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def clean_cnpj(self):
        value = self.cleaned_data.get("cnpj", "").strip()
        if value and not valid_cnpj(value):
            raise forms.ValidationError("CNPJ inválido.")
        return format_cnpj(value) if value else ""


class PurchaseOrderForm(TenantModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier", "order_date", "expected_date", "freight", "other_costs", "notes"]
        widgets = {"order_date": DateInput(), "expected_date": DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = self.fields["supplier"].queryset.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("expected_date") and cleaned.get("order_date") and \
                cleaned["expected_date"] < cleaned["order_date"]:
            self.add_error("expected_date", "A previsão de entrega não pode ser anterior ao pedido.")
        return cleaned


class PurchaseItemForm(TenantModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ["product", "quantity", "unit_cost"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.for_company(self.company).filter(is_active=True)
        for f in ("quantity", "unit_cost"):
            self.fields[f].widget.attrs["step"] = "any"


class BasePurchaseItemFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        products, count = set(), 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE") or not form.cleaned_data:
                continue
            product = form.cleaned_data.get("product")
            if product in products:
                raise forms.ValidationError("Cada produto deve aparecer uma única vez no pedido.")
            products.add(product)
            count += 1
        if count == 0:
            raise forms.ValidationError("Inclua ao menos um produto.")


PurchaseItemFormSet = forms.inlineformset_factory(
    PurchaseOrder, PurchaseOrderItem, form=PurchaseItemForm, formset=BasePurchaseItemFormSet,
    extra=3, can_delete=True)


class ReceiveForm(TenantForm):
    """Campos dinâmicos: uma quantidade por item pendente."""

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        from apps.companies.models import Warehouse

        if self.company and self.company.multi_warehouse_enabled:
            self.fields["warehouse"] = forms.ModelChoiceField(
                label="Depósito de entrada", queryset=Warehouse.objects.filter(company=self.company, is_active=True))
        for item in order.items.select_related("product"):
            if item.pending_quantity > 0:
                self.fields[f"item_{item.pk}"] = forms.DecimalField(
                    label=f"{item.product.name} ({item.product.sku}) — pendente {item.pending_quantity.normalize():f}",
                    required=False, min_value=0, max_value=item.pending_quantity, max_digits=14, decimal_places=3,
                    initial=item.pending_quantity)
        self._style_fields()

    def quantities(self) -> dict[int, object]:
        return {int(name[5:]): value for name, value in self.cleaned_data.items()
                if name.startswith("item_") and value}
