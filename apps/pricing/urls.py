from django.urls import path

from . import views

app_name = "pricing"

urlpatterns = [
    path("", views.calculator, name="calculator"),
    path("aplicar/", views.apply_price, name="apply"),
    path("simulacoes/", views.SimulationList.as_view(), name="simulations"),
    path("simulacoes/<int:pk>/excluir/", views.SimulationDelete.as_view(), name="simulation_delete"),
    path("ponto-de-equilibrio/", views.breakeven_view, name="breakeven"),
]
