from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.customer_list, name="list"),
    path("novo/", views.CustomerCreate.as_view(), name="create"),
    path("<int:pk>/", views.customer_detail, name="detail"),
    path("<int:pk>/editar/", views.CustomerUpdate.as_view(), name="edit"),
    path("<int:pk>/excluir/", views.CustomerDelete.as_view(), name="delete"),
    path("<int:pk>/exportar-dados/", views.customer_export, name="export"),
    path("<int:pk>/anonimizar/", views.customer_anonymize, name="anonymize"),
]
