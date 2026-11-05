"""Fornecedores e pedidos de compra."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantModel

ZERO = Decimal("0")


class Supplier(TenantModel):
    name = models.CharField("nome ou razão social", max_length=160)
    cnpj = models.CharField("CNPJ", max_length=18, blank=True)
    contact_name = models.CharField("contato", max_length=120, blank=True)
    phone = models.CharField("telefone", max_length=20, blank=True)
    email = models.EmailField("e-mail", blank=True)
    address = models.CharField("endereço", max_length=255, blank=True)
    notes = models.TextField("observações", blank=True)
    default_lead_time_days = models.PositiveIntegerField("prazo de entrega padrão (dias)", default=7)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "fornecedor"
        verbose_name_plural = "fornecedores"

    def __str__(self) -> str:
        return self.name


class PurchaseOrder(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        ORDERED = "ordered", "Pedido enviado"
        PARTIAL = "partial", "Recebido parcialmente"
        RECEIVED = "received", "Recebido"
        CANCELLED = "cancelled", "Cancelado"

    OPEN_STATUSES = [Status.ORDERED, Status.PARTIAL]

    number = models.PositiveIntegerField("número", editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders",
                                 verbose_name="fornecedor")
    order_date = models.DateField("data do pedido")
    expected_date = models.DateField("previsão de entrega", null=True, blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    freight = models.DecimalField("frete de entrada (R$)", max_digits=14, decimal_places=2, default=ZERO,
                                  validators=[MinValueValidator(ZERO)])
    other_costs = models.DecimalField("outras despesas (R$)", max_digits=14, decimal_places=2, default=ZERO,
                                      validators=[MinValueValidator(ZERO)])
    notes = models.TextField("observações", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    received_at = models.DateTimeField("recebido em", null=True, blank=True)

    class Meta:
        ordering = ["-order_date", "-number"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_purchase_number")]
        verbose_name = "pedido de compra"
        verbose_name_plural = "pedidos de compra"

    def __str__(self) -> str:
        return f"Compra #{self.number}"

    @property
    def items_total(self) -> Decimal:
        return sum((i.total for i in self.items.all()), ZERO)

    @property
    def total(self) -> Decimal:
        return self.items_total + self.freight + self.other_costs

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def can_receive(self) -> bool:
        return self.status in (self.Status.ORDERED, self.Status.PARTIAL)


class PurchaseOrderItem(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="purchase_items",
                                verbose_name="produto")
    quantity = models.DecimalField("quantidade", max_digits=14, decimal_places=3,
                                   validators=[MinValueValidator(Decimal("0.001"))])
    unit_cost = models.DecimalField("custo unitário (R$)", max_digits=14, decimal_places=4,
                                    validators=[MinValueValidator(ZERO)])
    received_quantity = models.DecimalField("recebido", max_digits=14, decimal_places=3, default=ZERO)

    class Meta:
        verbose_name = "item da compra"
        verbose_name_plural = "itens da compra"

    @property
    def total(self) -> Decimal:
        return (self.quantity or ZERO) * (self.unit_cost or ZERO)

    @property
    def pending_quantity(self) -> Decimal:
        return max(ZERO, (self.quantity or ZERO) - (self.received_quantity or ZERO))
