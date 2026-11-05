from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("contas/<str:kind>/", views.entry_list, name="entries"),
    path("lancamentos/novo/", views.entry_form, name="entry_new"),
    path("lancamentos/<int:pk>/", views.entry_form, name="entry_edit"),
    path("lancamentos/<int:pk>/liquidar/", views.entry_settle, name="entry_settle"),
    path("lancamentos/<int:pk>/cancelar/", views.entry_cancel, name="entry_cancel"),
    path("categorias/", views.CategoryList.as_view(), name="categories"),
    path("categorias/nova/", views.CategoryCreate.as_view(), name="category_new"),
    path("categorias/<int:pk>/", views.CategoryUpdate.as_view(), name="category_edit"),
    path("categorias/<int:pk>/excluir/", views.CategoryDelete.as_view(), name="category_delete"),
    path("despesas-fixas/", views.fixed_expenses, name="fixed"),
    path("despesas-fixas/nova/", views.FixedCreate.as_view(), name="fixed_new"),
    path("despesas-fixas/<int:pk>/", views.FixedUpdate.as_view(), name="fixed_edit"),
    path("despesas-fixas/<int:pk>/excluir/", views.FixedDelete.as_view(), name="fixed_delete"),
    path("metas/", views.GoalList.as_view(), name="goals"),
    path("metas/nova/", views.GoalCreate.as_view(), name="goal_new"),
    path("metas/<int:pk>/", views.GoalUpdate.as_view(), name="goal_edit"),
    path("metas/<int:pk>/excluir/", views.GoalDelete.as_view(), name="goal_delete"),
]
