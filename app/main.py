"""Lintel Agent HTTP API. Run: python3.12 -m uvicorn app.main:app --port 8740"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import card, llm, zetaris
from .ask import ask as run_ask
from .net import FetchError
from .resolve import clean_bbl, geocode

ROOT = Path(__file__).resolve().parent.parent
WEB = next((p for p in (ROOT / "web", ROOT / "public") if (p / "index.html").exists()), ROOT / "web")

app = FastAPI(title="Lintel Agent", docs_url=None, redoc_url=None)


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class CoreBody(BaseModel):
    bbl: str | None = None


class AskBody(BaseModel):
    bbl: str | None = None
    question: str | None = None
    workplace: str | None = None
    intent: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    st = llm.INSTANCE.status()
    return {"ok": True, "zetaris": {"configured": zetaris.configured()},
            "llm": {"provider": st["provider"], "mode": st["mode"]}}


@app.get("/api/geocode")
def api_geocode(q: str = "") -> Any:
    if len(q.strip()) < 3:
        return err(400, "Type at least a house number and a street.")
    try:
        hits = geocode(q, size=5)
    except FetchError:
        return err(502, "The city address search did not answer. Try again in a minute.")
    return {"results": [h.as_dict() for h in hits if h.bbl]}


@app.post("/api/core")
def api_core(body: CoreBody) -> Any:
    try:
        return card.build(body.bbl)
    except card.CardError as exc:
        return err(exc.status, exc.message)


@app.post("/api/ask")
def api_ask(body: AskBody, request: Request) -> Any:
    bbl = clean_bbl(body.bbl)
    if not bbl:
        return err(400, "Load a building first, then ask about it.")
    if not (body.question or "").strip() and not body.intent:
        return err(400, "Type a question about this building.")
    key = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "")
    try:
        return run_ask(bbl, body.question or "", workplace=body.workplace, intent=body.intent, rate_key=key)
    except FetchError:
        return err(502, "A city record source did not answer. Try again in a minute.")


@app.get("/", include_in_schema=False)
def index() -> Any:
    page = WEB / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"ok": True, "note": "Frontend not built yet. The API is at /api/health."})


if (WEB / "static").is_dir():
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
