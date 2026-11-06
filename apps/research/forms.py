from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.core.forms import BRDecimalField, StyledForm

from .analysis import DIRECTIONS
from .models import OpportunitySimulation
from .opportunity import CHANNEL_CHOICES, REGIMES, SIMPLIFIED
from .services import ASSUMPTION_LABELS
from .trends import REGIONS, TIMEFRAMES

MIN_MARGIN = Decimal("30")


class TrendSearchForm(StyledForm):
    q = forms.CharField(label="Produtos ou termos", max_length=500,
                        help_text="Até 5 termos separados por vírgula para comparar. Ex.: tênis de corrida, "
                                  "camiseta oversized",
                        widget=forms.TextInput(attrs={"placeholder": "Ex.: camiseta oversized, moletom"}))
    geo = forms.ChoiceField(label="Região", choices=REGIONS, initial="BR", required=False)
    periodo = forms.ChoiceField(label="Período", choices=TIMEFRAMES, initial="today 12-m", required=False)


def _dec(label, help_text="", **kwargs):
    return BRDecimalField(label=label, required=False, min_value=Decimal("0"), help_text=help_text,
                          widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "automático"}),
                          **kwargs)


class OpportunityForm(StyledForm):
    name = forms.CharField(label="Produto", max_length=160,
                           widget=forms.TextInput(attrs={"placeholder": "Ex.: camiseta oversized algodão"}))
    keyword = forms.CharField(required=False, max_length=120, widget=forms.HiddenInput)
    trend_score = forms.IntegerField(required=False, min_value=0, max_value=100, widget=forms.HiddenInput)
    trend_direction = forms.ChoiceField(required=False, choices=[("", "")] + list(DIRECTIONS.items()),
                                        widget=forms.HiddenInput)
    origin = forms.ChoiceField(label="Origem do produto", choices=OpportunitySimulation.Origin.choices,
                               initial=OpportunitySimulation.Origin.NATIONAL, widget=forms.RadioSelect)
    channel = forms.ChoiceField(label="Canal de venda", choices=CHANNEL_CHOICES, initial="mercado_livre")
    unit_cost_brl = BRDecimalField(label="Custo no fornecedor (R$ por unidade)", required=False,
                                   min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
                                   widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    unit_cost_usd = BRDecimalField(label="Preço FOB no fornecedor (US$ por unidade)", required=False,
                                   min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
                                   widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    sale_price = BRDecimalField(label="Preço de venda pretendido (R$)", required=False, min_value=Decimal("0.01"),
                                max_digits=14, decimal_places=2,
                                help_text="Preço praticado no mercado. Em branco, o sistema mostra o preço mínimo.",
                                widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    quantity = forms.IntegerField(label="Quantidade do lote (unidades)", min_value=1, max_value=1_000_000,
                                  initial=100, help_text="Usada para ratear frete e tributos da importação.")
    target_margin_pct = BRDecimalField(label="Margem líquida mínima exigida (%)", initial=MIN_MARGIN,
                                       min_value=MIN_MARGIN, max_value=Decimal("90"), max_digits=5,
                                       decimal_places=2, widget=forms.TextInput(attrs={"inputmode": "decimal"}),
                                       help_text="Mínimo de 30% após todos os custos estimados.")
    regime = forms.ChoiceField(label="Forma de importação", choices=REGIMES, initial=SIMPLIFIED, required=False,
                               widget=forms.RadioSelect)

    # Premissas: em branco = estimativa automática.
    tax_pct = _dec(ASSUMPTION_LABELS["tax_pct"][0] + " (%)", max_value=Decimal("99.99"))
    commission_pct = _dec(ASSUMPTION_LABELS["commission_pct"][0] + " (%)", max_value=Decimal("99.99"))
    fixed_fee = _dec(ASSUMPTION_LABELS["fixed_fee"][0] + " (R$)",
                     "No Mercado Livre vale para produtos abaixo de R$ 79.")
    payment_fee_pct = _dec(ASSUMPTION_LABELS["payment_fee_pct"][0] + " (%)", max_value=Decimal("99.99"))
    marketing_pct = _dec(ASSUMPTION_LABELS["marketing_pct"][0] + " (%)",
                         "Automático: investimento ÷ receita das suas campanhas.", max_value=Decimal("99.99"))
    returns_pct = _dec(ASSUMPTION_LABELS["returns_pct"][0] + " (%)", max_value=Decimal("99.99"))
    packaging = _dec(ASSUMPTION_LABELS["packaging"][0] + " (R$)")
    shipping = _dec(ASSUMPTION_LABELS["shipping"][0] + " (R$)",
                    "Frete grátis bancado pelo vendedor (no Mercado Livre e Amazon, a partir de R$ 79).")
    inbound_freight_pct = _dec(ASSUMPTION_LABELS["inbound_freight_pct"][0] + " (% do custo)",
                               max_value=Decimal("99.99"))
    exchange_rate = _dec(ASSUMPTION_LABELS["exchange_rate"][0] + " (R$)", "Automático: cotação do dia.",
                         max_digits=10, decimal_places=4)
    intl_freight_usd = _dec(ASSUMPTION_LABELS["intl_freight_usd"][0] + " (US$)", "Automático: 15% do FOB.")
    insurance_pct = _dec(ASSUMPTION_LABELS["insurance_pct"][0] + " (%)", max_value=Decimal("99.99"))
    fx_fee_pct = _dec(ASSUMPTION_LABELS["fx_fee_pct"][0] + " (%)", max_value=Decimal("99.99"))
    ii_pct = _dec(ASSUMPTION_LABELS["ii_pct"][0] + " (%)", "Depende da NCM do produto.", max_value=Decimal("99.99"))
    ipi_pct = _dec(ASSUMPTION_LABELS["ipi_pct"][0] + " (%)", max_value=Decimal("99.99"))
    pis_pct = _dec(ASSUMPTION_LABELS["pis_pct"][0] + " (%)", max_value=Decimal("99.99"))
    cofins_pct = _dec(ASSUMPTION_LABELS["cofins_pct"][0] + " (%)", max_value=Decimal("99.99"))
    icms_pct = _dec(ASSUMPTION_LABELS["icms_pct"][0] + " (%)", "Alíquota do seu estado.",
                    max_value=Decimal("99.99"))
    customs_fees = _dec(ASSUMPTION_LABELS["customs_fees"][0] + " (R$)",
                        "Despachante, armazenagem, Siscomex ou taxa de despacho postal.")
    domestic_freight_pct = _dec(ASSUMPTION_LABELS["domestic_freight_pct"][0] + " (%)", max_value=Decimal("99.99"))

    ASSUMPTION_FIELDS = list(ASSUMPTION_LABELS)

    def clean(self):
        cleaned = super().clean()
        origin = cleaned.get("origin")
        if origin == OpportunitySimulation.Origin.INTERNATIONAL:
            if not cleaned.get("unit_cost_usd"):
                self.add_error("unit_cost_usd", "Informe o preço do produto no fornecedor em dólares.")
            cleaned["regime"] = cleaned.get("regime") or SIMPLIFIED
        elif origin == OpportunitySimulation.Origin.NATIONAL and not cleaned.get("unit_cost_brl"):
            self.add_error("unit_cost_brl", "Informe o custo do produto no fornecedor.")
        return cleaned

    def serializable(self) -> dict:
        """Dados informados para salvar e reabrir a simulação (strings, sem float)."""
        out = {}
        for name, value in self.cleaned_data.items():
            if value is None or value == "":
                continue
            out[name] = str(value) if isinstance(value, Decimal) else value
        return out
