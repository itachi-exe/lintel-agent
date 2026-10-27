"""Zetaris relay: lets the hosted Lintel reach a Zetaris platform that has no public address.

Runs next to the platform (same machine as the Docker stack) and is the only thing
exposed. It takes one agent statement, checks it is a shape the agent writes
(app.zetaris.safe: allow-listed tables, plain columns or count(*), ORDER BY one
column), and runs it through the platform channel: the WHERE is pushed down to the
city API in a Zetaris REST SESSION TABLE, the rest runs as Spark SQL in Zetaris.

    ZETARIS_URL / ZETARIS_USER / ZETARIS_PASSWORD   the platform
    ZETARIS_RELAY_KEY                                shared key, required
    RELAY_PORT                                       default 8655, binds 127.0.0.1

    python -m zetaris.relay
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import zetaris  # noqa: E402

KEY = os.environ.get("ZETARIS_RELAY_KEY", "")
GATE = threading.BoundedSemaphore(3)  # the engine has two cores; queue the rest


class Relay(BaseHTTPRequestHandler):
    server_version = "lintel-zetaris-relay"

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/health"):
            return self._send(200, {"ok": True, "zetaris": zetaris.platform()})
        self._send(404, {"ok": False})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/run"):
            return self._send(404, {"ok": False})
        if not KEY or not hmac.compare_digest(self.headers.get("X-Relay-Key", ""), KEY):
            return self._send(401, {"ok": False, "error": "key"})
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 8000)
            body = json.loads(self.rfile.read(n) or b"{}")
            sql, table = str(body.get("sql") or ""), str(body.get("table") or "").lower()
            limit = max(1, min(int(body.get("limit") or 200), 1000))
        except (ValueError, TypeError):
            return self._send(400, {"ok": False, "error": "bad request"})
        if not zetaris.safe(sql, table):
            return self._send(400, {"ok": False, "error": "statement not allowed"})
        if not GATE.acquire(timeout=20):
            return self._send(503, {"ok": False, "error": "busy"})
        try:
            rows = zetaris._platform(sql, table, limit)
        except Exception as exc:  # noqa: BLE001 - reported, never raised to the client
            return self._send(502, {"ok": False, "error": type(exc).__name__})
        finally:
            GATE.release()
        self._send(200, {"ok": True, "via": "zetaris", "rows": rows})

    def log_message(self, fmt: str, *args) -> None:  # one line, no bodies
        sys.stderr.write("relay %s %s\n" % (self.command, args[1] if len(args) > 1 else ""))


def main() -> None:
    if not KEY or not zetaris.platform():
        sys.exit("set ZETARIS_RELAY_KEY and ZETARIS_URL / ZETARIS_USER / ZETARIS_PASSWORD")
    port = int(os.environ.get("RELAY_PORT", "8655"))
    ThreadingHTTPServer(("127.0.0.1", port), Relay).serve_forever()


if __name__ == "__main__":
    main()
