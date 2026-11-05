from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("v1/produtos/", api.products, name="products"),
    path("v1/produtos/<str:sku>/", api.product_detail, name="product_detail"),
    path("v1/pedidos/", api.create_order, name="create_order"),
]
