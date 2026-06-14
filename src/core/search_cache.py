"""
src/core/search_cache.py
SQLite-backed cache for provider search responses (v0.2.1 free-quota work).

Search credits (Serper et al.) are the scarcest resource in the pipeline;
re-running or resuming a pipeline must never pay twice for the same query.
Entries are keyed by a hash of the canonicalized request payload and expire
after a caller-supplied TTL. A TTL of 0 (or less) disables the cache
entirely — both reads and writes become no-ops — which keeps unit tests and
cache-averse callers free of any DB side effects.
"""

from __future__ import annotations

import hashlib
import json

from src.core.db import get_connection, init_db, with_writer

__all__ = ["cache_get", "cache_put"]


def _cache_key(provider: str, payload: dict) -> str:
    """Return a stable hash key for (*provider*, *payload*).

    Inputs: provider name and the JSON-serializable request payload.
    Output: hex digest; identical payloads with different key order map to
    the same key (canonical JSON with sorted keys). Pure function.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{provider}:{canonical}".encode()).hexdigest()
    return digest


def cache_get(provider: str, payload: dict, ttl_days: int) -> dict | None:
    """Return the cached response for (*provider*, *payload*), or None.

    Inputs: provider name, request payload dict, TTL in days. Output: the
    cached response dict when a non-expired entry exists; ``None`` on miss,
    expiry, or when *ttl_days* <= 0 (cache disabled). Side effects: reads
    the state DB (and initializes it on first use).
    """
    if ttl_days <= 0:
        return None
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT response FROM search_cache "
            "WHERE cache_key = ? AND created_at >= datetime('now', ?)",
            (_cache_key(provider, payload), f"-{int(ttl_days)} days"),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return json.loads(row["response"])
    except (json.JSONDecodeError, TypeError):
        return None


def cache_put(provider: str, payload: dict, response: dict) -> None:
    """Store *response* for (*provider*, *payload*), replacing any entry.

    Inputs: provider name, request payload dict, JSON-serializable response
    dict. Output: none. Side effects: writes one row to ``search_cache``
    (insert-or-replace, so re-fetches refresh the entry's timestamp).
    """
    init_db()
    with with_writer() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO search_cache "
            "(cache_key, provider, query, response, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (
                _cache_key(provider, payload),
                provider,
                json.dumps(payload, sort_keys=True),
                json.dumps(response),
            ),
        )
