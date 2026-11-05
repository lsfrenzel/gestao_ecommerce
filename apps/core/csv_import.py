"""Leitura segura de CSV enviado pelo usuário (tamanho, linhas, codificação e delimitador)."""

from __future__ import annotations

import csv
import io
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError


def normalize_header(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.replace(" ", "_").replace("-", "_")


def read_csv(uploaded) -> list[dict[str, str]]:
    max_bytes = settings.CSV_IMPORT_MAX_MB * 1024 * 1024
    if uploaded.size > max_bytes:
        raise ValidationError(f"Arquivo maior que {settings.CSV_IMPORT_MAX_MB} MB.")
    raw = uploaded.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise ValidationError("Não foi possível ler a codificação do arquivo. Salve como CSV UTF-8.")
    sample = text[:4096]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValidationError("O arquivo está vazio ou sem cabeçalho.")
    rows = []
    for index, row in enumerate(reader, start=1):
        if index > settings.CSV_IMPORT_MAX_ROWS:
            raise ValidationError(f"O arquivo excede o limite de {settings.CSV_IMPORT_MAX_ROWS} linhas.")
        clean = {normalize_header(k): (v or "").strip() for k, v in row.items() if k}
        if any(clean.values()):
            rows.append(clean)
    if not rows:
        raise ValidationError("Nenhuma linha de dados encontrada.")
    return rows
