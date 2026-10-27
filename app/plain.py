"""Bureaucratic record text to short plain English.

HPD writes violations as code citations ("§ 27-2017.4 ABATE THE INFESTATION
CONSISTING OF MICE ..."). A renter needs "Mice in the apartment." The rules below
are checked in order; the first match wins. Unknown text falls back to a cleaned,
shortened copy of the original so nothing is invented.
"""

from __future__ import annotations

import re

# (pattern over the upper-cased text, plain sentence). Order matters: specific first.
HPD_RULES: list[tuple[str, str]] = [
    (r"ACCESS TO .*HEATING SYSTEM|BOILER ROOM", "Heating system or boiler room locked to inspectors."),
    (r"HEAT\b.*\b(SUPPLY|PROVIDE|ADEQUATE)|SUPPLY OF HEAT|INADEQUATE HEAT|NO HEAT", "Not enough heat."),
    (r"HOT WATER", "Not enough hot water."),
    (r"COLD WATER", "No proper cold water at a fixture."),
    (r"\bLEAD\b.*PAINT|LEAD-BASED|LEAD BASED|PAINT.*\bLEAD\b", "Lead paint hazard."),
    (r"\bMICE\b", "Mice in the apartment."),
    (r"\bRATS?\b|RODENT", "Rats or rodents."),
    (r"ROACH", "Roaches."),
    (r"BED ?BUG", "Bedbugs."),
    (r"INFESTATION|VERMIN|PEST", "Pests."),
    (r"\bMOLD\b", "Mold."),
    (r"LEAK", "A leak."),
    (r"WATER DAMAGE|DAMP", "Water damage."),
    (r"CARBON MONOXIDE", "Carbon monoxide detector missing or broken."),
    (r"SMOKE DETECT", "Smoke detector missing or broken."),
    (r"SELF-CLOSING|SELF CLOSING|FIRE ?DOOR", "A fire door does not close on its own."),
    (r"FIRE RETARD|FIRE ESCAPE", "Fire safety repair needed."),
    (r"GAS\b", "Gas problem."),
    (r"ELECTRIC|OUTLET|LIGHT SWITCH|WIRING|FIXTURE", "Electrical repair needed."),
    (r"INTERCOM|BUZZER", "Broken intercom or buzzer."),
    (r"WINDOW GUARD", "Window guards missing or broken."),
    (r"WINDOW|SASH", "Broken window."),
    (r"\bLOCK\b|ENTRANCE DOOR", "Broken lock or entrance door."),
    (r"DOOR", "Broken door."),
    (r"PLASTER|PAINT", "Cracked plaster or peeling paint."),
    (r"FLOOR", "Damaged floor."),
    (r"FAUCET|TOILET|BATHTUB|SINK|WASH ?BASIN|PLUMBING|DRAIN", "Plumbing repair needed."),
    (r"REFRIGERATOR|STOVE|RANGE", "Broken kitchen appliance."),
    (r"ELEVATOR", "Elevator problem."),
    (r"GARBAGE|REFUSE|COMPACTOR|UNSANITARY|FILTH", "Garbage or dirty common areas."),
    (r"REGISTRATION|REGISTER", "The owner did not register the building with HPD."),
    (r"POST|SIGN|NOTICE", "A required notice is not posted."),
]

_CITE = re.compile(r"(?:HMC|ADM CODE|MDL|RCNY|[§\d\-\.,;:()\s])+?(?=[A-Z]{3,})")
_WHERE = re.compile(r"\bAPT\.?\s*([0-9A-Z\-]+)", re.I)
_STORY = re.compile(r"\b(\d+)(?:st|nd|rd|th)\s+STORY", re.I)


def _strip_citation(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"^(?:[§\d\-\.,;:()\s]|HMC|ADM|CODE|MDL|RCNY|AND)+", "", t, flags=re.I).strip(" :;,")
    return t


def where(text: str) -> str:
    """'... LOCATED AT APT 2D, 2nd STORY ...' -> 'Apt 2D'. Public hall -> 'Public hall'."""
    m = _WHERE.search(text or "")
    if m:
        return f"Apt {m.group(1).upper()}"
    if re.search(r"PUBLIC HALL|STAIR|LOBBY|CELLAR|ROOF|COMPACTOR", text or "", re.I):
        s = _STORY.search(text or "")
        return "Common area" + (f", floor {s.group(1)}" if s else "")
    return ""


def plain_line(text: str) -> tuple[str, str]:
    """HPD violation text -> (plain sentence, where in the building or '')."""
    up = (text or "").upper()
    spot = where(text)
    for pat, line in HPD_RULES:
        if re.search(pat, up):
            return line, spot
    raw = _strip_citation(text)
    if not raw:
        return "Housing code violation.", spot
    raw = re.split(r"\bLOCATED AT\b|\bAT APT\b", raw, flags=re.I)[0].strip(" ,.")
    raw = raw[:90].rsplit(" ", 1)[0] if len(raw) > 90 else raw
    return raw.capitalize().rstrip(".") + ".", spot


SR311 = {
    "HEAT/HOT WATER": "No heat or hot water.",
    "HEATING": "No heat.",
    "UNSANITARY CONDITION": "Dirty conditions or pests.",
    "PLUMBING": "Plumbing problem.",
    "WATER LEAK": "A leak.",
    "PAINT/PLASTER": "Peeling paint or cracked plaster.",
    "PAINT - PLASTER": "Peeling paint or cracked plaster.",
    "DOOR/WINDOW": "Broken door or window.",
    "ELECTRIC": "Electrical problem.",
    "ELEVATOR": "Elevator problem.",
    "GENERAL": "General building complaint.",
    "GENERAL CONSTRUCTION": "General building complaint.",
    "FLOORING/STAIRS": "Damaged floor or stairs.",
    "APPLIANCE": "Broken appliance.",
    "SAFETY": "Safety problem.",
    "NOISE - RESIDENTIAL": "Noise from neighbors.",
    "NOISE - STREET/SIDEWALK": "Street noise.",
    "NOISE": "Noise.",
    "NOISE - COMMERCIAL": "Noise from a business.",
    "NOISE - VEHICLE": "Vehicle noise.",
    "ILLEGAL PARKING": "Illegal parking.",
    "BLOCKED DRIVEWAY": "Blocked driveway.",
    "RODENT": "Rats or mice.",
    "DIRTY CONDITION": "Dirty sidewalk or lot.",
    "MISSED COLLECTION": "Missed trash pickup.",
    "WATER SYSTEM": "Water supply problem.",
    "SEWER": "Sewer problem.",
    "STREET CONDITION": "Street needs repair.",
    "ILLEGAL FIREWORKS": "Fireworks.",
    "NON-EMERGENCY POLICE MATTER": "Police matter, not an emergency.",
    "OUTSIDE BUILDING": "Problem outside the building.",
    "MOLD": "Mold.",
    "LEAD": "Lead paint concern.",
    "HPD LITERATURE REQUEST": "Asked HPD for information.",
}


def sr311_line(complaint_type: str) -> str:
    """311 complaint type -> plain sentence."""
    key = re.sub(r"\s+", " ", (complaint_type or "").strip().upper())
    if key in SR311:
        return SR311[key]
    for k, v in SR311.items():
        if key.startswith(k):
            return v
    if not key:
        return "311 complaint."
    return re.sub(r"\s*[/\-]\s*", " ", key).capitalize() + "."


DOB_TYPES: list[tuple[str, str]] = [
    ("UB-", "Unsafe building."),
    ("IMEGNCY", "Immediate emergency order."),
    ("FISP", "Facade inspection report late or missing."),
    ("FACADE", "Facade problem."),
    ("LL6291", "Boiler inspection not filed."),
    ("BOILER", "Boiler inspection not filed or failed."),
    ("LL1081", "Elevator inspection not filed."),
    ("EVCAT", "Elevator inspection not filed."),
    ("ELEVATOR", "Elevator violation."),
    ("ACC1", "Elevator repair not certified."),
    ("AEUHAZ", "Hazard fix not certified."),
    ("C-CONSTRUCTION", "Construction or upkeep violation."),
    ("P-PLUMBING", "Plumbing violation."),
    ("BENCH", "Energy use not reported."),
    ("EGRADE", "Energy grade not posted."),
    ("EARCX", "Energy audit not filed."),
    ("LANDMK", "Landmark rules violation."),
    ("ES-", "Sign violation."),
    ("Z-", "Zoning violation."),
    ("HVAC", "Heating or ventilation violation."),
]


def dob_line(category: str, vtype: str) -> str:
    """DOB violation category + type -> plain sentence, with 'still active' when it is."""
    t = (vtype or "").upper().strip()
    line = next((v for k, v in DOB_TYPES if t.startswith(k) or k in t[:20]), "")
    if not line:
        name = re.split(r"\s{2,}", t)[0]
        name = name.split("-", 1)[1] if "-" in name else name
        line = (name.strip().capitalize() or "Building code") + " violation."
    c = (category or "").upper()
    if "ACTIVE" in c:
        line = line.rstrip(".") + ", still active."
    elif "DISMISS" in c or "RESOLVE" in c:
        line = line.rstrip(".") + ", resolved."
    return line
