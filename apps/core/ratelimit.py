"""Rate limiting simples baseado em cache (use Redis com várias réplicas)."""

from __future__ import annotations

import hashlib

from django.core.cache import cache


def _key(scope: str, ident: str) -> str:
    return f"rl:{scope}:{hashlib.sha256(ident.encode()).hexdigest()[:32]}"


def is_limited(scope: str, ident: str, limit: int) -> bool:
    return (cache.get(_key(scope, ident)) or 0) >= limit


def hit(scope: str, ident: str, window: int) -> int:
    key = _key(scope, ident)
    if cache.add(key, 1, timeout=window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
        return 1


def reset(scope: str, ident: str) -> None:
    cache.delete(_key(scope, ident))
