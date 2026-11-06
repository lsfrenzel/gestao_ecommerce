from django.urls import path

from . import views

app_name = "platform"

urlpatterns = [
    path("", views.home, name="home"),
    path("usuarios/", views.users, name="users"),
    path("usuarios/convidar/", views.user_invite, name="user_invite"),
    path("usuarios/cadastrar/", views.user_create, name="user_create"),
    path("usuarios/<int:pk>/", views.user_detail, name="user_detail"),
    path("usuarios/<int:pk>/bloqueio/", views.user_block, name="user_block"),
    path("usuarios/<int:pk>/link-acesso/", views.user_access_link, name="user_access_link"),
    path("usuarios/<int:pk>/senha-provisoria/", views.user_provisional_password, name="user_provisional_password"),
    path("empresas/", views.companies, name="companies"),
    path("empresas/<int:pk>/", views.company_detail, name="company_detail"),
    path("empresas/<int:pk>/atualizar/", views.company_update, name="company_update"),
    path("empresas/<int:pk>/cupom/", views.company_coupon, name="company_coupon"),
    path("cupons/", views.coupons, name="coupons"),
    path("cupons/novo/", views.coupon_form, name="coupon_new"),
    path("cupons/<int:pk>/", views.coupon_detail, name="coupon_detail"),
    path("cupons/<int:pk>/editar/", views.coupon_form, name="coupon_edit"),
    path("cupons/<int:pk>/ativar/", views.coupon_toggle, name="coupon_toggle"),
    path("planos/", views.plans, name="plans"),
    path("planos/novo/", views.plan_form, name="plan_new"),
    path("planos/<int:pk>/", views.plan_form, name="plan_edit"),
    path("auditoria/", views.audit, name="audit"),
]
