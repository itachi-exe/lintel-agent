"""The building card behind POST /api/core: four record sections and one verdict."""

from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import sources
from .config import BOROUGHS, LINK
from .net import FetchError
from .plain import dob_line, plain_line, sr311_line
from .resolve import Resolved, clean_bbl, resolve_bbl

MAX_ITEMS = 8


class CardError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _day(v: Any) -> str:
    s = re.sub(r"\D", "", str(v or ""))[:8]
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
    except (ValueError, IndexError):
        return ""


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _title(s: str) -> str:
    return " ".join(w.capitalize() if not re.match(r"^\d", w) else w.lower() for w in (s or "").split())


def hpd_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def is_open(r: dict[str, Any]) -> bool:
        return (r.get("violationstatus") or "").lower() == "open"
    ranked = sorted(rows, key=lambda r: (not is_open(r), {"C": 0, "B": 1, "A": 2}.get(r.get("class") or "", 3)))
    items = []
    for r in ranked[:MAX_ITEMS]:
        line, spot = plain_line(r.get("novdescription") or "")
        items.append({"line": line + (f" {spot}." if spot else ""), "date": _day(r.get("inspectiondate")),
                      "class": r.get("class"), "open": is_open(r), "source": "HPD violations",
                      "record": str(r.get("violationid") or ""),
                      "url": LINK["hpd_violation"].format(id=r.get("violationid"))})
    open_rows = [r for r in rows if is_open(r)]
    by_class = {c: sum(1 for r in open_rows if r.get("class") == c) for c in ("C", "B", "A")}
    return {"id": "hpd", "title": "Housing violations", "count_open": len(open_rows), "count_total": len(rows),
            "open_by_class": by_class, "items": items}


def sr311_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def is_open(r: dict[str, Any]) -> bool:
        return (r.get("status") or "").lower() not in ("closed", "")
    kinds: dict[str, int] = {}
    for r in rows:
        k = sr311_line(r.get("complaint_type") or "")
        kinds[k] = kinds.get(k, 0) + 1
    items = [{"line": sr311_line(r.get("complaint_type") or ""), "detail": r.get("descriptor") or "",
              "date": _day(r.get("created_date")), "open": is_open(r), "source": "311 requests",
              "record": str(r.get("unique_key") or ""), "url": LINK["sr311"].format(id=r.get("unique_key"))}
             for r in rows[:MAX_ITEMS]]
    year_ago = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    return {"id": "311", "title": "311 complaints", "count_open": sum(1 for r in rows if is_open(r)),
            "count_total": len(rows), "count_12m": sum(1 for r in rows if _day(r.get("created_date")) >= year_ago),
            # complaints about the building itself (routed to HPD), not street noise or parking
            "count_12m_housing": sum(1 for r in rows if _day(r.get("created_date")) >= year_ago
                                     and (r.get("agency") or "").upper() == "HPD"),
            "top": [{"line": k, "count": v} for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:4]],
            "items": items}


def dob_section(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    if rows is None:
        return {"id": "dob", "title": "Building department violations", "count_open": 0, "count_total": 0,
                "items": [], "line": "No building number on record, so no Buildings Department lookup."}
    def is_open(r: dict[str, Any]) -> bool:
        return "ACTIVE" in (r.get("violation_category") or "").upper()
    ranked = sorted(rows, key=lambda r: not is_open(r))
    items = [{"line": dob_line(r.get("violation_category") or "", r.get("violation_type") or ""),
              "date": _day(r.get("issue_date")), "open": is_open(r), "source": "DOB violations",
              "record": str(r.get("isn_dob_bis_viol") or ""),
              "url": LINK["dob_violation"].format(id=r.get("isn_dob_bis_viol"))} for r in ranked[:MAX_ITEMS]]
    return {"id": "dob", "title": "Building department violations", "count_open": sum(1 for r in rows if is_open(r)),
            "count_total": len(rows), "items": items}


def flood_section(inside: bool | None) -> dict[str, Any]:
    if inside is None:
        line = "No map point for this building, so the flood map was not checked."
    elif inside:
        line = "Inside the city's 2050s floodplain. Ask about flood insurance and the basement."
    else:
        line = "Outside the city's 2050s floodplain."
    return {"id": "flood", "title": "Flood risk 2050s", "line": line, "inside": inside, "count_open": int(bool(inside)),
            "count_total": int(bool(inside)), "items": [{"line": line, "date": "", "open": bool(inside),
                                                         "source": "NYC Future Floodplain 2050s", "record": "27ya-gqtm",
                                                         "url": LINK["flood"]}]}


def verdict(hpd: dict[str, Any], s311: dict[str, Any], dob: dict[str, Any], homes: int | None) -> dict[str, str]:
    c = hpd["open_by_class"]["C"]
    b = hpd["open_by_class"]["B"]
    active = dob["count_open"]
    calls = s311["count_12m_housing"]
    per_home = calls / homes if homes else calls / 10
    if c >= 5 or c + b >= 25 or active >= 5 or (c >= 2 and per_home >= 2):
        level = "bad"
    elif c == 0 and b <= 3 and active == 0 and per_home < 1:
        level = "good"
    else:
        level = "mixed"
    bits = []
    if c:
        bits.append(f"{c} serious housing problem{'s' if c != 1 else ''} still open")
    if b:
        bits.append(f"{b} hazardous one{'s' if b != 1 else ''} still open")
    if active:
        bits.append(f"{active} active Buildings Department violation{'s' if active != 1 else ''}")
    if calls:
        bits.append(f"{calls} housing complaint{'s' if calls != 1 else ''} to 311 in the last year")
    if level == "good":
        line = "The records look clean: " + (", ".join(bits) if bits else "no open housing problems") + "."
    elif level == "bad":
        line = "The records show trouble: " + ", ".join(bits) + ". Look closely before you sign."
    else:
        line = "Some problems on file: " + (", ".join(bits) if bits else "a few older ones") + ". Ask the landlord about them."
    return {"level": level, "line": line}


def build(bbl_in: Any) -> dict[str, Any]:
    bbl = clean_bbl(bbl_in)
    if not bbl:
        raise CardError(400, "That is not a BBL. Pick an address from the search list.")
    res: Resolved = resolve_bbl(bbl)
    if not res.lot:
        raise CardError(404, "No lot record for that BBL. Search the street address instead.")
    m = res.match
    bin_value = m.bin if m else None
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_hpd = pool.submit(sources.hpd_violations, bbl)
        f_311 = pool.submit(sources.sr311, bbl)
        f_dob = pool.submit(sources.dob_violations, bin_value) if bin_value else None
        f_fl = pool.submit(sources.flood_2050, m.lat, m.lon) if m and m.lat is not None else None
        try:
            hpd_rows, rows_311 = f_hpd.result(), f_311.result()
            dob_rows = f_dob.result() if f_dob else None
        except FetchError as exc:
            raise CardError(502, f"NYC Open Data did not answer ({exc}). Try again in a minute.") from exc
        try:
            inside = f_fl.result() if f_fl else None
        except FetchError:
            inside = None
    lot = res.lot
    homes = _int(lot.get("unitsres"))
    hpd, s311, dob = hpd_section(hpd_rows), sr311_section(rows_311), dob_section(dob_rows)
    boro = BOROUGHS.get(bbl[0], "")
    return {
        "bbl": bbl, "bin": bin_value,
        "address": (m.label if m and m.label else f"{_title(lot.get('address') or '')}, {boro}"),
        "borough": boro,
        "lat": m.lat if m else None, "lon": m.lon if m else None,
        "lot": {"year_built": _int(lot.get("yearbuilt")) or None, "floors": _int(lot.get("numfloors")),
                "homes": homes, "owner": (lot.get("ownername") or "").strip() or None,
                "building_class": lot.get("bldgclass")},
        "verdict": verdict(hpd, s311, dob, homes),
        "sections": [hpd, s311, dob, flood_section(inside)],
        "links": {"zola": LINK["zola"].format(bbl=bbl), "hpd": LINK["hpd_building"].format(bbl=bbl),
                  "dob": LINK["dob_building"].format(bin=bin_value) if bin_value else None},
        "window": "last 3 years",
    }
