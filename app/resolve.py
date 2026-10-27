"""Addresses to lots and lots to doors: NYC GeoSearch plus PLUTO."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .config import BOROUGHS, DS, GEOSEARCH, SOCRATA
from .net import FetchError, get_json


@dataclass
class Place:
    label: str
    bbl: str | None
    bin: str | None
    lat: float | None
    lon: float | None
    borough: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Resolved:
    """A BBL checked against PLUTO. ``match`` is the door GeoSearch knows for it, or None."""

    bbl: str
    lot: dict[str, Any]
    match: Place | None


def clean_bbl(value: Any) -> str | None:
    """'1-00835-0041', '1008350041.00000000' or 1008350041 -> '1008350041'. None if it is not a BBL."""
    s = str(value or "").strip()
    if re.fullmatch(r"\d{10}\.0*", s):
        s = s.split(".")[0]
    m = re.fullmatch(r"([1-5])[-\s/]?(\d{1,5})[-\s/]?(\d{1,4})", s)
    if not m:
        return None
    if len(s) == 10 and s.isdigit():
        return s
    if not re.search(r"[-\s/]", s):
        return None
    return m.group(1) + m.group(2).zfill(5) + m.group(3).zfill(4)


def _real_bin(b: Any) -> str | None:
    s = str(b or "").strip()
    # x000000 BINs are city placeholders for lots with no building number
    return s if re.fullmatch(r"[1-5]\d{6}", s) and s[1:] != "000000" else None


def _place(feature: dict[str, Any]) -> Place:
    p = feature.get("properties") or {}
    pad = (p.get("addendum") or {}).get("pad") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
    label = (p.get("label") or "").replace(", USA", "").replace(", New York, NY", ", Manhattan, NY")
    head, _, tail = label.partition(",")
    label = " ".join(w if w[:1].isdigit() else w.capitalize() for w in head.split()) + ("," + tail if tail else "")
    return Place(label=label, bbl=clean_bbl(pad.get("bbl")), bin=_real_bin(pad.get("bin")),
                 lat=coords[1], lon=coords[0], borough=p.get("borough"))


def geocode(text: str, size: int = 5) -> list[Place]:
    """Free text address -> up to ``size`` places, best first. Raises FetchError if GeoSearch is down."""
    text = (text or "").strip()
    if len(text) < 3:
        return []
    data = get_json(GEOSEARCH, {"text": text[:200], "size": max(1, min(size, 10))}, timeout=15)
    out: list[Place] = []
    seen: set[tuple[str | None, str]] = set()
    for f in (data or {}).get("features") or []:
        pl = _place(f)
        key = (pl.bbl, pl.label)
        if key not in seen:
            seen.add(key)
            out.append(pl)
    return out[:size]


def pluto_lot(bbl: str) -> dict[str, Any]:
    rows = get_json(SOCRATA.format(ds=DS["pluto"]), {"$query": (
        "SELECT bbl, address, borough, ownername, unitsres, unitstotal, yearbuilt, numfloors, bldgclass, "
        f"latitude, longitude, zipcode WHERE bbl = '{bbl}' LIMIT 1")}, timeout=25)
    return rows[0] if rows else {}


def resolve_bbl(bbl: str) -> Resolved:
    """BBL -> its PLUTO lot and the GeoSearch door with a real BIN, if the city has one."""
    b = clean_bbl(bbl) or ""
    try:
        lot = pluto_lot(b) if b else {}
    except FetchError:
        lot = {}
    match: Place | None = None
    if lot.get("address"):
        boro = BOROUGHS.get(b[0], "")
        try:
            hits = [h for h in geocode(f"{lot['address']} {boro}", size=5) if h.bbl == b]
        except FetchError:
            hits = []
        if hits:
            match = next((h for h in hits if h.bin), hits[0])
    if match is None and lot.get("latitude"):
        match = Place(label=str(lot.get("address") or ""), bbl=b, bin=None,
                      lat=float(lot["latitude"]), lon=float(lot["longitude"]), borough=BOROUGHS.get(b[0]))
    return Resolved(bbl=b, lot=lot, match=match)
