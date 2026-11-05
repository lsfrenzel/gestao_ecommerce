from django.urls import path

from . import views

app_name = "platform"

urlpatterns = [
    path("", views.home, name="home"),
    path("usuarios/", views.users, name="users"),
    path("usuarios/convidar/", views.user_invite, name="user_invite"),
    path("usuarios/<int:pk>/", views.user_detail, name="user_detail"),
    path("usuarios/<int:pk>/bloqueio/", views.user_block, name="user_block"),
    path("usuarios/<int:pk>/link-acesso/", views.user_access_link, name="user_access_link"),
    path("empresas/", views.companies, name="companies"),
    path("empresas/<int:pk>/", views.company_detail, name="company_detail"),
    path("empresas/<int:pk>/atualizar/", views.company_update, name="company_update"),
    path("planos/", views.plans, name="plans"),
    path("planos/novo/", views.plan_form, name="plan_new"),
    path("planos/<int:pk>/", views.plan_form, name="plan_edit"),
    path("auditoria/", views.audit, name="audit"),
]
