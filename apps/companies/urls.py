from django.urls import path

from . import views

app_name = "companies"

urlpatterns = [
    path("empresa/cadastro/", views.onboarding, name="onboarding"),
    path("empresa/trocar/", views.switch_company, name="switch"),
    path("configuracoes/", views.settings_view, name="settings"),
    path("configuracoes/equipe/", views.team, name="team"),
    path("configuracoes/equipe/convidar/", views.invite_member, name="invite"),
    path("configuracoes/equipe/<int:pk>/", views.edit_member, name="edit_member"),
    path("configuracoes/equipe/<int:pk>/link/", views.resend_invite, name="resend_invite"),
    path("configuracoes/integracoes/", views.api_tokens, name="api_tokens"),
    path("configuracoes/integracoes/<int:pk>/revogar/", views.revoke_api_token, name="revoke_api_token"),
    path("configuracoes/depositos/novo/", views.warehouse_form, name="warehouse_new"),
    path("configuracoes/depositos/<int:pk>/", views.warehouse_form, name="warehouse_edit"),
]
