"""Movimentações de estoque (histórico imutável) e saldos por depósito."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class StockBalance(models.Model):
    """Saldo físico de um produto em um depósito. Alterado apenas por inventory.services."""

    product = models.ForeignKey("products.Product", on_delete=models.CASCADE, related_name="balances")
    warehouse = models.ForeignKey("companies.Warehouse", on_delete=models.PROTECT, related_name="balances")
    on_hand = models.DecimalField("saldo", max_digits=14, decimal_places=3, default=Decimal("0"))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "warehouse"], name="uniq_balance_product_warehouse"),
            models.CheckConstraint(condition=models.Q(on_hand__gte=0), name="balance_non_negative"),
        ]
        verbose_name = "saldo por depósito"
        verbose_name_plural = "saldos por depósito"


class StockMovement(TenantModel):
    class Kind(models.TextChoices):
        INITIAL = "initial", "Saldo inicial"
        IN = "in", "Entrada"
        PURCHASE = "purchase", "Entrada por compra"
        OUT = "out", "Saída"
        SALE = "sale", "Saída por venda"
        ADJUST_IN = "adjust_in", "Ajuste (+)"
        ADJUST_OUT = "adjust_out", "Ajuste (−)"
        RETURN = "return", "Devolução de cliente"
        SALE_CANCEL = "sale_cancel", "Estorno de venda cancelada"
        LOSS = "loss", "Perda"
        TRANSFER_OUT = "transfer_out", "Transferência (saída)"
        TRANSFER_IN = "transfer_in", "Transferência (entrada)"
        RESERVE = "reserve", "Reserva de pedido"
        RELEASE = "release", "Liberação de reserva"

    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="movements",
                                verbose_name="produto")
    warehouse = models.ForeignKey("companies.Warehouse", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="movements", verbose_name="depósito")
    kind = models.CharField("tipo", max_length=14, choices=Kind.choices, db_index=True)
    quantity = models.DecimalField("quantidade", max_digits=14, decimal_places=3)
    on_hand_delta = models.DecimalField("variação do saldo", max_digits=14, decimal_places=3, default=0)
    reserved_delta = models.DecimalField("variação da reserva", max_digits=14, decimal_places=3, default=0)
    balance_after = models.DecimalField("saldo após", max_digits=14, decimal_places=3)
    reserved_after = models.DecimalField("reservado após", max_digits=14, decimal_places=3, default=0)
    unit_cost = models.DecimalField("custo unitário", max_digits=14, decimal_places=4, null=True, blank=True)
    note = models.CharField("observação", max_length=255, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             verbose_name="usuário")
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="stock_movements", verbose_name="pedido de venda")
    purchase_order = models.ForeignKey("purchasing.PurchaseOrder", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="stock_movements",
                                       verbose_name="pedido de compra")
    transfer_group = models.UUIDField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["company", "product", "created_at"]),
                   models.Index(fields=["company", "kind", "created_at"])]
        verbose_name = "movimentação de estoque"
        verbose_name_plural = "movimentações de estoque"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.quantity} × {self.product.sku}"

    @property
    def total_cost(self) -> Decimal | None:
        return None if self.unit_cost is None else self.unit_cost * self.quantity
