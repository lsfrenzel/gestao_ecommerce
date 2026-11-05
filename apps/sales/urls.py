from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("", views.order_list, name="list"),
    path("novo/", views.order_form, name="create"),
    path("importar/", views.order_import, name="import"),
    path("importar/modelo.csv", views.import_template, name="import_template"),
    path("<int:pk>/", views.order_detail, name="detail"),
    path("<int:pk>/editar/", views.order_form, name="edit"),
    path("<int:pk>/status/", views.order_status, name="status"),
]
