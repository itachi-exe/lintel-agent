"""Zetaris, the data layer the agent queries through.

Every question Lintel answers becomes one or more SQL statements over the NYC
open records. The same statement runs in one of two places:

1. Zetaris, when an instance is configured. The NYC datasets are registered as
   Lightning REST tables under the database NYC (see zetaris/register.sql) and
   the statement is sent to the Zetaris REST API (POST /sql-editor/sqls/run),
   the same channel the zetaris/getting-started repo uses.
2. NYC Open Data directly, when no instance is configured. Socrata speaks SoQL,
   which is SQL without the FROM clause, so the statement runs as is.

The answer always reports which one ran (``via``). Nothing pretends.

Two ways to reach Zetaris, all config optional:

Platform (the local Docker stack, or any Lightning gateway):
    ZETARIS_URL       e.g. http://localhost:3000
    ZETARIS_USER      platform user (email)
    ZETARIS_PASSWORD  its password
  Each statement is federated in two steps. The row filter (its WHERE) is pushed
  down to the city API through a Zetaris REST SESSION TABLE, so Zetaris pulls only
  this building's records; then the rest of the statement (columns, ORDER BY,
  LIMIT, count) runs as Spark SQL inside Zetaris over that table. The session
  table is dropped after.

Relay (how the hosted demo reaches a Zetaris platform that is not on the internet):
    ZETARIS_RELAY_URL  the relay in zetaris/relay.py, which runs next to the platform
    ZETARIS_RELAY_KEY  shared key
  The relay accepts only the agent's statement shape (allow-listed tables, plain
  columns or count(*), ORDER BY one column) and runs it with the platform channel.

Cloud REST channel:
    ZETARIS_REST_URL  e.g. https://<cluster>/api/v1.0
    ZETARIS_API_KEY   bearer key
    ZETARIS_ORG_ID    defaults to 1
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import DS, SOCRATA
from .net import FetchError, get_json

# Table name in Zetaris -> Socrata dataset key in config.DS
TABLES = {
    "hpd_violations": "hpd_violations",
    "sr311": "311",
    "dob_violations": "dob_violations",
    "pluto": "pluto",
    "flood_2050": "flood_2050",
}

_FROM = re.compile(r"\s+FROM\s+nyc\.(\w+)", re.I)


@dataclass
class Query:
    """One statement plus where it ran and what came back."""

    sql: str
    table: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    via: str = ""
    ok: bool = True
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"sql": self.sql, "table": self.table, "via": self.via, "rows": len(self.rows),
                "ok": self.ok, "error": self.error or None}


def platform() -> bool:
    return bool(os.environ.get("ZETARIS_URL") and os.environ.get("ZETARIS_USER")
                and os.environ.get("ZETARIS_PASSWORD"))


def relay() -> bool:
    return bool(os.environ.get("ZETARIS_RELAY_URL") and os.environ.get("ZETARIS_RELAY_KEY"))


def configured() -> bool:
    return platform() or relay() or bool(os.environ.get("ZETARIS_REST_URL") and os.environ.get("ZETARIS_API_KEY"))


def status() -> dict[str, Any]:
    return {"configured": configured(), "mode": "platform" if platform() else "relay" if relay() else ("rest" if configured() else "direct"),
            "database": "NYC", "tables": sorted(TABLES)}


# ------------------------------------------------------------ platform channel

_SESSION: dict[str, Any] = {}
_SPLIT = re.compile(r"^\s*SELECT\s+(?P<cols>.+?)\s+FROM\s+nyc\.(?P<table>\w+)"
                    r"(?:\s+WHERE\s+(?P<where>.+?))?(?P<tail>\s+ORDER\s+BY\s+.+?)?(?P<limit>\s+LIMIT\s+\d+)?\s*$",
                    re.I | re.S)


def _post(path: str, body: dict[str, Any], token: str | None = None, timeout: int = 90) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers.update({"Authorization": f"Bearer {token}", "X-Org-ID": str(_SESSION.get("org", "")),
                        "X-Request-ID": str(uuid.uuid4())})
    req = urllib.request.Request(os.environ["ZETARIS_URL"].rstrip("/") + path,
                                 data=json.dumps(body).encode(), method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "null")


def _login() -> str:
    # Platform tokens live 15 minutes; renew well before that.
    if _SESSION.get("token") and time.monotonic() - _SESSION.get("at", 0.0) < 600:
        return _SESSION["token"]
    _SESSION.clear()
    out = _post("/api/auth/login", {"username": os.environ["ZETARIS_USER"],
                                    "password": os.environ["ZETARIS_PASSWORD"]})
    blob = json.dumps(out)
    tok = re.search(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", blob)
    org = re.search(r'"orgId"\s*:\s*(\d+)', blob)
    if not tok or not org:
        raise ValueError("zetaris login returned no token")
    _SESSION.update(token=tok.group(0), org=org.group(1), at=time.monotonic())
    return _SESSION["token"]


def _rows(out: Any) -> list[dict[str, Any]]:
    """Rows from a /sql-editor/sqls/run response: {headers: [...], data: [[...], ...]}."""
    if not isinstance(out, dict):
        return []
    heads = out.get("headers") or []
    return [dict(zip(heads, r)) for r in out.get("data") or [] if isinstance(r, list)]


def _lightning(sql: str, limit: int = 5000) -> dict[str, Any]:
    """One Lightning SQL statement through the platform; retries once on an expired login."""
    body = {"queryId": "", "sql": sql, "source": "SqlEditor", "limit": limit}
    for attempt in (0, 1):
        body["queryId"] = str(uuid.uuid4())
        try:
            out = _post("/api/proxy/sql-editor/sqls/run", body, _login())
        except urllib.error.HTTPError as exc:
            # an expired token comes back as a bare 400 "Bad Request", not 401
            if exc.code in (400, 401, 403) and attempt == 0:
                _SESSION.clear()
                continue
            detail = exc.read().decode(errors="replace")[:200]
            raise ValueError(f"zetaris {exc.code}: {detail}") from exc
        return out if isinstance(out, dict) else {}
    return {}


_COL = re.compile(r"^[A-Za-z_]\w*$")


def _platform(sql: str, table: str, limit: int) -> list[dict[str, Any]]:
    m = _SPLIT.match(sql)
    if not m:
        raise ValueError("statement shape not supported for pushdown")
    where = (m.group("where") or "").strip()
    params = {"$where": where, "$limit": "5000"} if where else {"$limit": "5000"}
    endpoint = SOCRATA.format(ds=DS[TABLES[table]]) + "?" + urllib.parse.urlencode(params)
    name = f"lintel_{table}_{uuid.uuid4().hex[:10]}"
    _lightning(
        f'CREATE REST SESSION TABLE {name} REQUEST (endpoint "{endpoint}", method "GET", '
        f'http_encoding "URLENCODED", response_type "JSON", insecure_ssl "false") '
        f'HEADER ("User-Agent" "Lintel github.com/itachi-exe/lintel-agent") BODY ()')
    try:
        cols = [c.strip() for c in m.group("cols").split(",")]
        if all(_COL.match(c) for c in cols):
            # The schema is inferred from the rows that came back, so a field that is
            # null on every row is not a column. Ask what exists, select NULL for the rest.
            have = set(_lightning(f"SELECT * FROM session.{name} LIMIT 1", 1).get("headers") or [])
            if not have:
                return []
            cols = [c if c in have else f"NULL AS {c}" for c in cols]
            tail = m.group("tail") or ""
            if tail and not any(re.search(rf"\b{re.escape(h)}\b", tail) for h in have):
                tail = ""
        else:
            tail = m.group("tail") or ""
        return _rows(_lightning(f"SELECT {', '.join(cols)} FROM session.{name}{tail}{m.group('limit') or ''}",
                                limit))
    finally:
        try:
            _lightning(f"DROP TABLE session.{name}", 1)
        except Exception:  # noqa: BLE001 - cleanup only
            pass


_SAFE_COL = re.compile(r"^(?:[A-Za-z_]\w*|count\(\*\)\s+AS\s+n)$", re.I)
_SAFE_TAIL = re.compile(r"^\s+ORDER\s+BY\s+[A-Za-z_]\w*(?:\s+(?:ASC|DESC))?$", re.I)


def safe(sql: str, table: str) -> bool:
    """The relay's gate: only the shapes the agent itself writes."""
    m = _SPLIT.match(sql)
    if not m or m.group("table").lower() != table or table not in TABLES:
        return False
    cols = [c.strip() for c in m.group("cols").split(",")]
    return all(_SAFE_COL.match(c) for c in cols) and (not m.group("tail") or bool(_SAFE_TAIL.match(m.group("tail"))))


def _relay(sql: str, table: str, limit: int) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        os.environ["ZETARIS_RELAY_URL"].rstrip("/") + "/run",
        data=json.dumps({"sql": sql, "table": table, "limit": limit}).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Relay-Key": os.environ["ZETARIS_RELAY_KEY"]})
    with urllib.request.urlopen(req, timeout=25) as resp:
        out = json.loads(resp.read().decode() or "{}")
    if not out.get("ok"):
        raise ValueError(str(out.get("error") or "relay refused")[:160])
    return list(out.get("rows") or [])


def _direct(sql: str, table: str) -> list[dict[str, Any]]:
    soql = _FROM.sub("", sql)
    url = SOCRATA.format(ds=DS[TABLES[table]])
    return list(get_json(url, {"$query": soql}, timeout=35) or [])


def run(sql: str, *, limit: int = 200, use_zetaris: bool | None = None) -> Query:
    """Run one statement written as ``SELECT ... FROM nyc.<table> WHERE ...``."""
    m = _FROM.search(sql)
    table = m.group(1).lower() if m else ""
    if table not in TABLES:
        return Query(sql=sql, table=table, ok=False, error="unknown table", via="none")
    q = Query(sql=sql, table=table)
    if use_zetaris if use_zetaris is not None else configured():
        try:
            if platform():
                q.rows = _platform(sql, table, limit)
            elif relay():
                q.rows = _relay(sql, table, limit)
            else:
                q.rows = _zetaris(sql, limit)
            q.via = "zetaris"
            return q
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            q.error = f"zetaris unavailable ({type(exc).__name__}), ran direct"
    try:
        q.rows, q.via = _direct(sql, table), "direct"
    except FetchError as exc:
        q.ok, q.via, q.error = False, "direct", str(exc)[:160]
    return q
