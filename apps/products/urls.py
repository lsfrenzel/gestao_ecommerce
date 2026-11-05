from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path("", views.product_list, name="list"),
    path("novo/", views.product_create, name="create"),
    path("importar/", views.product_import, name="import"),
    path("importar/modelo.csv", views.import_template, name="import_template"),
    path("<int:pk>/", views.product_detail, name="detail"),
    path("<int:pk>/editar/", views.product_edit, name="edit"),
    path("<int:pk>/arquivar/", views.product_toggle_active, name="toggle_active"),
    path("<int:pk>/excluir/", views.ProductDelete.as_view(), name="delete"),
    path("categorias/", views.CategoryList.as_view(), name="categories"),
    path("categorias/nova/", views.CategoryCreate.as_view(), name="category_new"),
    path("categorias/<int:pk>/", views.CategoryUpdate.as_view(), name="category_edit"),
    path("categorias/<int:pk>/excluir/", views.CategoryDelete.as_view(), name="category_delete"),
]
