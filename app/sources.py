"""Record fetchers for the building card, plus the transit trip planner."""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from .config import DS, SOCRATA, TRANSIT
from .net import FetchError, get_json

NY = ZoneInfo("America/New_York")
MODES = {"SUBWAY": "subway", "METRO": "subway", "BUS": "bus", "COACH": "bus", "REGIONAL_RAIL": "train",
         "REGIONAL_FAST_RAIL": "train", "RAIL": "train", "LONG_DISTANCE": "train", "HIGHSPEED_RAIL": "train",
         "FERRY": "ferry", "TRAM": "tram", "FUNICULAR": "tram", "AERIAL_LIFT": "tram"}


def since(days: int, fmt: str = "%Y-%m-%dT00:00:00") -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime(fmt)


def soql(ds: str, query: str, timeout: float = 35) -> list[dict[str, Any]]:
    return list(get_json(SOCRATA.format(ds=DS[ds]), {"$query": query}, timeout=timeout) or [])


def hpd_violations(bbl: str, days: int = 3 * 365) -> list[dict[str, Any]]:
    return soql("hpd_violations", (
        "SELECT violationid, class, novdescription, inspectiondate, violationstatus, currentstatus "
        f"WHERE bbl = '{bbl}' AND inspectiondate > '{since(days)}' ORDER BY inspectiondate DESC LIMIT 3000"))


def sr311(bbl: str, days: int = 3 * 365) -> list[dict[str, Any]]:
    return soql("311", (
        "SELECT unique_key, created_date, closed_date, complaint_type, descriptor, status, agency "
        f"WHERE bbl = '{bbl}' AND created_date > '{since(days)}' ORDER BY created_date DESC LIMIT 3000"))


def dob_violations(bin_value: str, days: int = 3 * 365) -> list[dict[str, Any]]:
    return soql("dob_violations", (
        "SELECT isn_dob_bis_viol, issue_date, violation_category, violation_type, description, disposition_comments "
        f"WHERE bin = '{bin_value}' AND issue_date > '{since(days, '%Y%m%d')}' ORDER BY issue_date DESC LIMIT 1000"))


def flood_2050(lat: float, lon: float) -> bool:
    rows = soql("flood_2050", f"SELECT count(*) AS n WHERE intersects(the_geom, 'POINT({lon} {lat})')")
    return bool(rows) and int(float(rows[0].get("n") or 0)) > 0


def next_weekday_830() -> dt.datetime:
    now = dt.datetime.now(NY)
    day = now.date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(8, 30), NY)


def transit(lat: float, lon: float, lat2: float, lon2: float) -> dict[str, Any]:
    """Weekday 8:30 am transit plans from Transitous. {"ok", "data": [{minutes, modes, transfers}], "error"}."""
    when = next_weekday_830().astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        out = get_json(TRANSIT, {"fromPlace": f"{lat},{lon}", "toPlace": f"{lat2},{lon2}", "time": when}, timeout=30)
    except FetchError as exc:
        return {"ok": False, "data": [], "error": str(exc)}
    plans = []
    for it in (out or {}).get("itineraries") or []:
        legs = it.get("legs") or []
        modes = [MODES.get(lg.get("mode") or "", (lg.get("mode") or "").lower().replace("_", " "))
                 for lg in legs if lg.get("mode") and lg.get("mode") != "WALK"] or ["walking"]
        plans.append({"minutes": round((it.get("duration") or 0) / 60), "transfers": int(it.get("transfers") or 0),
                      "modes": modes, "lines": [lg.get("routeShortName") for lg in legs if lg.get("routeShortName")],
                      "start": it.get("startTime")})
    return {"ok": bool(plans), "data": plans, "error": None if plans else "no itinerary"}
