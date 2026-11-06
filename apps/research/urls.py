from django.urls import path

from . import views

app_name = "research"

urlpatterns = [
    path("", views.trend_search, name="trends"),
    path("oportunidade/", views.opportunity, name="opportunity"),
    path("oportunidades/", views.simulations, name="simulations"),
    path("oportunidades/<int:pk>/excluir/", views.OpportunityDelete.as_view(), name="simulation_delete"),
]
