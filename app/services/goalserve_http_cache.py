"""
Shared in-memory cache for Goalserve JSON feeds.

Within ``GOALSERVE_HTTP_CACHE_TTL_SECONDS`` (default 20), identical feed paths
return the cached JSON without calling Goalserve again — reduces 429 rate limits.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import httpx
import requests

from app.core.config import settings

_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = Lock()


class GoalserveHttpError(Exception):
    """Goalserve HTTP response that should not be cached."""

    def __init__(self, status_code: int, message: str, feed_path: str = ""):
        self.status_code = status_code
        self.feed_path = feed_path
        super().__init__(message)


def _normalize_path(feed_path: str) -> str:
    path = feed_path.strip().lstrip("/")
    if path.endswith("?json=1"):
        path = path[: -len("?json=1")]
    return path


def _ttl_seconds() -> float:
    return float(settings.GOALSERVE_HTTP_CACHE_TTL_SECONDS)


def _get_cached(key: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        if (now - entry["loaded_at"]) >= _ttl_seconds():
            return None
        return dict(entry["data"])


def _set_cached(key: str, data: Dict[str, Any]) -> None:
    with _LOCK:
        _CACHE[key] = {"data": data, "loaded_at": time.time()}


def cache_age_seconds(feed_path: str) -> Optional[float]:
    """Seconds since this feed was last fetched from Goalserve (None if not cached)."""
    key = _normalize_path(feed_path)
    with _LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        return time.time() - entry["loaded_at"]


def is_cache_fresh(feed_path: str) -> bool:
    return _get_cached(_normalize_path(feed_path)) is not None


def build_goalserve_url(feed_path: str) -> str:
    key = (settings.GOALSERVE_API_KEY or "").strip()
    if not key:
        raise GoalserveHttpError(0, "GOALSERVE_API_KEY is not set in .env")
    path = _normalize_path(feed_path)
    return f"{settings.GOALSERVE_BASE_URL}{key}/{path}?json=1"


def fetch_goalserve_json(
    feed_path: str,
    *,
    timeout: Optional[float] = None,
    force_refresh: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """
    Fetch a Goalserve JSON feed by path (e.g. ``soccernew/live``).

    Returns ``(data, from_cache)``.
    """
    key = _normalize_path(feed_path)
    if not force_refresh:
        cached = _get_cached(key)
        if cached is not None:
            return cached, True

    url = build_goalserve_url(feed_path)
    req_timeout = timeout if timeout is not None else settings.GOALSERVE_REQUEST_TIMEOUT_SECONDS
    response = requests.get(url, timeout=req_timeout)
    if response.status_code >= 400:
        raise GoalserveHttpError(
            response.status_code,
            f"Goalserve {response.status_code} for {key}",
            feed_path=key,
        )

    data = response.json()
    if not isinstance(data, dict):
        data = {}
    _set_cached(key, data)
    return data, False


async def fetch_goalserve_json_async(
    feed_path: str,
    *,
    timeout: Optional[float] = None,
    force_refresh: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """Async variant; uses the same process-wide cache as the sync helper."""
    key = _normalize_path(feed_path)
    if not force_refresh:
        cached = _get_cached(key)
        if cached is not None:
            return cached, True

    url = build_goalserve_url(feed_path)
    req_timeout = timeout if timeout is not None else settings.GOALSERVE_REQUEST_TIMEOUT_SECONDS
    async with httpx.AsyncClient(timeout=req_timeout) as client:
        response = await client.get(url)
        if response.status_code >= 400:
            raise GoalserveHttpError(
                response.status_code,
                f"Goalserve {response.status_code} for {key}",
                feed_path=key,
            )
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    _set_cached(key, data)
    return data, False
