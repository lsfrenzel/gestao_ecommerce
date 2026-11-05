from django.urls import path

from . import views

app_name = "marketing"

urlpatterns = [
    path("", views.campaign_list, name="list"),
    path("nova/", views.CampaignCreate.as_view(), name="create"),
    path("<int:pk>/", views.campaign_detail, name="detail"),
    path("<int:pk>/editar/", views.CampaignUpdate.as_view(), name="edit"),
    path("<int:pk>/excluir/", views.CampaignDelete.as_view(), name="delete"),
]
