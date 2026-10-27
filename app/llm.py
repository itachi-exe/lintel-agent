"""The model that writes the sentence, and an honest record of which one did.

1. Meterless Nightshift (primary). OpenAI compatible chat completions, routing mode
   ``meterless/auto``, header ``meterless-app: lintel``, 180 s timeout, status read
   from GET /v1/models.
2. A fallback OpenAI compatible provider, only when Nightshift does not answer.
3. Neither: a deterministic summary made from the facts, provider ``none``.

``compose`` returns (text, {"provider", "mode", "model"}) and never names a provider
that did not answer.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx


def _root(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def _chat(base: str, key: str, model: str, system: str, user: str, timeout: float,
          headers: dict[str, str] | None = None) -> tuple[str, str]:
    h = {"Content-Type": "application/json", **(headers or {})}
    if key:
        h["Authorization"] = f"Bearer {key}"
    r = httpx.post(_root(base) + "/v1/chat/completions", headers=h, timeout=timeout, json={
        "model": model, "temperature": 0.2, "max_tokens": 300,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    body = r.json()
    text = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    # reasoning models sometimes wrap a think block around the answer
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        raise ValueError("empty completion")
    return text, str(body.get("model") or model)


class LLM:
    def __init__(self) -> None:
        self._status: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------- config
    @staticmethod
    def meterless_token() -> str:
        tok = os.environ.get("METERLESS_TOKEN", "").strip()
        f = os.environ.get("METERLESS_TOKEN_FILE", "").strip()
        if not tok and f:
            try:
                tok = Path(f).expanduser().read_text().strip()
            except OSError:
                tok = ""
        return tok

    @staticmethod
    def meterless_base() -> str:
        return os.environ.get("METERLESS_BASE_URL", "").strip()

    @staticmethod
    def meterless_model() -> str:
        return os.environ.get("METERLESS_MODEL", "meterless/auto").strip() or "meterless/auto"

    @staticmethod
    def fallback() -> tuple[str, str, str]:
        return (os.environ.get("LINTEL_LLM_BASE", "").strip(), os.environ.get("LINTEL_LLM_KEY", "").strip(),
                os.environ.get("LINTEL_LLM_MODEL", "").strip())

    def meterless_configured(self) -> bool:
        return bool(self.meterless_base())

    def fallback_configured(self) -> bool:
        base, _, model = self.fallback()
        return bool(base and model)

    # ---------------------------------------------------------- status
    def meterless_status(self, ttl: float = 60.0) -> dict[str, Any]:
        """GET /v1/models on Nightshift, cached for ``ttl`` seconds."""
        if not self.meterless_configured():
            return {"configured": False, "up": False}
        with self._lock:
            if self._status and time.time() - self._status[0] < ttl:
                return self._status[1]
        st: dict[str, Any] = {"configured": True, "up": False}
        try:
            h = {"meterless-app": "lintel"}
            if self.meterless_token():
                h["Authorization"] = f"Bearer {self.meterless_token()}"
            r = httpx.get(_root(self.meterless_base()) + "/v1/models", headers=h, timeout=8)
            r.raise_for_status()
            ids = [m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)]
            st.update(up=True, models=ids[:20])
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            st["error"] = type(exc).__name__
        with self._lock:
            self._status = (time.time(), st)
        return st

    def provider(self) -> str:
        """Which provider would answer right now: meterless, fallback or none."""
        if self.meterless_configured() and self.meterless_status().get("up"):
            return "meterless"
        if self.fallback_configured():
            return "fallback"
        return "none"

    def status(self) -> dict[str, Any]:
        return {"provider": self.provider(), "mode": self.meterless_model(),
                "meterless": self.meterless_status(), "fallback": {"configured": self.fallback_configured()}}

    # ---------------------------------------------------------- compose
    def compose(self, system: str, user: str) -> tuple[str, dict[str, Any]]:
        mode = self.meterless_model()
        if self.meterless_configured():
            try:
                timeout = float(os.environ.get("METERLESS_TIMEOUT", "180"))
                text, model = _chat(self.meterless_base(), self.meterless_token(), mode, system, user, timeout,
                                    {"meterless-app": "lintel"})
                return text, {"provider": "meterless", "mode": mode, "model": model}
            except (httpx.HTTPError, ValueError, KeyError, IndexError, AttributeError):
                pass
        if self.fallback_configured():
            base, key, fmodel = self.fallback()
            try:
                text, model = _chat(base, key, fmodel, system, user, 60)
                return text, {"provider": "fallback", "mode": "direct", "model": model}
            except (httpx.HTTPError, ValueError, KeyError, IndexError, AttributeError):
                pass
        return summarize(user), {"provider": "none", "mode": "rules", "model": None}


def summarize(user: str) -> str:
    """No model reachable: the facts as read, joined in order. Nothing added."""
    facts = [ln[2:].strip() for ln in (user or "").splitlines() if ln.startswith("- ") and ln[2:].strip() != "nothing"]
    return " ".join(facts) if facts else "The records show nothing on this."


INSTANCE = LLM()
