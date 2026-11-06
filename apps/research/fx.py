"""
Cotação automática do dólar (USD → BRL) para simulações de produtos importados.

Fonte principal: AwesomeAPI (economia.awesomeapi.com.br, cotação comercial de venda).
Alternativa: PTAX de venda do Banco Central (olinda.bcb.gov.br).
Cache de 1 hora. Se nenhuma fonte responder, o usuário informa a cotação manualmente.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone

from .trends import cache_get, cache_set

logger = logging.getLogger(__name__)
AWESOME = "https://economia.awesomeapi.com.br/json/last/USD-BRL"
BCB_PTAX = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
            "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)")


@dataclass
class Quote:
    rate: Decimal
    source: str
    quoted_at: str


def _get_json(url: str) -> dict:
    if not settings.EXTERNAL_LOOKUPS_ENABLED:
        raise OSError("Consultas externas desativadas neste ambiente.")
    req = request.Request(url, headers={"User-Agent": "FrenzelTech-Ecommerce/1.0", "Accept": "application/json"})
    with request.urlopen(req, timeout=8) as response:  # noqa: S310 — hosts fixos
        return json.loads(response.read(200_000).decode())


def _awesome() -> Quote:
    data = _get_json(AWESOME)["USDBRL"]
    return Quote(rate=Decimal(str(data["ask"])), source="AwesomeAPI (dólar comercial)",
                 quoted_at=data.get("create_date", ""))


def _ptax() -> Quote:
    today = timezone.localdate()
    params = {"@dataInicial": f"'{(today - timedelta(days=7)):%m-%d-%Y}'",
              "@dataFinalCotacao": f"'{today:%m-%d-%Y}'", "$format": "json", "$orderby": "dataHoraCotacao desc",
              "$top": "1"}
    rows = _get_json(BCB_PTAX + "?" + parse.urlencode(params, safe="@'$")).get("value", [])
    if not rows:
        raise ValueError("PTAX sem cotação")
    return Quote(rate=Decimal(str(rows[0]["cotacaoVenda"])), source="Banco Central (PTAX venda)",
                 quoted_at=rows[0].get("dataHoraCotacao", ""))


def usd_brl() -> Quote | None:
    cached = cache_get("fx:usd-brl", timedelta(hours=1))
    if cached:
        return Quote(rate=Decimal(cached["rate"]), source=cached["source"], quoted_at=cached["quoted_at"])
    for source in (_awesome, _ptax):
        try:
            quote = source()
        except (error.URLError, TimeoutError, OSError, KeyError, ValueError, InvalidOperation) as exc:
            logger.info("Cotação indisponível em %s: %s", getattr(source, "__name__", "fonte"), exc)
            continue
        if quote.rate > 0:
            cache_set("fx:usd-brl", {"rate": str(quote.rate), "source": quote.source, "quoted_at": quote.quoted_at})
            return quote
    return None
