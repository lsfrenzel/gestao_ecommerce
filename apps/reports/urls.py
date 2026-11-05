from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.report_view, name="report"),
]
