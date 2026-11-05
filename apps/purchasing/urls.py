from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.order_list, name="orders"),
    path("nova/", views.order_form, name="order_new"),
    path("<int:pk>/", views.order_detail, name="order_detail"),
    path("<int:pk>/editar/", views.order_form, name="order_edit"),
    path("<int:pk>/<str:action>/", views.order_action, name="order_action"),
    path("fornecedores/", views.SupplierList.as_view(), name="suppliers"),
    path("fornecedores/novo/", views.SupplierCreate.as_view(), name="supplier_new"),
    path("fornecedores/<int:pk>/", views.supplier_detail, name="supplier_detail"),
    path("fornecedores/<int:pk>/editar/", views.SupplierUpdate.as_view(), name="supplier_edit"),
    path("fornecedores/<int:pk>/excluir/", views.SupplierDelete.as_view(), name="supplier_delete"),
]
