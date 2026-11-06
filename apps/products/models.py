"""Catálogo de produtos da empresa."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantModel

ZERO = Decimal("0")
NON_NEGATIVE = [MinValueValidator(Decimal("0"))]


class Category(TenantModel):
    name = models.CharField("nome", max_length=80)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_category_name")]
        verbose_name = "categoria"
        verbose_name_plural = "categorias"

    def __str__(self) -> str:
        return self.name


class Product(TenantModel):
    UNITS = [
        ("un", "Unidade (un)"),
        ("pc", "Peça (pç)"),
        ("cx", "Caixa (cx)"),
        ("kit", "Kit"),
        ("par", "Par"),
        ("kg", "Quilograma (kg)"),
        ("g", "Grama (g)"),
        ("l", "Litro (L)"),
        ("ml", "Mililitro (mL)"),
        ("m", "Metro (m)"),
    ]

    name = models.CharField("nome do produto", max_length=160)
    sku = models.CharField("SKU", max_length=60)
    barcode = models.CharField("código de barras", max_length=32, blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="products", verbose_name="categoria")
    description = models.TextField("descrição", blank=True)
    brand = models.CharField("marca", max_length=80, blank=True)
    supplier = models.ForeignKey("purchasing.Supplier", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="products", verbose_name="fornecedor")
    unit = models.CharField("unidade de medida", max_length=4, choices=UNITS, default="un")

    acquisition_cost = models.DecimalField("custo de aquisição (R$)", max_digits=14, decimal_places=4,
                                           default=ZERO, validators=NON_NEGATIVE)
    purchase_freight = models.DecimalField("frete de compra por unidade (R$)", max_digits=14, decimal_places=4,
                                           default=ZERO, validators=NON_NEGATIVE)
    other_costs = models.DecimalField("outros custos de aquisição por unidade (R$)", max_digits=14,
                                      decimal_places=4, default=ZERO, validators=NON_NEGATIVE)
    sale_price = models.DecimalField("preço de venda (R$)", max_digits=14, decimal_places=2, default=ZERO,
                                     validators=NON_NEGATIVE)
    image = models.ImageField("imagem do produto", upload_to="produtos/%Y/%m/", blank=True)

    # Saldos: alterados SOMENTE por apps.inventory.services (sempre com movimentação registrada).
    stock_on_hand = models.DecimalField("estoque atual", max_digits=14, decimal_places=3, default=ZERO,
                                        editable=False)
    stock_reserved = models.DecimalField("quantidade reservada", max_digits=14, decimal_places=3, default=ZERO,
                                         editable=False)
    last_movement_at = models.DateTimeField("última movimentação", null=True, blank=True, editable=False)

    min_stock = models.DecimalField("estoque mínimo", max_digits=14, decimal_places=3, default=ZERO,
                                    validators=NON_NEGATIVE)
    max_stock = models.DecimalField("estoque máximo", max_digits=14, decimal_places=3, null=True, blank=True,
                                    validators=NON_NEGATIVE)
    lead_time_days = models.PositiveIntegerField("tempo de reposição do fornecedor (dias)", default=7)
    safety_stock = models.DecimalField("estoque de segurança", max_digits=14, decimal_places=3, default=ZERO,
                                       validators=NON_NEGATIVE)
    manual_daily_consumption = models.DecimalField(
        "consumo médio diário (ajuste manual)", max_digits=14, decimal_places=4, null=True, blank=True,
        validators=NON_NEGATIVE, help_text="Vazio = calculado automaticamente pelo histórico de vendas.")

    is_active = models.BooleanField("ativo", default=True)

    # Estrutura para variações futuras (tamanho, cor, modelo): um produto "pai" agrupa variações.
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="variants",
                               verbose_name="produto pai")
    variant_attributes = models.JSONField("atributos da variação", default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "sku"], name="uniq_product_sku"),
            models.CheckConstraint(condition=models.Q(stock_on_hand__gte=0), name="product_stock_non_negative"),
            models.CheckConstraint(condition=models.Q(stock_reserved__gte=0), name="product_reserved_non_negative"),
        ]
        indexes = [models.Index(fields=["company", "name"]), models.Index(fields=["company", "is_active"])]
        verbose_name = "produto"
        verbose_name_plural = "produtos"

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"

    # ---- Custos e indicadores ----------------------------------------------------------
    @property
    def total_unit_cost(self) -> Decimal:
        return (self.acquisition_cost or ZERO) + (self.purchase_freight or ZERO) + (self.other_costs or ZERO)

    @property
    def available_stock(self) -> Decimal:
        return (self.stock_on_hand or ZERO) - (self.stock_reserved or ZERO)

    @property
    def stock_value(self) -> Decimal:
        return (self.stock_on_hand or ZERO) * self.total_unit_cost

    @property
    def markup_percent(self) -> Decimal | None:
        from apps.pricing.calculations import markup_percent

        return markup_percent(self.sale_price, self.total_unit_cost)

    @property
    def gross_margin_percent(self) -> Decimal | None:
        """Margem bruta simples sobre o preço (sem despesas variáveis)."""
        if not self.sale_price:
            return None
        return (self.sale_price - self.total_unit_cost) / self.sale_price * 100

    @property
    def stock_status(self) -> str:
        if self.stock_on_hand <= 0:
            return "out"
        # Baixo: disponível ≤ mínimo (com mínimo definido) ou tudo reservado (nada disponível para venda).
        if self.available_stock <= 0 or (self.min_stock > 0 and self.available_stock <= self.min_stock):
            return "low"
        if self.max_stock is not None and self.stock_on_hand > self.max_stock:
            return "excess"
        return "ok"

    @property
    def stock_status_label(self) -> str:
        return {"out": "Esgotado", "low": "Estoque baixo", "excess": "Excesso", "ok": "Normal"}[self.stock_status]


class PriceHistory(models.Model):
    """Histórico de alterações de preço de venda e custo."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_history")
    created_at = models.DateTimeField("data", auto_now_add=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   verbose_name="alterado por")
    old_sale_price = models.DecimalField("preço anterior", max_digits=14, decimal_places=2, null=True)
    new_sale_price = models.DecimalField("novo preço", max_digits=14, decimal_places=2, null=True)
    old_cost = models.DecimalField("custo anterior", max_digits=14, decimal_places=4, null=True)
    new_cost = models.DecimalField("novo custo", max_digits=14, decimal_places=4, null=True)
    reason = models.CharField("origem", max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "histórico de preço"
        verbose_name_plural = "histórico de preços"
