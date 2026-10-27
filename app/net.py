"""One JSON GET with a timeout and a small disk cache. Every outside read goes through here."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import HTTP_TIMEOUT, USER_AGENT


class FetchError(Exception):
    """An outside source did not answer with JSON."""


def _cache_dir() -> Path | None:
    d = Path(os.environ.get("LINTEL_CACHE_DIR", "/tmp/lintel-cache"))
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _ttl() -> float:
    try:
        return float(os.environ.get("LINTEL_CACHE_TTL", "3600"))
    except ValueError:
        return 3600.0


def _key(url: str, params: dict[str, Any] | None) -> str:
    raw = url + "?" + json.dumps(params or {}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_json(url: str, params: dict[str, Any] | None = None, *, timeout: float | None = None,
             headers: dict[str, str] | None = None, cache: bool = True) -> Any:
    """GET url and parse JSON. Cached on disk for LINTEL_CACHE_TTL seconds (0 turns it off)."""
    ttl = _ttl()
    d = _cache_dir() if cache and ttl > 0 else None
    path = d / (_key(url, params) + ".json") if d else None
    if path and path.exists() and time.time() - path.stat().st_mtime < ttl:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token and "data.cityofnewyork.us" in url:
        h["X-App-Token"] = token
    h.update(headers or {})
    try:
        r = httpx.get(url, params=params, headers=h, timeout=timeout or HTTP_TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(f"{httpx.URL(url).host} did not answer ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        detail = ""
        try:
            body = r.json()
            detail = str(body.get("message") or body.get("error") or "")[:120] if isinstance(body, dict) else ""
        except ValueError:
            pass
        raise FetchError(f"{httpx.URL(url).host} said {r.status_code}" + (f": {detail}" if detail else ""))
    try:
        data = r.json()
    except ValueError as exc:
        raise FetchError(f"{httpx.URL(url).host} sent something that is not JSON") from exc
    if path:
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError:
            pass
    return data
