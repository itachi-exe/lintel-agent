"""The agent that explains why: one question about one building, answered from records.

Flow, every step visible in the reply:

1. Scout Intent decides what the question is (intent, confidence, guard, route)
   and signs an execution contract. Nothing runs without one.
2. The executor runs only the tools in that contract, each as one SQL statement
   through Zetaris (or straight to NYC Open Data when no Zetaris instance is up).
3. The answer is written from those rows only: by Meterless Nightshift when it
   is reachable, else by a fallback model, else by rule text. Each evidence row
   links to the record it came from.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable

from . import llm, zetaris
from .config import LINK
from .plain import dob_line, plain_line, sr311_line
from .resolve import clean_bbl, geocode, resolve_bbl
from .scout import ContractRefused, authorize_tool, get_scout
from .sources import transit

KEYWORDS = {
    "building.heat": ["HEAT", "HOT WATER"],
    "building.pests": ["RODENT", "MICE", "ROACH", "VERMIN", "BEDBUG", "BED BUG", "PEST", "RATS"],
    "building.mold_leaks": ["MOLD", "LEAK", "WATER DAMAGE", "DAMP"],
    "building.lead": ["LEAD"],
}
TOPIC = {
    "building.heat": "heat or hot water",
    "building.pests": "pest",
    "building.mold_leaks": "mold or leak",
    "building.lead": "lead paint",
}

# Capabilities whose datasets are not publicly readable any more. Scout may plan
# them; the executor says plainly that they were skipped.
UNREADABLE = {
    "records.hpd_complaints": "HPD complaints now need a city login, skipped",
    "records.hpd_registration": "HPD registration is not public over the API, skipped",
}


def _since(months: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=months * 31)).strftime("%Y-%m-%dT00:00:00")


def _date(v: Any) -> str:
    s = str(v or "")[:10]
    try:
        return dt.date.fromisoformat(s).strftime("%b %-d %Y")
    except ValueError:
        return s


def _q(v: str) -> str:
    return v.replace("'", "''")


def _like(cols: list[str], kws: list[str]) -> str:
    return "(" + " OR ".join(f"upper({c}) LIKE '%{k}%'" for c in cols for k in kws) + ")"


class Run:
    """What one answer read: the facts, the evidence rows and every statement."""

    def __init__(self, bbl: str, contract: dict[str, Any]) -> None:
        self.bbl = bbl
        self.contract = contract
        self.facts: list[str] = []
        self.evidence: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []
        self.steps: list[str] = []
        self.via: set[str] = set()
        self._lot: dict[str, Any] | None = None
        self._match: Any = None

    def tool(self, capability: str) -> str:
        """The tool the contract granted for this capability. Raises if none."""
        for step in self.contract.get("tool_plan") or []:
            if step.get("capability") == capability:
                authorize_tool(self.contract, step["tool"], capability)
                return step["tool"]
        raise ContractRefused(f"{capability} is not in the contract")

    def sql(self, capability: str, statement: str) -> list[dict[str, Any]]:
        tool = self.tool(capability)
        q = zetaris.run(statement, use_zetaris=(tool == "zetaris.sql"))
        self.queries.append(q.as_dict())
        self.via.add(q.via)
        if not q.ok:
            self.steps.append(f"Could not read {q.table} ({q.error})")
        return q.rows

    def lot(self) -> dict[str, Any]:
        if self._lot is None:
            rows = self.sql("records.pluto", (
                "SELECT address, ownername, unitsres, unitstotal, yearbuilt, numfloors, bldgclass, "
                f"latitude, longitude FROM nyc.pluto WHERE bbl = '{self.bbl}' LIMIT 1"))
            self._lot = rows[0] if rows else {}
        return self._lot

    def where(self) -> tuple[float | None, float | None, str | None]:
        """lat, lon, bin. PLUTO carries the ground point, the city geocoder the door."""
        if self._match is None:
            self._match = resolve_bbl(self.bbl).match
        m = self._match
        return (m.lat, m.lon, m.bin) if m else (None, None, None)

    def ev(self, label: str, source: str, record: str, url: str, date: str = "", detail: str = "") -> None:
        if len(self.evidence) < 10:
            self.evidence.append({"label": label, "detail": detail[:240], "source": source,
                                  "record": record, "url": url, "date": date})


# ------------------------------------------------------------ the tools


def hpd(run: Run, intent: str) -> None:
    kws = KEYWORDS.get(intent)
    where = f"bbl = '{run.bbl}' AND inspectiondate > '{_since(36)}'"
    if kws:
        where += " AND " + _like(["novdescription"], kws)
    rows = run.sql("records.hpd_violations", (
        "SELECT violationid, class, novdescription, inspectiondate, violationstatus "
        f"FROM nyc.hpd_violations WHERE {where} ORDER BY inspectiondate DESC LIMIT 300"))
    open_rows = [r for r in rows if (r.get("violationstatus") or "").lower() == "open"]
    worst = sum(1 for r in open_rows if r.get("class") == "C")
    topic = TOPIC.get(intent, "")
    what = f"{topic} violations" if topic else "housing violations"
    if not rows:
        run.facts.append(f"HPD inspectors wrote no {what} here in the last 3 years.")
    else:
        fact = f"HPD inspectors wrote {len(rows)} {what} here in the last 3 years, {len(open_rows)} still open"
        if worst:
            fact += f" ({worst} class C, immediately hazardous)"
        run.facts.append(fact + f". The latest was {_date(rows[0].get('inspectiondate'))}.")
    for r in sorted(rows, key=lambda r: (r.get("violationstatus") != "Open", r.get("class") != "C"))[:5]:
        line, _ = plain_line(r.get("novdescription") or "")
        state = "open" if (r.get("violationstatus") or "").lower() == "open" else "closed"
        run.ev(f"{line} Class {r.get('class')}, {state}.", "HPD violation", str(r.get("violationid")),
               LINK["hpd_violation"].format(id=r.get("violationid")), _date(r.get("inspectiondate")),
               re.sub(r"\s+", " ", r.get("novdescription") or ""))


def sr311(run: Run, intent: str) -> None:
    kws = KEYWORDS.get(intent)
    where = f"bbl = '{run.bbl}' AND created_date > '{_since(12)}'"
    if kws:
        where += " AND " + _like(["complaint_type", "descriptor"], kws + (["UNSANITARY"] if intent == "building.pests" else []))
    rows = run.sql("records.sr311", (
        "SELECT unique_key, created_date, complaint_type, descriptor, status "
        f"FROM nyc.sr311 WHERE {where} ORDER BY created_date DESC LIMIT 300"))
    if intent == "building.pests":
        rows = [r for r in rows if any(k in f"{r.get('complaint_type')} {r.get('descriptor')}".upper()
                                       for k in KEYWORDS[intent])]
    topic = TOPIC.get(intent, "")
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r.get("complaint_type") or "Other"] = kinds.get(r.get("complaint_type") or "Other", 0) + 1
    top = ", ".join(f"{re.sub(r'[ /]*-[ ]*', ' ', k).lower()} ({v})" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:3])
    what = f"{topic} complaints" if topic else "complaints"
    if not rows:
        run.facts.append(f"Nobody called 311 with {what} about this building in the last 12 months.")
    elif topic:
        run.facts.append(f"311 logged {len(rows)} {what} here in the last 12 months, "
                         f"the latest on {_date(rows[0].get('created_date'))}.")
    else:
        run.facts.append(f"311 logged {len(rows)} {what} here in the last 12 months, mostly {top}.")
    for r in rows[:3]:
        run.ev(sr311_line(r.get("complaint_type") or ""), "311 request", str(r.get("unique_key")),
               LINK["sr311"].format(id=r.get("unique_key")), _date(r.get("created_date")),
               f"{r.get('descriptor') or ''}, {(r.get('status') or '').lower()}".strip(", "))


def dob(run: Run, intent: str) -> None:
    _, _, bin_value = run.where()
    if not bin_value:
        run.steps.append("No building number (BIN) on record, DOB skipped")
        return
    rows = run.sql("records.dob", (
        "SELECT isn_dob_bis_viol, issue_date, violation_category, violation_type, description "
        f"FROM nyc.dob_violations WHERE bin = '{_q(str(bin_value))}' ORDER BY issue_date DESC LIMIT 100"))
    active = [r for r in rows if "ACTIVE" in (r.get("violation_category") or "").upper()]
    run.facts.append(f"The Buildings Department has {len(rows)} violations on file for it, {len(active)} still active.")
    for r in (active or rows)[:3]:
        run.ev(dob_line(r.get("violation_category") or "", r.get("violation_type") or ""),
               "DOB violation", str(r.get("isn_dob_bis_viol")),
               LINK["dob_violation"].format(id=r.get("isn_dob_bis_viol")), _date(r.get("issue_date")),
               r.get("description") or "")


def facts_tool(run: Run, intent: str) -> None:
    lot = run.lot()
    if not lot:
        run.facts.append("No lot record on PLUTO for this BBL.")
        return
    owner = (lot.get("ownername") or "").strip().rstrip(" .")
    floors = int(float(lot.get("numfloors") or 0)) or "?"
    def add(f: str) -> None:
        if f not in run.facts:
            run.facts.append(f)
    if intent in ("building.owner", "owner.portfolio", "building.verdict"):
        add(f"The owner of record is {owner}." if owner else "No owner is listed on the lot record.")
    if intent in ("building.facts", "building.verdict", "building.lead"):
        add(f"It was built in {lot.get('yearbuilt') or 'an unknown year'}, {floors} floors, "
            f"{lot.get('unitsres') or 0} homes.")
    if any(e["source"] == "PLUTO lot record" for e in run.evidence):
        return
    run.ev(f"{lot.get('address')}: owner {owner or 'not listed'}, built {lot.get('yearbuilt')}, "
           f"{lot.get('unitsres')} homes.", "PLUTO lot record", run.bbl, LINK["pluto"].format(bbl=run.bbl))


def portfolio_tool(run: Run, intent: str) -> None:
    raw = run.lot().get("ownername") or ""
    owner = raw.strip().rstrip(" .")
    if len(owner) < 3:
        run.facts.append("No owner name on record, so no portfolio to look up.")
        return
    rows = run.sql("records.portfolio", (
        "SELECT bbl, address, unitsres, yearbuilt FROM nyc.pluto "
        f"WHERE ownername LIKE '{_q(owner)}%' AND unitsres > 0 ORDER BY unitsres DESC LIMIT 60"))
    others = [r for r in rows if not str(r.get("bbl") or "").startswith(run.bbl)]
    homes = sum(int(float(r.get("unitsres") or 0)) for r in others)
    run.facts.append(f"The same owner name holds {len(others)} other residential lots in the city ({homes} homes)."
                     if others else "The same owner name holds no other residential lots in the city.")
    for r in others[:4]:
        b = str(r.get("bbl") or "")[:10]
        run.ev(f"{r.get('address')}, {r.get('unitsres')} homes.", "PLUTO, same owner", b, LINK["pluto"].format(bbl=b))


def flood_tool(run: Run, intent: str) -> None:
    lat, lon, _ = run.where()
    if lat is None:
        run.facts.append("No map point for this building, flood map skipped.")
        return
    rows = run.sql("geo.flood", (
        f"SELECT count(*) AS n FROM nyc.flood_2050 WHERE intersects(the_geom, 'POINT({lon} {lat})')"))
    inside = bool(rows) and int(float(rows[0].get("n") or 0)) > 0
    run.facts.append("The building sits " + ("inside" if inside else "outside") + " the city's 2050s floodplain map.")
    run.ev("Inside the 2050s floodplain." if inside else "Outside the 2050s floodplain.",
           "NYC Future Floodplain 2050s", "27ya-gqtm", LINK["flood"])


def noise_tool(run: Run, intent: str) -> None:
    lat, lon, _ = run.where()
    if lat is None:
        run.facts.append("No map point for this building, noise skipped.")
        return
    rows = run.sql("geo.noise", (
        "SELECT count(*) AS n FROM nyc.sr311 WHERE complaint_type = 'Noise - Residential' "
        f"AND created_date > '{_since(12)}' AND within_circle(location, {lat}, {lon}, 200)"))
    n = int(float(rows[0].get("n") or 0)) if rows else 0
    run.facts.append(f"311 logged {n} residential noise complaints within 200 m in the last 12 months.")
    run.ev(f"{n} residential noise calls within 200 m in a year.", "311 requests", "erm2-nwe9",
           "https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9")


def commute_tool(run: Run, intent: str, workplace: str | None) -> None:
    lat, lon, _ = run.where()
    if lat is None or not workplace:
        run.facts.append("Commute needs the building point and a place to go.")
        return
    run.tool("geo.geocode")
    hits = geocode(workplace, size=1)
    run.queries.append({"sql": f"geosearch: {workplace}", "table": "nyc.geosearch", "via": "nyc.geosearch",
                        "rows": len(hits), "ok": bool(hits), "error": None})
    if not hits or hits[0].lat is None:
        run.facts.append(f"Could not place '{workplace}' on the city map.")
        return
    dest = hits[0]
    tool = run.tool("geo.commute")
    plan = transit(lat, lon, dest.lat, dest.lon)
    run.queries.append({"sql": f"{tool}: weekday 8:30 am, to {dest.label}", "table": tool, "via": tool,
                        "rows": len(plan.get("data") or []), "ok": plan.get("ok"), "error": None})
    if not plan.get("ok"):
        run.facts.append(f"No transit plan came back for {dest.label}.")
        return
    best = min(plan["data"], key=lambda p: p["minutes"])
    modes = list(dict.fromkeys(m.lower() for m in best["modes"]))
    run.facts.append(f"On a weekday morning {workplace.strip().title()} is {best['minutes']} minutes away by "
                     f"{' and '.join(modes)}, {best['transfers']} transfers.")
    run.ev(f"{best['minutes']} min to {dest.label}, {best['transfers']} transfers.", "Transitous trip plan",
           "plan", LINK["transit"])


# intent -> the tools it runs, in order
PLAN: dict[str, list[Callable[..., None]]] = {
    "building.violations": [hpd],
    "building.heat": [hpd, sr311],
    "building.pests": [hpd, sr311],
    "building.mold_leaks": [hpd],
    "building.lead": [hpd, facts_tool],
    "building.owner": [facts_tool],
    "owner.portfolio": [facts_tool, portfolio_tool],
    "building.complaints": [sr311],
    "building.structure": [dob],
    "building.facts": [facts_tool],
    "area.flood": [flood_tool],
    "area.noise": [noise_tool],
    "building.verdict": [facts_tool, hpd, sr311, dob],
}

SYSTEM = (
    "You answer a renter's question about one New York City building. Use ONLY the facts given. "
    "Two or three short sentences, plain words, numbers as digits. Say what the records show, "
    "not what to do, no advice, no guesses, no new facts. If the facts are empty, say the records "
    "show nothing on it. Never use dashes as punctuation. Reply with the answer only."
)


def _clean(text: str) -> str:
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip()[:700]


def ask(bbl: str, question: str, *, workplace: str | None = None, intent: str | None = None,
        surface: str = "card", rate_key: str = "") -> dict[str, Any]:
    bbl = clean_bbl(bbl) or ""
    question = (question or "").strip()[:500]
    if workplace and "commute" not in question.lower() and intent == "area.commute":
        question = f"{question} commute to {workplace}"
    avail = {"zetaris.sql": zetaris.configured(), "socrata.direct": True}
    decision = get_scout().decide(question, surface=surface, role="renter",
                                  context={"bbl": bbl, "rate_key": rate_key} if bbl else {"rate_key": rate_key},
                                  availability=avail, forced_intent=intent)
    why = {"intent": decision.get("intent"), "intent_label": decision.get("intent_label"),
           "confidence": decision.get("confidence"), "band": decision.get("band"),
           "risk": (decision.get("risk") or {}).get("level"), "steps": list(decision.get("steps") or []),
           "contract": None}

    if decision["status"] == "refuse":
        return {"status": "refused", "message": decision.get("message") or "I can only answer about this building.",
                "why": why}
    if decision["status"] == "clarify":
        c = decision.get("clarification") or {}
        return {"status": "clarify", "question": c.get("question") or "Which one did you mean?",
                "options": c.get("options") or [], "why": why}

    contract = decision["contract"]
    why["contract"] = {k: contract.get(k) for k in ("id", "registry_version", "tool_plan", "signature",
                                                     "issued_at", "expires_at")}
    run = Run(bbl, contract)
    intents = contract.get("intents") or [contract["intent"]]
    for it in intents:
        if it == "area.commute":
            commute_tool(run, it, (contract.get("params") or {}).get("workplace") or workplace)
        for fn in PLAN.get(it, []):
            try:
                fn(run, it)
            except ContractRefused as exc:
                run.steps.append(f"Executor refused: {exc}")
    for cap, note in UNREADABLE.items():
        if any(s.get("capability") == cap for s in contract.get("tool_plan") or []):
            run.steps.append(note)

    via = "zetaris" if "zetaris" in run.via else "direct"
    rows = sum(int(q.get("rows") or 0) for q in run.queries)
    n_rec = sum(1 for q in run.queries if q["table"] in zetaris.TABLES)
    n_api = len(run.queries) - n_rec
    ran = f"{n_rec} {'query' if n_rec == 1 else 'queries'} via {'Zetaris' if via == 'zetaris' else 'NYC Open Data'}"
    if n_api:
        ran = (ran + " and " if n_rec else "") + f"{n_api} {'lookup' if n_api == 1 else 'lookups'} on city and transit maps"
    run.steps.insert(0, f"Ran {ran}, {rows} rows back")

    facts = "\n".join(f"- {f}" for f in run.facts) or "- nothing"
    text, model = llm.INSTANCE.compose(SYSTEM, f"Question: {question}\nBuilding BBL {bbl}.\nFacts:\n{facts}")
    answer = _clean(text) if text else " ".join(run.facts) or "The records show nothing on this."
    run.steps.append({"meterless": "Wrote the answer with Meterless Nightshift (meterless/auto) from these rows only",
                      "fallback": f"Wrote the answer with the fallback model {model.get('model')} from these rows only",
                      "none": "No model reachable, the answer is the facts as read"}[model["provider"]])
    why["steps"] += run.steps
    why["facts"] = run.facts
    return {"status": "ok", "answer": answer, "evidence": run.evidence, "why": why,
            "data": {"via": via, "sql": "\n\n".join(q["sql"] + ";" for q in run.queries if q["table"] in zetaris.TABLES),
                     "rows": rows, "queries": run.queries},
            "model": model}
