"""
Cliente do Google Trends.

Dois provedores, mesma saída normalizada (`TrendResult`):

- **Google Trends direto** (padrão, sem chave): usa os mesmos endpoints da página trends.google.com
  (`/api/explore` → tokens dos widgets → `/api/widgetdata/multiline`, `relatedsearches` e `comparedgeo`).
  O Google costuma limitar (HTTP 429) consultas vindas de servidores de nuvem.
- **SerpApi** (quando `SERPAPI_API_KEY` está configurada): API Google Trends do SerpApi, estável em produção.

Os resultados são públicos e ficam em cache (`CachedLookup`) por `TRENDS_CACHE_HOURS` horas para
poupar a cota e responder rápido. Nenhum dado da empresa é enviado aos provedores além dos termos pesquisados.
"""

from __future__ import annotations

import hashlib
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from http.cookiejar import CookieJar
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

GOOGLE = "https://trends.google.com"
SERPAPI = "https://serpapi.com/search.json"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
MAX_KEYWORDS = 5
MAX_BYTES = 2_000_000

TIMEFRAMES = [
    ("now 7-d", "Últimos 7 dias"),
    ("today 1-m", "Últimos 30 dias"),
    ("today 3-m", "Últimos 90 dias"),
    ("today 12-m", "Últimos 12 meses"),
    ("today 5-y", "Últimos 5 anos"),
]
REGIONS = [("BR", "Brasil (todo o país)")] + [(f"BR-{uf}", name) for uf, name in [
    ("AC", "Acre"), ("AL", "Alagoas"), ("AP", "Amapá"), ("AM", "Amazonas"), ("BA", "Bahia"), ("CE", "Ceará"),
    ("DF", "Distrito Federal"), ("ES", "Espírito Santo"), ("GO", "Goiás"), ("MA", "Maranhão"),
    ("MT", "Mato Grosso"), ("MS", "Mato Grosso do Sul"), ("MG", "Minas Gerais"), ("PA", "Pará"),
    ("PB", "Paraíba"), ("PR", "Paraná"), ("PE", "Pernambuco"), ("PI", "Piauí"), ("RJ", "Rio de Janeiro"),
    ("RN", "Rio Grande do Norte"), ("RS", "Rio Grande do Sul"), ("RO", "Rondônia"), ("RR", "Roraima"),
    ("SC", "Santa Catarina"), ("SP", "São Paulo"), ("SE", "Sergipe"), ("TO", "Tocantins")]]


class TrendsError(Exception):
    """Falha ao consultar o provedor de tendências (mensagem amigável)."""


class TrendsRateLimited(TrendsError):
    pass


@dataclass
class TrendResult:
    keywords: list[str]
    geo: str
    timeframe: str
    provider: str
    fetched_at: str
    # [{"date": "2026-01-05", "label": "5 de jan. de 2026", "values": [54, 30], "partial": False}]
    timeline: list[dict] = field(default_factory=list)
    # {"tênis": {"top": [{"query": "...", "value": "100"}], "rising": [...]}}
    related: dict = field(default_factory=dict)
    # [{"code": "BR-SP", "name": "São Paulo", "values": [100, 40]}]
    regions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TrendResult":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def provider_name() -> str:
    return "serpapi" if settings.SERPAPI_API_KEY else "google"


def clean_keywords(raw: str) -> list[str]:
    """'tênis, tênis ,  mochila' → ['tênis', 'mochila'] (máx. 5, sem duplicados, até 100 caracteres)."""
    seen, result = set(), []
    for part in (raw or "").replace(";", ",").split(","):
        term = " ".join(part.split())[:100]
        if term and term.lower() not in seen:
            seen.add(term.lower())
            result.append(term)
    return result[:MAX_KEYWORDS]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _cache_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def cache_get(key: str, max_age: timedelta):
    from .models import CachedLookup

    row = CachedLookup.objects.filter(key=key, fetched_at__gte=timezone.now() - max_age).first()
    return row.payload if row else None


def cache_set(key: str, payload) -> None:
    from .models import CachedLookup

    CachedLookup.objects.update_or_create(key=key, defaults={"payload": payload, "fetched_at": timezone.now()})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _read(opener, url: str, timeout: int = 20) -> str:
    if not settings.EXTERNAL_LOOKUPS_ENABLED:
        raise TrendsError("Consultas externas desativadas neste ambiente.")
    req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
    try:
        with opener.open(req, timeout=timeout) as response:  # noqa: S310 — hosts fixos (Google/SerpApi)
            return response.read(MAX_BYTES).decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        if exc.code == 429:
            raise TrendsRateLimited(
                "O Google Trends limitou temporariamente as consultas deste servidor. Tente novamente em alguns "
                "minutos" + ("" if settings.SERPAPI_API_KEY else " ou configure SERPAPI_API_KEY para consultas "
                                                                 "estáveis em produção") + ".") from exc
        logger.warning("Trends HTTP %s em %s", exc.code, url.split("?")[0])
        raise TrendsError(f"O provedor de tendências respondeu com erro {exc.code}. Tente novamente.") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Trends indisponível: %s", exc)
        raise TrendsError("Não foi possível conectar ao Google Trends agora. Tente novamente em instantes.") from exc


def _json_after_prefix(text: str) -> dict:
    """As respostas do Google começam com `)]}'` para evitar JSON hijacking."""
    start = text.find("{")
    if start < 0:
        raise TrendsError("Resposta inesperada do Google Trends.")
    try:
        return json.loads(text[start:])
    except ValueError as exc:
        raise TrendsError("Resposta inesperada do Google Trends.") from exc


# ---------------------------------------------------------------------------
# Provedor: Google Trends direto
# ---------------------------------------------------------------------------
def _google_fetch(keywords: list[str], geo: str, timeframe: str) -> TrendResult:
    opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))
    common = {"hl": "pt-BR", "tz": "180"}
    # Cookie NID exigido pelos endpoints de dados.
    try:
        _read(opener, f"{GOOGLE}/trends/?geo={parse.quote(geo.split('-')[0])}", timeout=10)
    except TrendsRateLimited:
        raise
    except TrendsError:
        pass
    explore_req = {"comparisonItem": [{"keyword": k, "geo": geo, "time": timeframe} for k in keywords],
                   "category": 0, "property": ""}
    explore = _json_after_prefix(_read(opener, f"{GOOGLE}/trends/api/explore?" + parse.urlencode(
        {**common, "req": json.dumps(explore_req, ensure_ascii=False)})))
    widgets = explore.get("widgets") or []

    def widget_data(endpoint: str, widget: dict) -> dict:
        url = f"{GOOGLE}/trends/api/widgetdata/{endpoint}?" + parse.urlencode(
            {**common, "req": json.dumps(widget["request"], ensure_ascii=False), "token": widget["token"]})
        return _json_after_prefix(_read(opener, url)).get("default") or {}

    result = TrendResult(keywords=keywords, geo=geo, timeframe=timeframe, provider="google",
                         fetched_at=timezone.now().isoformat())
    for widget in widgets:
        wid = widget.get("id", "")
        if wid == "TIMESERIES":
            for point in widget_data("multiline", widget).get("timelineData", []):
                ts = int(point.get("time", 0))
                result.timeline.append({
                    "date": datetime.fromtimestamp(ts, tz=dt_timezone.utc).date().isoformat(),
                    "label": point.get("formattedTime", ""),
                    "values": [int(v) for v in point.get("value", [])],
                    "partial": bool(point.get("isPartial")),
                })
        elif wid.startswith("RELATED_QUERIES"):
            try:
                kw = widget["request"]["restriction"]["complexKeywordsRestriction"]["keyword"][0]["value"]
            except (KeyError, IndexError, TypeError):
                kw = keywords[0]
            try:
                lists = widget_data("relatedsearches", widget).get("rankedList", [])
            except TrendsRateLimited:
                raise
            except TrendsError:
                continue
            result.related[kw] = {
                "top": [{"query": i["query"], "value": i.get("formattedValue", "")}
                        for i in (lists[0].get("rankedKeyword", []) if lists else [])][:10],
                "rising": [{"query": i["query"], "value": i.get("formattedValue", "")}
                           for i in (lists[1].get("rankedKeyword", []) if len(lists) > 1 else [])][:10],
            }
        elif wid == "GEO_MAP" and not result.regions:
            try:
                geo_data = widget_data("comparedgeo", widget).get("geoMapData", [])
            except TrendsRateLimited:
                raise
            except TrendsError:
                continue
            result.regions = [{"code": r.get("geoCode", ""), "name": r.get("geoName", ""),
                               "values": [int(v) for v in r.get("value", [])]}
                              for r in geo_data if any(r.get("hasData", [True]))]
    if not result.timeline:
        raise TrendsError("O Google Trends não retornou dados para esses termos.")
    return result


# ---------------------------------------------------------------------------
# Provedor: SerpApi
# ---------------------------------------------------------------------------
def _serpapi(params: dict) -> dict:
    opener = request.build_opener()
    url = SERPAPI + "?" + parse.urlencode({**params, "api_key": settings.SERPAPI_API_KEY, "hl": "pt-br"})
    data = json.loads(_read(opener, url, timeout=40) or "{}")
    if data.get("error"):
        message = str(data["error"])
        if "hasn't returned any results" in message:
            return {}
        logger.warning("SerpApi: %s", message[:200])
        raise TrendsError(f"SerpApi: {message[:200]}")
    return data


def _serpapi_fetch(keywords: list[str], geo: str, timeframe: str) -> TrendResult:
    base = {"engine": "google_trends", "geo": geo, "date": timeframe}
    result = TrendResult(keywords=keywords, geo=geo, timeframe=timeframe, provider="serpapi",
                         fetched_at=timezone.now().isoformat())
    data = _serpapi({**base, "q": ",".join(keywords), "data_type": "TIMESERIES"})
    for point in (data.get("interest_over_time") or {}).get("timeline_data", []):
        values = {v.get("query"): int(v.get("extracted_value") or 0) for v in point.get("values", [])}
        ts = int(point.get("timestamp") or 0)
        result.timeline.append({
            "date": datetime.fromtimestamp(ts, tz=dt_timezone.utc).date().isoformat(),
            "label": point.get("date", ""),
            "values": [values.get(k, 0) for k in keywords],
            "partial": bool(point.get("partial_data")),
        })
    if not result.timeline:
        raise TrendsError("O Google Trends não retornou dados para esses termos.")
    for kw in keywords:
        related = (_serpapi({**base, "q": kw, "data_type": "RELATED_QUERIES"}).get("related_queries") or {})
        result.related[kw] = {
            "top": [{"query": i["query"], "value": str(i.get("value", ""))} for i in related.get("top", [])][:10],
            "rising": [{"query": i["query"], "value": str(i.get("value", ""))}
                       for i in related.get("rising", [])][:10],
        }
    if len(keywords) == 1:
        rows = _serpapi({**base, "q": keywords[0], "data_type": "GEO_MAP_0"}).get("interest_by_region", [])
        result.regions = [{"code": r.get("geo", ""), "name": r.get("location", ""),
                           "values": [int(r.get("extracted_value") or 0)]} for r in rows]
    else:
        rows = _serpapi({**base, "q": ",".join(keywords), "data_type": "GEO_MAP"}).get(
            "compared_breakdown_by_region", [])
        for r in rows:
            values = {v.get("query"): int(v.get("extracted_value") or 0) for v in r.get("values", [])}
            result.regions.append({"code": r.get("geo", ""), "name": r.get("location", ""),
                                   "values": [values.get(k, 0) for k in keywords]})
    return result


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def interest(keywords: list[str], geo: str = "BR", timeframe: str = "today 12-m", *,
             allow_fetch: bool = True) -> tuple[TrendResult, bool]:
    """
    Retorna (resultado, veio_do_cache). Levanta TrendsError com mensagem amigável.
    Com `allow_fetch=False` (limite de pesquisas atingido) só responde o que já estiver em cache.
    """
    keywords = clean_keywords(",".join(keywords))
    if not keywords:
        raise TrendsError("Informe ao menos um produto ou termo para pesquisar.")
    if geo not in dict(REGIONS) or timeframe not in dict(TIMEFRAMES):
        raise TrendsError("Região ou período inválido.")
    key = _cache_key("interest", provider_name(), [k.lower() for k in keywords], geo, timeframe)
    cached = cache_get(key, timedelta(hours=settings.TRENDS_CACHE_HOURS))
    if cached:
        return TrendResult.from_dict(cached), True
    if not allow_fetch:
        raise TrendsRateLimited("Limite de pesquisas por hora da empresa atingido. Tente novamente mais tarde "
                                "(pesquisas já feitas continuam disponíveis).")
    fetch = _serpapi_fetch if settings.SERPAPI_API_KEY else _google_fetch
    result = fetch(keywords, geo, timeframe)
    cache_set(key, result.to_dict())
    return result, False


def trending_now(geo: str = "BR", limit: int = 20) -> list[dict]:
    """
    Buscas em alta agora (feed público do Google Trends). Falhas não interrompem a página: retorna [].
    [{"query": "...", "traffic": "20 mil+", "news": "título", "url": "https://..."}]
    """
    geo = geo.split("-")[0] or "BR"
    key = _cache_key("trending", provider_name(), geo)
    cached = cache_get(key, timedelta(hours=1))
    if cached is not None:
        return cached.get("items", [])[:limit]
    items: list[dict] = []
    try:
        if settings.SERPAPI_API_KEY:
            data = _serpapi({"engine": "google_trends_trending_now", "geo": geo})
            for row in data.get("trending_searches", [])[:limit]:
                volume = row.get("search_volume")
                items.append({"query": row.get("query", ""),
                              "traffic": f"{volume:,}+".replace(",", ".") if isinstance(volume, int) else "",
                              "news": ", ".join(c.get("name", "") for c in row.get("categories", [])[:2]),
                              "url": ""})
        else:
            items = parse_trending_rss(_read(request.build_opener(), f"{GOOGLE}/trending/rss?geo={geo}",
                                             timeout=10))[:limit]
    except TrendsError as exc:
        logger.info("Em alta agora indisponível: %s", exc)
        return []
    cache_set(key, {"items": items})
    return items


def parse_trending_rss(text: str) -> list[dict]:
    ns = {"ht": "https://trends.google.com/trending/rss"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        news = item.find("ht:news_item", ns)
        url = news.findtext("ht:news_item_url", default="", namespaces=ns) if news is not None else ""
        items.append({
            "query": (item.findtext("title") or "").strip(),
            "traffic": (item.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip(),
            "news": news.findtext("ht:news_item_title", default="", namespaces=ns) if news is not None else "",
            "url": url if url.startswith("https://") else "",
        })
    return [i for i in items if i["query"]]
