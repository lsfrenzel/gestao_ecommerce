from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.companies.services import PlanLimitError, check_product_limit
from apps.core.audit import log_action
from apps.core.csv_import import read_csv
from apps.core.exports import csv_response
from apps.core.permissions import company_view
from apps.core.views import TenantCreateView, TenantDeleteView, TenantListView, TenantUpdateView, get_tenant_object
from apps.inventory import services as stock
from apps.dashboard.metrics import product_ranking
from apps.inventory.models import StockMovement
from apps.inventory.reorder import build_rows

from . import services
from .forms import CategoryForm, CsvImportForm, ProductForm
from .models import Category, Product


def _filtered_products(request):
    company = request.company
    qs = Product.objects.for_company(company).select_related("category", "supplier")
    term = request.GET.get("q", "").strip()
    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(sku__icontains=term) | Q(barcode__icontains=term))
    category = request.GET.get("categoria")
    if category and category.isdigit():
        qs = qs.filter(category_id=int(category))
    status = request.GET.get("situacao", "ativos")
    if status == "ativos":
        qs = qs.filter(is_active=True)
    elif status == "inativos":
        qs = qs.filter(is_active=False)
    return qs, status


@company_view("products")
def product_list(request):
    qs, status = _filtered_products(request)
    if request.GET.get("exportar") == "csv":
        return csv_response(services.export_report(qs), "produtos.csv")
    page = Paginator(qs.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(request, "products/list.html", {
        "page_obj": page, "categories": Category.objects.for_company(request.company),
        "status": status, "q": request.GET.get("q", ""), "category": request.GET.get("categoria", ""),
        "total": qs.count(),
    })


@company_view("products")
def product_create(request):
    company = request.company
    try:
        check_product_limit(company)
    except PlanLimitError as exc:
        messages.error(request, str(exc))
        return redirect("products:list")
    form = ProductForm(request.POST or None, request.FILES or None, company=company)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            product = form.save(commit=False)
            product.company = company
            product.save()
            initial = form.cleaned_data.get("initial_stock")
            if initial:
                stock.move(product, StockMovement.Kind.INITIAL, initial, user=request.user,
                           note="Saldo inicial no cadastro")
            services.record_price_change(product, old_price=None, old_cost=None, user=request.user,
                                         reason="Cadastro do produto")
            log_action("product.create", request=request, target=product)
        messages.success(request, f"Produto {product.sku} cadastrado.")
        return redirect("products:detail", pk=product.pk)
    return render(request, "products/form.html", {"form": form, "title": "Novo produto"})


@company_view("products")
def product_edit(request, pk):
    product = get_tenant_object(Product, request.company, pk=pk)
    old_price, old_cost = product.sale_price, product.total_unit_cost
    form = ProductForm(request.POST or None, request.FILES or None, instance=product, company=request.company)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            product = form.save()
            services.record_price_change(product, old_price=old_price, old_cost=old_cost, user=request.user,
                                         reason="Edição do cadastro")
            log_action("product.update", request=request, target=product, details={"campos": form.changed_data})
        messages.success(request, "Produto atualizado.")
        return redirect("products:detail", pk=product.pk)
    return render(request, "products/form.html", {"form": form, "title": f"Editar {product.name}",
                                                  "product": product})


@company_view(("products", "inventory", "reorder"))
def product_detail(request, pk):
    product = get_tenant_object(Product, request.company, pk=pk)
    reorder = build_rows(request.company, [product])[0]
    today = timezone.localdate()
    sales_90 = [r for r in product_ranking(request.company, today - timedelta(days=89), today, limit=0)
                if r["product_id"] == product.pk]
    return render(request, "products/detail.html", {
        "product": product,
        "history": product.price_history.select_related("changed_by")[:30],
        "movements": product.movements.select_related("user", "warehouse")[:20],
        "reorder": reorder,
        "sales_90": sales_90[0] if sales_90 else None,
        "simulations": product.pricing_simulations.all()[:5],
    })


@company_view("products", manager=True)
@require_POST
def product_toggle_active(request, pk):
    product = get_tenant_object(Product, request.company, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active", "updated_at"])
    log_action("product.archive" if not product.is_active else "product.unarchive", request=request,
               target=product)
    messages.success(request, "Produto arquivado." if not product.is_active else "Produto reativado.")
    return redirect("products:detail", pk=pk)


class ProductDelete(TenantDeleteView):
    model = Product
    module = "products"
    success_url_name = "products:list"
    audit_action = "product"
    protected_message = ("Este produto possui movimentações, vendas ou compras e não pode ser excluído. "
                         "Use “Arquivar” para retirá-lo das operações mantendo o histórico.")

    def form_valid(self, form):
        product = self.get_object()
        if product.movements.exists():
            messages.error(self.request, self.protected_message)
            return redirect("products:detail", pk=product.pk)
        return super().form_valid(form)


@company_view("products", manager=True, feature="csv_import")
def product_import(request):
    form = CsvImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            rows = read_csv(form.cleaned_data["file"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            result = services.import_rows(request.company, rows, user=request.user,
                                          update_existing=form.cleaned_data["update_existing"])
            log_action("product.import", request=request, details={k: v for k, v in result.items() if k != "errors"})
            messages.success(request, f"Importação concluída: {result['created']} criado(s), "
                                       f"{result['updated']} atualizado(s), {result['skipped']} ignorado(s).")
    return render(request, "products/import.html", {"form": form, "result": result,
                                                    "columns": services.CSV_COLUMNS})


@company_view("products")
def import_template(request):
    from apps.core.exports import Column, TableReport

    report = TableReport("modelo", [Column(key) for key, _ in services.CSV_COLUMNS],
                         [["CAM-001", "Camiseta básica preta M", "Camisetas", "Minha Marca", "", "un", "25,00",
                           "2,50", "0", "79,90", "5", "50", "10", "3", "sim", "Algodão 100%"]])
    return csv_response(report, "modelo-importacao-produtos.csv")


class CategoryList(TenantListView):
    model = Category
    module = "products"
    title = "Categorias"
    search_fields = ("name",)
    columns = [("Nome", "name", "text")]
    create_url_name = "products:category_new"
    edit_url_name = "products:category_edit"
    delete_url_name = "products:category_delete"


class CategoryCreate(TenantCreateView):
    model = Category
    form_class = CategoryForm
    module = "products"
    success_url_name = "products:categories"
    title = "Nova categoria"


class CategoryUpdate(TenantUpdateView):
    model = Category
    form_class = CategoryForm
    module = "products"
    success_url_name = "products:categories"
    title = "Editar categoria"


class CategoryDelete(TenantDeleteView):
    model = Category
    module = "products"
    success_url_name = "products:categories"
