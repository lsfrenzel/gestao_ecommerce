from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("movimentar/", views.movement_create, name="move"),
    path("transferir/", views.transfer_create, name="transfer"),
    path("historico/", views.history, name="history"),
    path("alertas/", views.alerts, name="alerts"),
    path("ponto-de-pedido/", views.reorder, name="reorder"),
    path("ponto-de-pedido/<int:pk>/", views.reorder_edit, name="reorder_edit"),
    path("ponto-de-pedido/gerar-compras/", views.reorder_create_purchases, name="reorder_purchases"),
]
