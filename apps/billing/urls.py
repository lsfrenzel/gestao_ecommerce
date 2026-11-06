from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("assinar/", views.subscribe, name="subscribe"),
    path("retorno/", views.checkout_return, name="return"),
    path("verificar/", views.refresh, name="refresh"),
    path("cancelar/", views.cancel, name="cancel"),
    path("webhook/mercadopago/", views.webhook, name="webhook"),
]
