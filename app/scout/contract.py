"""The signed execution contract: issue, verify, and the executor's scope check."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any

log = logging.getLogger("lintel.scout")
TTL_S = 300  # spec default, 5 minutes
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_ENV_KEY = os.environ.get("SCOUT_SIGNING_KEY", "")
KEY = _ENV_KEY.encode() or secrets.token_bytes(32)
EPHEMERAL = not _ENV_KEY  # per-process key: contracts die with the process


class ContractRefused(Exception):
    pass


def ulid() -> str:
    """48-bit ms timestamp + 80 random bits, Crockford base32: sortable like a ULID."""
    n = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sign(body: dict[str, Any]) -> str:
    return "hmac-sha256:" + hmac.new(KEY, _canonical(body), hashlib.sha256).hexdigest()


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def issue(**fields: Any) -> dict[str, Any]:
    now = time.time()
    body = {"id": "sc_" + ulid(), **fields, "issued_at": _iso(now), "expires_at": _iso(now + TTL_S),
            "signed_by": "scout", "ephemeral_key": EPHEMERAL}
    body["signature"] = _sign(body)
    log.info("contract.issued contractId=%s intent=%s risk=%s", body["id"], fields.get("intent"),
             (fields.get("risk") or {}).get("level"))
    return body


def verify_contract(contract: dict[str, Any], *, now: float | None = None) -> bool:
    """True only for an untampered contract, signed by this key, not past expires_at."""
    if not isinstance(contract, dict) or not isinstance(contract.get("signature"), str):
        return False
    body = {k: v for k, v in contract.items() if k != "signature"}
    if not hmac.compare_digest(_sign(body), contract["signature"]):
        return False
    try:
        expires = dt.datetime.fromisoformat(str(contract["expires_at"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return False
    return (now if now is not None else time.time()) < expires


def authorize_tool(contract: dict[str, Any], tool: str, capability: str | None = None) -> None:
    """Executor boundary: raise ContractRefused unless the tool (and capability) is in the signed plan."""
    if not verify_contract(contract):
        log.info("contract.refused contractId=%s reason=invalid-or-expired", contract.get("id"))
        raise ContractRefused("contract invalid or expired")
    ok = any(s["tool"] == tool and (capability is None or s["capability"] == capability) for s in contract["tool_plan"])
    if not ok:
        log.info("contract.refused contractId=%s reason=scope-mismatch tool=%s", contract.get("id"), tool)
        raise ContractRefused(f"{tool} is not in this contract's tool plan")
