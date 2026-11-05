"""Logs estruturados em JSON e filtro de dados sensíveis."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

_SENSITIVE = re.compile(r"(password|senha|token|secret|authorization)(['\"]?\s*[:=]\s*['\"]?)([^'\"\s,&]+)", re.I)


class RedactSensitiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = _SENSITIVE.sub(r"\1\2***", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
