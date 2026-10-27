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

Config, all optional:
    ZETARIS_REST_URL  e.g. https://<cluster>/api/v1.0 (default unset = direct)
    ZETARIS_API_KEY   bearer key
    ZETARIS_ORG_ID    defaults to 1
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
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


def configured() -> bool:
    return bool(os.environ.get("ZETARIS_REST_URL") and os.environ.get("ZETARIS_API_KEY"))


def status() -> dict[str, Any]:
    return {"configured": configured(), "database": "NYC", "tables": sorted(TABLES)}


def _zetaris(sql: str, limit: int) -> list[dict[str, Any]]:
    base = os.environ["ZETARIS_REST_URL"].rstrip("/")
    req = urllib.request.Request(
        base + "/sql-editor/sqls/run",
        data=json.dumps({"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor",
                         "limit": limit}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['ZETARIS_API_KEY']}",
            "X-Org-ID": os.environ.get("ZETARIS_ORG_ID", "1"),
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        out = json.loads(resp.read().decode() or "null")
    data = out.get("data") if isinstance(out, dict) else None
    # The REST channel returns either a list of objects or {columns, rows}.
    if isinstance(data, dict) and "columns" in data:
        cols = [c.get("name") if isinstance(c, dict) else c for c in data["columns"]]
        return [dict(zip(cols, r)) for r in data.get("rows") or []]
    return list(data or [])


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
            q.rows, q.via = _zetaris(sql, limit), "zetaris"
            return q
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            q.error = f"zetaris unavailable ({type(exc).__name__}), ran direct"
    try:
        q.rows, q.via = _direct(sql, table), "direct"
    except FetchError as exc:
        q.ok, q.via, q.error = False, "direct", str(exc)[:160]
    return q
