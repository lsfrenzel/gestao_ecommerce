"""Pedidos de venda."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantModel

ZERO = Decimal("0")
NON_NEG = [MinValueValidator(ZERO)]


class Channel(models.TextChoices):
    OWN_STORE = "own_store", "Loja virtual própria"
    MERCADO_LIVRE = "mercado_livre", "Mercado Livre"
    SHOPEE = "shopee", "Shopee"
    AMAZON = "amazon", "Amazon"
    INSTAGRAM = "instagram", "Instagram"
    WHATSAPP = "whatsapp", "WhatsApp"
    OTHER = "other", "Outros canais"


class PaymentMethod(models.TextChoices):
    PIX = "pix", "Pix"
    CREDIT = "credit", "Cartão de crédito"
    DEBIT = "debit", "Cartão de débito"
    BOLETO = "boleto", "Boleto"
    CASH = "cash", "Dinheiro"
    TRANSFER = "transfer", "Transferência"
    MARKETPLACE = "marketplace", "Pagamento via marketplace"
    OTHER = "other", "Outro"


class SalesOrder(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        AWAITING_PAYMENT = "awaiting_payment", "Aguardando pagamento"
        PAID = "paid", "Pago"
        SEPARATING = "separating", "Em separação"
        SHIPPED = "shipped", "Enviado"
        DELIVERED = "delivered", "Entregue"
        CANCELLED = "cancelled", "Cancelado"
        RETURNED = "returned", "Devolvido"

    class StockState(models.TextChoices):
        NONE = "none", "Sem efeito no estoque"
        RESERVED = "reserved", "Reservado"
        DEDUCTED = "deducted", "Baixado"
        RESTORED = "restored", "Estornado"

    # Pedidos que representam receita efetiva (pagos em diante). Pendentes NUNCA contam como receita.
    REVENUE_STATUSES = [Status.PAID, Status.SEPARATING, Status.SHIPPED, Status.DELIVERED]
    OPEN_STATUSES = [Status.PENDING, Status.AWAITING_PAYMENT]
    EDITABLE_STATUSES = [Status.PENDING, Status.AWAITING_PAYMENT]

    TRANSITIONS = {
        Status.PENDING: [Status.AWAITING_PAYMENT, Status.PAID, Status.CANCELLED],
        Status.AWAITING_PAYMENT: [Status.PENDING, Status.PAID, Status.CANCELLED],
        Status.PAID: [Status.SEPARATING, Status.SHIPPED, Status.DELIVERED, Status.CANCELLED, Status.RETURNED],
        Status.SEPARATING: [Status.SHIPPED, Status.DELIVERED, Status.CANCELLED, Status.RETURNED],
        Status.SHIPPED: [Status.DELIVERED, Status.RETURNED],
        Status.DELIVERED: [Status.RETURNED],
        Status.CANCELLED: [],
        Status.RETURNED: [],
    }

    number = models.PositiveIntegerField("número do pedido", editable=False)
    external_reference = models.CharField("nº no canal de venda", max_length=60, blank=True,
                                          help_text="Número do pedido no marketplace (para integrações futuras).")
    order_date = models.DateField("data da venda", db_index=True)
    customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="orders", verbose_name="cliente")
    channel = models.CharField("canal de venda", max_length=16, choices=Channel.choices, default=Channel.OWN_STORE,
                               db_index=True)
    payment_method = models.CharField("forma de pagamento", max_length=12, choices=PaymentMethod.choices,
                                      default=PaymentMethod.PIX)
    status = models.CharField("status", max_length=18, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    stock_state = models.CharField("estoque", max_length=10, choices=StockState.choices, default=StockState.NONE,
                                   editable=False)
    campaign = models.ForeignKey("marketing.Campaign", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="linked_sales", verbose_name="campanha de origem")

    discount = models.DecimalField("desconto no pedido (R$)", max_digits=14, decimal_places=2, default=ZERO,
                                   validators=NON_NEG)
    shipping_charged = models.DecimalField("frete cobrado do cliente (R$)", max_digits=14, decimal_places=2,
                                           default=ZERO, validators=NON_NEG)
    shipping_cost = models.DecimalField("custo do frete pago pela loja (R$)", max_digits=14, decimal_places=2,
                                        default=ZERO, validators=NON_NEG)
    commission = models.DecimalField("comissão (R$)", max_digits=14, decimal_places=2, default=ZERO,
                                     validators=NON_NEG, help_text="Comissão do marketplace e/ou do vendedor.")
    fees = models.DecimalField("taxas (R$)", max_digits=14, decimal_places=2, default=ZERO, validators=NON_NEG,
                               help_text="Taxas de pagamento/intermediação.")
    taxes = models.DecimalField("impostos (R$)", max_digits=14, decimal_places=2, default=ZERO, validators=NON_NEG)

    # Totais calculados pelo serviço (cópia histórica, não mudam se o cadastro do produto mudar)
    subtotal = models.DecimalField("subtotal", max_digits=14, decimal_places=2, default=ZERO, editable=False)
    total = models.DecimalField("total", max_digits=14, decimal_places=2, default=ZERO, editable=False)
    cogs = models.DecimalField("custo dos produtos", max_digits=14, decimal_places=2, default=ZERO, editable=False)

    paid_at = models.DateTimeField("pago em", null=True, blank=True, editable=False)
    notes = models.TextField("observações", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["-order_date", "-number"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_sales_number")]
        indexes = [models.Index(fields=["company", "status", "order_date"])]
        verbose_name = "pedido de venda"
        verbose_name_plural = "pedidos de venda"

    def __str__(self) -> str:
        return f"Pedido #{self.number}"

    @property
    def variable_expenses(self) -> Decimal:
        return self.commission + self.fees + self.taxes + self.shipping_cost

    @property
    def gross_profit(self) -> Decimal:
        return self.total - self.cogs

    @property
    def contribution_margin(self) -> Decimal:
        return self.total - self.cogs - self.variable_expenses

    @property
    def is_editable(self) -> bool:
        return self.status in self.EDITABLE_STATUSES

    @property
    def is_revenue(self) -> bool:
        return self.status in self.REVENUE_STATUSES

    def allowed_transitions(self):
        return [(s, self.Status(s).label) for s in self.TRANSITIONS.get(self.status, [])]

    @property
    def status_color(self) -> str:
        return {
            "pending": "secondary", "awaiting_payment": "warning", "paid": "primary", "separating": "info",
            "shipped": "info", "delivered": "success", "cancelled": "danger", "returned": "danger",
        }.get(self.status, "secondary")


class SalesOrderItem(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="sales_items",
                                verbose_name="produto")
    quantity = models.DecimalField("quantidade", max_digits=14, decimal_places=3,
                                   validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField("preço unitário (R$)", max_digits=14, decimal_places=2, validators=NON_NEG)
    discount = models.DecimalField("desconto do item (R$)", max_digits=14, decimal_places=2, default=ZERO,
                                   validators=NON_NEG)
    unit_cost = models.DecimalField("custo unitário na venda", max_digits=14, decimal_places=4, default=ZERO)

    class Meta:
        verbose_name = "item do pedido"
        verbose_name_plural = "itens do pedido"

    @property
    def total(self) -> Decimal:
        return self.quantity * self.unit_price - self.discount

    @property
    def cost_total(self) -> Decimal:
        return self.quantity * self.unit_cost
