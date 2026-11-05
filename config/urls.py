from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve

from apps.core import views as core_views

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

urlpatterns = [
    path("healthz", core_views.healthz, name="healthz"),
    path("healthz/", core_views.healthz),
    path("", RedirectView.as_view(pattern_name="dashboard:home", permanent=False)),
    path("conta/", include("apps.accounts.urls")),
    path("", include("apps.companies.urls")),
    path("plataforma/", include("apps.platform.urls")),
    path("painel/", include("apps.dashboard.urls")),
    path("produtos/", include("apps.products.urls")),
    path("precificacao/", include("apps.pricing.urls")),
    path("estoque/", include("apps.inventory.urls")),
    path("compras/", include("apps.purchasing.urls")),
    path("clientes/", include("apps.customers.urls")),
    path("vendas/", include("apps.sales.urls")),
    path("financeiro/", include("apps.finance.urls")),
    path("marketing/", include("apps.marketing.urls")),
    path("relatorios/", include("apps.reports.urls")),
    path("api/", include("apps.core.api_urls")),
    path("django-admin/", admin.site.urls),
]

if settings.DEBUG or settings.SERVE_MEDIA_FROM_DISK:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]
