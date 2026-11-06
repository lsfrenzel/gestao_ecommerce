"""Views base com isolamento por empresa e CRUD genérico, além do health check."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError, Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import generic

from .audit import log_action
from .permissions import CompanyAccessMixin

logger = logging.getLogger(__name__)


def healthz(request):
    """Health check: 200 com banco acessível e sem migrações pendentes; 503 caso contrário."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            return JsonResponse({"status": "error", "detail": "migrações pendentes"}, status=503)
    except Exception:  # noqa: BLE001
        logger.exception("healthz: banco indisponível")
        return JsonResponse({"status": "error", "detail": "banco indisponível"}, status=503)
    return JsonResponse({"status": "ok"})


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_403(request, exception=None):
    return render(request, "errors/403.html", {"reason": str(exception or "")}, status=403)


def error_500(request):
    return render(request, "errors/500.html", status=500)


class CompanyScopedMixin(CompanyAccessMixin):
    """Toda consulta passa por `for_company`: objetos de outra empresa resultam em 404."""

    model = None

    def get_queryset(self):
        return self.model.objects.for_company(self.company)


class TenantListView(CompanyScopedMixin, generic.ListView):
    template_name = "core/crud_list.html"
    paginate_by = 25
    search_fields: tuple[str, ...] = ()
    columns: list[tuple[str, str, str]] = []  # (rótulo, atributo, formato: text|brl|pct|qty|date|bool|badge)
    title = ""
    create_url_name: str | None = None
    detail_url_name: str | None = None
    edit_url_name: str | None = None
    delete_url_name: str | None = None
    filters: list[tuple[str, str, list]] = []  # (parâmetro, rótulo, choices)
    extra_actions: list[tuple[str, str, str]] = []  # (url_name, rótulo, ícone)
    locked_attr: str | None = None  # atributo que, se verdadeiro, impede editar/excluir a linha
    empty_message = "Nenhum registro encontrado."

    def get_queryset(self):
        qs = super().get_queryset()
        term = self.request.GET.get("q", "").strip()
        if term and self.search_fields:
            cond = Q()
            for field in self.search_fields:
                cond |= Q(**{f"{field}__icontains": term})
            qs = qs.filter(cond)
        for param, _label, choices in self.filters:
            value = self.request.GET.get(param, "")
            valid = {str(c[0]) for c in choices}
            if value and value in valid:
                qs = qs.filter(**{param: value})
        return qs

    def get_filters(self):
        return self.filters

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            title=self.title or self.model._meta.verbose_name_plural.capitalize(),
            columns=self.columns,
            create_url=reverse(self.create_url_name) if self.create_url_name else None,
            detail_url_name=self.detail_url_name,
            edit_url_name=self.edit_url_name,
            delete_url_name=self.delete_url_name,
            filters=[(p, label, choices, self.request.GET.get(p, "")) for p, label, choices in self.get_filters()],
            search_enabled=bool(self.search_fields),
            q=self.request.GET.get("q", ""),
            extra_actions=self.extra_actions,
            empty_message=self.empty_message,
            can_delete=self.membership.is_manager_or_admin,
            locked_attr=self.locked_attr or "",
        )
        return ctx


class TenantFormMixin(CompanyScopedMixin):
    template_name = "core/crud_form.html"
    success_url_name: str | None = None
    title = ""
    audit_action: str | None = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["company"] = self.company
        return kwargs

    def get_success_url(self):
        if self.success_url_name:
            return reverse(self.success_url_name)
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title or self.model._meta.verbose_name.capitalize()
        ctx["cancel_url"] = self.get_success_url() if self.success_url_name else None
        return ctx


class TenantCreateView(TenantFormMixin, generic.CreateView):
    success_message = "Registro criado com sucesso."

    def form_valid(self, form):
        form.instance.company = self.company
        response = super().form_valid(form)
        if self.audit_action:
            log_action(f"{self.audit_action}.create", request=self.request, target=self.object)
        messages.success(self.request, self.success_message)
        return response


class TenantUpdateView(TenantFormMixin, generic.UpdateView):
    success_message = "Alterações salvas."

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.audit_action:
            log_action(f"{self.audit_action}.update", request=self.request, target=self.object,
                       details={"campos": form.changed_data})
        messages.success(self.request, self.success_message)
        return response


class TenantDeleteView(CompanyScopedMixin, generic.DeleteView):
    """Exclusão sempre via POST com página de confirmação; restrita a gerente/administrador."""

    template_name = "core/confirm_delete.html"
    require_manager = True
    success_url_name: str | None = None
    audit_action: str | None = None
    protected_message = ("Este registro possui movimentações vinculadas e não pode ser excluído. "
                         "Considere arquivá-lo/inativá-lo.")

    def get_success_url(self):
        return reverse(self.success_url_name)

    def form_valid(self, form):
        obj = self.get_object()
        label = str(obj)
        try:
            response = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, self.protected_message)
            return HttpResponseRedirect(self.get_success_url())
        if self.audit_action:
            log_action(f"{self.audit_action}.delete", request=self.request, details={"registro": label})
        messages.success(self.request, f"“{label}” excluído.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cancel_url"] = self.get_success_url()
        return ctx


def get_tenant_object(model, company, **lookup):
    """Busca um objeto da empresa ativa ou 404 (nunca de outra empresa)."""
    try:
        return model.objects.for_company(company).get(**lookup)
    except model.DoesNotExist as exc:
        raise Http404 from exc
