"""Base de formulários: estilo Bootstrap e restrição de chaves estrangeiras ao tenant."""

from __future__ import annotations

from django import forms

from .money import to_decimal


class StyledFormMixin:
    """Aplica classes do Bootstrap 5 aos widgets."""

    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", "form-select")
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                pass
            elif isinstance(widget, forms.ClearableFileInput):
                widget.attrs.setdefault("class", "form-control")
            else:
                widget.attrs.setdefault("class", "form-control")
            if isinstance(field, forms.DecimalField):
                widget.attrs.setdefault("inputmode", "decimal")
                widget.attrs.setdefault("step", "any")


class CompanyScopedFormMixin(StyledFormMixin):
    """
    Restringe todas as opções de ModelChoiceField à empresa ativa.

    Impede que um usuário envie, por exemplo, o id de um fornecedor de outra empresa:
    o valor não estará no queryset e o formulário será inválido.
    """

    def __init__(self, *args, company=None, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            queryset = getattr(field, "queryset", None)
            if queryset is None:
                continue
            model = queryset.model
            if any(f.name == "company" for f in model._meta.get_fields()):
                field.queryset = queryset.filter(company=company) if company else queryset.none()
        self._style_fields()


class StyledForm(StyledFormMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class TenantModelForm(CompanyScopedFormMixin, forms.ModelForm):
    pass


class TenantForm(CompanyScopedFormMixin, forms.Form):
    pass


class BRDecimalField(forms.DecimalField):
    """Aceita vírgula decimal (12,50) e separador de milhar (1.234,56)."""

    def to_python(self, value):
        if isinstance(value, str) and value.strip():
            converted = to_decimal(value, None)
            value = str(converted) if converted is not None else value
        return super().to_python(value)


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")
