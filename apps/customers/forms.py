from __future__ import annotations

import re

from django import forms

from apps.companies.forms import UF_CHOICES, valid_cnpj
from apps.core.forms import TenantModelForm

from .models import Customer


def valid_cpf(value: str) -> bool:
    d = re.sub(r"\D", "", value or "")
    if len(d) != 11 or d == d[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d[i]) * (size + 1 - i) for i in range(size))
        digit = (total * 10 % 11) % 10
        if digit != int(d[size]):
            return False
    return True


class CustomerForm(TenantModelForm):
    state = forms.ChoiceField(label="UF", choices=UF_CHOICES, required=False)

    class Meta:
        model = Customer
        fields = ["name", "email", "phone", "document", "address", "city", "state", "marketing_consent", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def clean_document(self):
        value = self.cleaned_data.get("document", "").strip()
        if not value:
            return ""
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11 and valid_cpf(digits):
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        if len(digits) == 14 and valid_cnpj(digits):
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
        raise forms.ValidationError("CPF ou CNPJ inválido.")
