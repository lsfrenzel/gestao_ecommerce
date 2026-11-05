from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Autenticação por e-mail (sem diferenciar maiúsculas/minúsculas)."""

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        User = get_user_model()
        login = (email or username or "").strip()
        if not login or not password:
            return None
        try:
            user = User.objects.get(email__iexact=login)
        except User.DoesNotExist:
            User().set_password(password)  # tempo constante contra enumeração de usuários
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
