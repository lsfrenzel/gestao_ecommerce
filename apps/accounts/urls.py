from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.login_view, name="login"),
    path("sair/", views.logout_view, name="logout"),
    path("cadastro/", views.signup_view, name="signup"),
    path("perfil/", views.profile_view, name="profile"),
    path("senha/alterar/", views.password_change_view, name="password_change"),
    path("senha/recuperar/", views.RateLimitedPasswordResetView.as_view(), name="password_reset"),
    path("senha/recuperar/enviado/",
         auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
         name="password_reset_done"),
    path("senha/redefinir/<uidb64>/<token>/", views.PasswordResetConfirm.as_view(),
         name="password_reset_confirm"),
    path("senha/redefinida/",
         auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
         name="password_reset_complete"),
]
