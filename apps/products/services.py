from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.core.exports import Column, TableReport
from apps.core.money import ZERO, to_decimal

from .models import Category, PriceHistory, Product


def record_price_change(product: Product, *, old_price, old_cost, user=None, reason: str = "") -> None:
    new_cost = product.total_unit_cost
    if old_price == product.sale_price and old_cost == new_cost:
        return
    PriceHistory.objects.create(product=product, changed_by=user, old_sale_price=old_price,
                                new_sale_price=product.sale_price, old_cost=old_cost, new_cost=new_cost,
                                reason=reason[:120])


def snapshot(product: Product) -> tuple[Decimal | None, Decimal | None]:
    if not product.pk:
        return None, None
    fresh = Product.objects.get(pk=product.pk)
    return fresh.sale_price, fresh.total_unit_cost


CSV_COLUMNS = [
    ("sku", "SKU"), ("nome", "Nome"), ("categoria", "Categoria"), ("marca", "Marca"),
    ("codigo_barras", "Código de barras"), ("unidade", "Unidade"), ("custo_aquisicao", "Custo de aquisição"),
    ("frete_compra", "Frete de compra"), ("outros_custos", "Outros custos"), ("preco_venda", "Preço de venda"),
    ("estoque_minimo", "Estoque mínimo"), ("estoque_maximo", "Estoque máximo"),
    ("prazo_reposicao", "Prazo de reposição (dias)"), ("estoque_seguranca", "Estoque de segurança"),
    ("ativo", "Ativo"), ("descricao", "Descrição"),
]


def export_report(queryset) -> TableReport:
    columns = [Column(label) for _, label in CSV_COLUMNS] + [Column("Estoque atual", "qty"),
                                                             Column("Custo total unitário", "brl")]
    rows = []
    for p in queryset.select_related("category"):
        rows.append([p.sku, p.name, p.category.name if p.category else "", p.brand, p.barcode, p.unit,
                     p.acquisition_cost, p.purchase_freight, p.other_costs, p.sale_price, p.min_stock,
                     p.max_stock, p.lead_time_days, p.safety_stock, "sim" if p.is_active else "não",
                     p.description, p.stock_on_hand, p.total_unit_cost])
    return TableReport("Produtos", columns, rows)


def _get(row: dict, *keys: str) -> str:
    for key in keys:
        if row.get(key):
            return row[key]
    return ""


@transaction.atomic
def import_rows(company, rows: list[dict], *, user=None, update_existing: bool = True) -> dict:
    """
    Importa produtos. Colunas aceitas (cabeçalho, sem acentos): sku, nome, categoria, marca, codigo_barras,
    unidade, custo_aquisicao, frete_compra, outros_custos, preco_venda, estoque_minimo, estoque_maximo,
    prazo_reposicao, estoque_seguranca, ativo, descricao.
    O estoque NÃO é importado (use movimentações de entrada/ajuste). Erros não interrompem as demais linhas.
    """
    from apps.companies.services import PlanLimitError, check_product_limit

    created = updated = skipped = 0
    errors: list[str] = []
    units = {u for u, _ in Product.UNITS}
    for line, row in enumerate(rows, start=2):
        sku = _get(row, "sku").strip().upper()
        name = _get(row, "nome", "name", "produto").strip()
        if not sku or not name:
            errors.append(f"Linha {line}: SKU e nome são obrigatórios.")
            continue
        numbers = {}
        bad = False
        for field, keys in {
            "acquisition_cost": ("custo_aquisicao", "custo"), "purchase_freight": ("frete_compra",),
            "other_costs": ("outros_custos",), "sale_price": ("preco_venda", "preco"),
            "min_stock": ("estoque_minimo",), "max_stock": ("estoque_maximo",),
            "safety_stock": ("estoque_seguranca",), "lead_time_days": ("prazo_reposicao",),
        }.items():
            raw = _get(row, *keys)
            if raw == "":
                continue
            value = to_decimal(raw, None)
            if value is None or value < 0:
                errors.append(f"Linha {line}: valor inválido em “{keys[0]}” ({raw}).")
                bad = True
                break
            numbers[field] = int(value) if field == "lead_time_days" else value
        if bad:
            continue
        unit = _get(row, "unidade").lower() or "un"
        if unit not in units:
            errors.append(f"Linha {line}: unidade “{unit}” inválida (use: {', '.join(sorted(units))}).")
            continue
        category = None
        if _get(row, "categoria"):
            category, _ = Category.objects.get_or_create(company=company, name=_get(row, "categoria")[:80])
        product = Product.objects.for_company(company).filter(sku__iexact=sku).first()
        if product and not update_existing:
            skipped += 1
            continue
        if product is None:
            try:
                check_product_limit(company)
            except PlanLimitError as exc:
                errors.append(f"Linha {line}: {exc}")
                break
            product = Product(company=company, sku=sku)
            is_new = True
        else:
            is_new = False
        old_price, old_cost = (product.sale_price, product.total_unit_cost) if not is_new else (None, None)
        product.name = name[:160]
        product.category = category or product.category
        product.brand = _get(row, "marca")[:80] or product.brand
        product.barcode = _get(row, "codigo_barras", "ean")[:32] or product.barcode
        product.unit = unit
        product.description = _get(row, "descricao") or product.description
        if _get(row, "ativo"):
            product.is_active = _get(row, "ativo").lower() in ("sim", "s", "1", "true", "ativo", "yes")
        for field, value in numbers.items():
            setattr(product, field, value)
        product.save()
        if is_new:
            created += 1
        else:
            updated += 1
            record_price_change(product, old_price=old_price, old_cost=old_cost, user=user,
                                reason="Importação CSV")
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


__all__ = ["import_rows", "export_report", "record_price_change", "snapshot", "ZERO"]
