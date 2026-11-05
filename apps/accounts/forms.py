from __future__ import annotations

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm

from apps.core.forms import StyledFormMixin

from .models import User


class SignupForm(StyledFormMixin, forms.ModelForm):
    password1 = forms.CharField(label="Senha", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirme a senha",
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    accept_terms = forms.BooleanField(
        label="Li e aceito os termos de uso e a política de privacidade (LGPD).", required=True)

    class Meta:
        model = User
        fields = ["full_name", "email"]
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "email"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe uma conta com este e-mail. Use “Esqueci minha senha”.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "As senhas não conferem.")
        if p1:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class LoginForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={"autocomplete": "email",
                                                                           "autofocus": True}))
    password = forms.CharField(label="Senha", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def get_users(self, email):
        # Inclui usuários convidados (senha ainda não definida), mas nunca contas bloqueadas.
        return User.objects.filter(email__iexact=email, is_active=True)


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
