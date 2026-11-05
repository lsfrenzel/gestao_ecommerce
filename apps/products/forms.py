from __future__ import annotations

from decimal import Decimal

from django import forms
from django.conf import settings

from apps.core.forms import StyledForm, TenantModelForm

from .models import Category, Product


def validate_image(upload) -> None:
    if not upload or not hasattr(upload, "size"):
        return
    if upload.size > settings.PRODUCT_IMAGE_MAX_MB * 1024 * 1024:
        raise forms.ValidationError(f"A imagem deve ter no máximo {settings.PRODUCT_IMAGE_MAX_MB} MB.")
    from PIL import Image

    try:
        upload.seek(0)
        image = Image.open(upload)
        image.verify()
        if image.format not in ("JPEG", "PNG", "WEBP"):
            raise forms.ValidationError("Formatos aceitos: JPG, PNG ou WebP.")
    except forms.ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise forms.ValidationError("Arquivo de imagem inválido.") from exc
    finally:
        upload.seek(0)


class ProductForm(TenantModelForm):
    initial_stock = forms.DecimalField(label="Estoque inicial", required=False, min_value=Decimal("0"),
                                       max_digits=14, decimal_places=3,
                                       help_text="Gera uma movimentação de “Saldo inicial” no histórico.")

    class Meta:
        model = Product
        fields = ["name", "sku", "barcode", "category", "brand", "supplier", "unit", "description",
                  "acquisition_cost", "purchase_freight", "other_costs", "sale_price", "image",
                  "min_stock", "max_stock", "lead_time_days", "safety_stock", "manual_daily_consumption",
                  "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["initial_stock"]
        else:
            self.fields["initial_stock"].widget.attrs.update({"class": "form-control", "step": "any"})

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip().upper()
        qs = Product.objects.for_company(self.company).filter(sku__iexact=sku)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Já existe um produto com este SKU.")
        return sku

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and image != getattr(self.instance, "image", None):
            validate_image(image)
        return image

    def clean(self):
        cleaned = super().clean()
        min_stock, max_stock = cleaned.get("min_stock"), cleaned.get("max_stock")
        if max_stock is not None and min_stock is not None and max_stock < min_stock:
            self.add_error("max_stock", "O estoque máximo deve ser maior ou igual ao mínimo.")
        return cleaned


class CategoryForm(TenantModelForm):
    class Meta:
        model = Category
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Category.objects.for_company(self.company).filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Categoria já cadastrada.")
        return name


class CsvImportForm(StyledForm):
    file = forms.FileField(label="Arquivo CSV", help_text="Separador ; ou , — codificação UTF-8.")
    update_existing = forms.BooleanField(label="Atualizar produtos existentes (mesmo SKU)", required=False,
                                         initial=True)
