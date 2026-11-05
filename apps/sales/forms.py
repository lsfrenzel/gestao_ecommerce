from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.core.forms import DateInput, TenantForm, TenantModelForm
from apps.products.models import Product

from .models import SalesOrder

INITIAL_STATUSES = [
    (SalesOrder.Status.PENDING, "Pendente"),
    (SalesOrder.Status.AWAITING_PAYMENT, "Aguardando pagamento"),
    (SalesOrder.Status.PAID, "Pago"),
]


class SalesOrderForm(TenantModelForm):
    initial_status = forms.ChoiceField(label="Status inicial", choices=INITIAL_STATUSES, required=False)
    auto_taxes = forms.BooleanField(label="Calcular impostos pela alíquota padrão da empresa", required=False,
                                    initial=True)

    class Meta:
        model = SalesOrder
        fields = ["order_date", "customer", "channel", "external_reference", "payment_method", "campaign",
                  "discount", "shipping_charged", "shipping_cost", "commission", "fees", "taxes", "notes"]
        widgets = {"order_date": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["initial_status"]
        for name in ("discount", "shipping_charged", "shipping_cost", "commission", "fees", "taxes"):
            self.fields[name].widget.attrs.update({"step": "0.01", "min": "0"})


class OrderItemForm(TenantForm):
    product = forms.ModelChoiceField(label="Produto", queryset=Product.objects.all())
    quantity = forms.DecimalField(label="Qtd.", min_value=Decimal("0.001"), max_digits=14, decimal_places=3)
    unit_price = forms.DecimalField(label="Preço unit. (R$)", min_value=Decimal("0"), max_digits=14,
                                    decimal_places=2, required=False,
                                    help_text="Vazio = preço de venda cadastrado.")
    discount = forms.DecimalField(label="Desconto (R$)", min_value=Decimal("0"), max_digits=14, decimal_places=2,
                                  required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)
        for f in ("quantity", "unit_price", "discount"):
            self.fields[f].widget.attrs["step"] = "any"


class BaseItemFormSet(forms.BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        rows = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not rows:
            raise forms.ValidationError("Inclua ao menos um produto no pedido.")
        products = [r["product"].pk for r in rows]
        if len(products) != len(set(products)):
            raise forms.ValidationError("Cada produto deve aparecer uma única vez no pedido.")

    def items(self) -> list[dict]:
        out = []
        for f in self.forms:
            cd = f.cleaned_data
            if not cd or cd.get("DELETE"):
                continue
            price = cd.get("unit_price")
            out.append({"product": cd["product"], "quantity": cd["quantity"],
                        "unit_price": price if price is not None else cd["product"].sale_price,
                        "discount": cd.get("discount") or Decimal("0")})
        return out


OrderItemFormSet = forms.formset_factory(OrderItemForm, formset=BaseItemFormSet, extra=1, can_delete=True,
                                         max_num=100, validate_max=True)


class StatusForm(forms.Form):
    status = forms.ChoiceField(label="Novo status")
    restock = forms.BooleanField(label="Devolver os itens ao estoque (produto em condições de revenda)",
                                 required=False, initial=True)
    note = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = order.allowed_transitions() if order else []
        self.fields["status"].widget.attrs["class"] = "form-select"
        self.fields["note"].widget.attrs["class"] = "form-control"
        self.fields["restock"].widget.attrs["class"] = "form-check-input"
