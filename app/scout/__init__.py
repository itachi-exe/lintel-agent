"""Scout: Meterless Scout Intent for Lintel.

Every renter question passes Sense, Interpret, Guard, Route, Recommend and leaves
as one of three decisions: act (with a signed execution contract), clarify, or refuse.
See README.md for what is implemented from the spec and what is our own choice.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

from . import guard
from .contract import EPHEMERAL, ContractRefused, authorize_tool, issue, verify_contract
from .route import Graph, RouteError
from .sense import Sense, band

__all__ = ["Scout", "get_scout", "verify_contract", "authorize_tool", "ContractRefused"]

HERE = Path(__file__).parent
POLICY_PACK = "lintel@1"
TIE_WINDOW = 0.05
HIGH_RISK_PROCEED = 0.95
DEFAULT_OPTIONS = ["building.verdict", "building.violations", "building.heat", "building.owner"]
PERMISSIONS = {"renter": set(), "operator": {"operator"}}
PROBES = ["will I have heat this winter", "who owns this building and what else do they own",
          "are there rats, roaches or bedbugs, and is there mold or leaks in the bathroom ceiling " * 3]
WORKPLACE = re.compile(r"\b(?:commute|commuting|get|getting|go|going|travel|ride|trip|work|working|office|job|far|long)\b"
                       r"[^?.!]*?\b(?:to|at|in|near|from)\s+(?P<place>[^?.!,;]+)", re.I)
TRAILING = re.compile(r"\s+(?:by|on|via|in the|every|each|during|at rush|from here|from this|from the building|"
                      r"from there|from my|taking|using)\b.*$", re.I)
NOT_A_PLACE = re.compile(r"^(?:my |the |a )?(?:work|office|job|there|here|get there|commute|it|downtown)$", re.I)


def bind_workplace(prompt: str) -> str | None:
    """'commute to Union Square by train' -> 'Union Square'."""
    for m in WORKPLACE.finditer(prompt):
        place = TRAILING.sub("", m.group("place")).strip(" '\"")
        place = re.sub(r"^(?:my )?(?:work|office|job) (?:at|in|near) ", "", place, flags=re.I)
        if len(place) >= 2 and not NOT_A_PLACE.match(place):
            return place
    return None


class Scout:
    def __init__(self, registry: str | Path = HERE / "registry.json", capabilities: str | Path = HERE / "capabilities.json",
                 *, rate_limit: int = 30, rate_window_s: float = 60.0) -> None:
        reg = json.loads(Path(registry).read_text())
        self.registry_version: int = reg["registryVersion"]
        self.intents: dict[str, dict[str, Any]] = {i["id"]: i for i in reg["intents"]}
        for iid, it in self.intents.items():
            if not it.get("examples"):
                raise ValueError(f"intent {iid} has no examples (spec 3: examples are mandatory)")
            for cap in it["capabilities"]:
                if cap not in json.loads(Path(capabilities).read_text())["capabilities"]:
                    raise ValueError(f"intent {iid} needs unknown capability {cap}")
        self.graph = Graph(json.loads(Path(capabilities).read_text()))
        self.sense = Sense(list(self.intents.values()))
        self.limiter = guard.RateLimiter(rate_limit, rate_window_s)
        self.scan_ms = self.sense.self_test(PROBES)  # raises if the full-registry scan is over 16 ms

    def label(self, iid: str) -> str:
        return self.intents[iid].get("label") or iid

    def classify(self, prompt: str) -> list[dict[str, Any]]:
        """Sense only: top-k candidates {intent, score, span}."""
        text, _ = guard.clean(prompt)
        return [{k: c[k] for k in ("intent", "score", "span")} for c in self.sense.classify(text)]

    # ------------------------------------------------------------------ decide
    def decide(self, prompt: str, *, surface: str = "card", role: str = "renter", context: dict[str, Any] | None = None,
               availability: dict[str, bool] | None = None, forced_intent: str | None = None) -> dict[str, Any]:
        ctx, avail = dict(context or {}), dict(availability or {})
        text, flags = guard.clean(prompt or "")
        trace = "tr_" + secrets.token_hex(8)
        out: dict[str, Any] = {"status": "refuse", "trace_id": trace, "intent": "GENERAL", "intent_label": "General",
                               "confidence": 0.0, "band": "reject", "candidates": [], "plan": [], "params": {},
                               "risk": {}, "clarification": None, "contract": None, "message": None,
                               "redacted_prompt": None, "steps": []}
        steps: list[str] = out["steps"]
        layers: dict[str, tuple[str, str]] = {}

        def finish(status: str, floor: str = "low", message: str | None = None) -> dict[str, Any]:
            out["status"], out["message"] = status, message
            out["risk"] = guard.assess(text, floor=floor, layers=layers, flags=flags, redactions=redactions)
            if out["risk"]["level"] == "block" and status != "refuse":
                out["status"], out["clarification"], out["contract"] = "refuse", None, None
            return out

        # Guard layer 5 first: a flood of requests should not cost a classification each.
        redactions: list[dict[str, Any]] = []
        if not self.limiter.allow(f"{ctx.get('client', role)}:{surface}"):
            layers["rate"] = ("block", "rate limit")
            flags.append("rate:limited")
            steps.append("Guard: too many questions in a minute, refused")
            return finish("refuse", message="Too many questions at once. Wait a minute and ask again.")
        layers["rate"] = ("pass", "")

        # Guard layer 1 runs on the raw text, before anything reads it as a question.
        inj = guard.injection(text)
        if inj["detected"]:
            layers["injection"] = ("block", f"prompt injection ({inj['kind']})")
            flags += [f"injection:{inj['kind']}"] + [f"injection:{e}" for e in inj["evidence"]]
            out["risk_detail"] = {"injection": inj}
            steps.append("Guard: this reads like an instruction to the system, not a question. Refused")
            return finish("refuse", message="I only answer questions about this building's public records. "
                                            "Try: what violations are open here?")
        layers["injection"] = ("pass", "")
        text_r, redactions = guard.redact(text)
        out["redacted_prompt"] = text_r
        if redactions:
            layers["pii"] = ("warn", "personal data redacted")
            flags += [f"pii:redacted:{r['kind']}" for r in redactions]
            steps.append("Guard: removed personal contact details from the question")

        # Sense.
        recent = [i for i in ctx.get("recent_intents", []) if i in self.intents] or None
        cands = self.sense.classify(text_r, recent=recent)
        out["candidates"] = [{k: c[k] for k in ("intent", "score", "span")} for c in cands]
        top = cands[0]

        # Guard layers 3 and 4 that do not depend on the intent.
        if guard.OFF_TOPIC.search(text_r):
            layers["scope"] = ("block", "outside Lintel's scope")
            flags.append("scope:off-topic")
            steps.append("Guard: not a question about this building, refused")
            return finish("refuse", message="I can only answer about this building and its block. "
                                            "Try: who owns this building?")
        if guard.SENSITIVE.search(text_r):
            layers["pii"] = ("block", "asks about private people")
            flags.append("pii:private-person")
            steps.append("Guard: asks about private people, refused")
            return finish("refuse", message="I don't look up tenants or other private people, only the building and "
                                            "its owner of record. Try: who owns this building?")
        layers["scope"] = ("pass", "")

        if forced_intent:
            if forced_intent not in self.intents or forced_intent == "GENERAL":
                layers["policy"] = ("block", "unknown intent")
                return finish("refuse", message="That option is not one I can answer.")
            iid, conf = forced_intent, 1.0
            steps.append(f"You chose: {self.label(iid).lower()}")
            plan_c: list[dict[str, Any]] = []
        else:
            iid, conf = top["intent"], top["score"]
            plan_c = [] if band(conf) in ("low", "reject") else self.sense.multi(text_r)
            if plan_c and iid not in [p["intent"] for p in plan_c]:
                iid, conf = plan_c[0]["intent"], plan_c[0]["score"]
        b = band(conf)
        out.update(intent=iid, intent_label=self.label(iid), confidence=round(conf, 3), band=b,
                   plan=[p for p in plan_c if p["intent"] != iid])
        if not forced_intent:
            also = "".join(f", plus {self.label(p['intent']).lower()} ({p['score']:.2f})" for p in out["plan"])
            steps.append(f"Understood: {self.label(iid).lower()} ({conf:.2f}, {b}){also}")

        # Reject band: the safe GENERAL intent, never a guess.
        if b == "reject":
            out.update(intent="GENERAL", intent_label="General")
            return self._clarify(out, finish, "I answer questions about this building's records. Which one?",
                                 [c["intent"] for c in cands if c["_lex"]][:2] + DEFAULT_OPTIONS, "general")

        intent = self.intents[iid]
        second = cands[1] if cands[1]["intent"] != iid else cands[0]
        if not forced_intent and not plan_c:
            if b == "low":
                return self._clarify(out, finish, "Did you mean one of these?", [c["intent"] for c in cands[:3]], "low-band")
            if b == "medium" and conf - second["score"] <= TIE_WINDOW and second["score"] >= 0.55:
                return self._clarify(out, finish, f"Did you mean {self.label(iid).lower()} or "
                                                  f"{self.label(second['intent']).lower()}?",
                                     [iid, second["intent"]], "tie")
            if intent["riskClass"] == "high" and conf < HIGH_RISK_PROCEED:
                return self._clarify(out, finish, f"Just to check: {self.label(iid).lower()}?", [iid], "high-risk")

        # Interpret: references and parameters.
        params: dict[str, Any] = {}
        if "workplace" in intent["parameters"]["properties"]:
            wp = bind_workplace(text_r) or (ctx.get("workplace") or "").strip() or None
            if wp:
                params["workplace"] = wp[:120]
        if "status" in intent["parameters"]["properties"]:
            params["status"] = "open" if re.search(r"\b(open|unresolved|still|outstanding|current)\b", text_r, re.I) else "all"
        out["params"] = params
        bbl = str(ctx.get("bbl") or "").strip()
        if not re.fullmatch(r"[1-5]\d{9}", bbl):
            steps.append("Interpret: no building selected yet")
            out["clarification"] = {"question": "Which building? Search an address first.", "options": [],
                                    "missing": "bbl", "trigger": "params"}
            return finish("clarify", intent["riskClass"])
        missing = [p for p in intent["parameters"].get("required", []) if p not in params]
        if missing:
            steps.append(f"Interpret: need {missing[0]} to answer")
            out["clarification"] = {"question": "Where do you commute to? Name a place, like Union Square.",
                                    "options": [], "missing": missing[0], "trigger": "params"}
            return finish("clarify", intent["riskClass"])
        if params.get("workplace"):
            steps.append(f"Interpret: workplace is {params['workplace']}")

        # Guard layer 2: registry roles and surfaces are the policy floor.
        plan_ids = [iid] + [p["intent"] for p in out["plan"]]
        for pid in plan_ids:
            it = self.intents[pid]
            if (role not in it["roles"] and "*" not in it["roles"]) or surface not in it["surfaces"]:
                layers["policy"] = ("block", f"{pid} not allowed for {role} on {surface}")
                flags.append("policy:role-or-surface")
                steps.append("Guard: this question is not allowed here, refused")
                return finish("refuse", message="That question is not available here.")
        layers["policy"] = ("pass", "")
        floor = max((self.intents[p]["riskClass"] for p in plan_ids), key=guard.LEVELS.index)

        # Route.
        caps = list(dict.fromkeys(c for p in plan_ids for c in self.intents[p]["capabilities"]))
        inputs = {"bbl": bbl, **params}
        try:
            tool_plan = self.graph.plan(caps, surface=surface, permissions=PERMISSIONS.get(role, set()),
                                        availability=avail, inputs=inputs)
            model = self.graph.model("answer.compose", data_class="public", availability=avail)
        except RouteError as exc:
            flags.append(f"route:{exc.capability}")
            steps.append(f"Route: no tool can read {exc.capability} right now")
            out["risk"] = guard.assess(text, floor=floor, layers=layers, flags=flags, redactions=redactions)
            out.update(status="refuse", message="That record source is offline right now. Try again shortly.")
            return out
        tools = list(dict.fromkeys(s["tool"] for s in tool_plan))
        steps.append(f"Guard: {'pass' if not redactions else 'pass with redaction'}, read only public records")
        steps.append(f"Route: {len(caps)} record source{'s' if len(caps) != 1 else ''} via {', '.join(tools)}")
        steps.append(f"Recommend: model profile {model['profile']}")

        # Recommend: the signed contract.
        result = finish("act", floor)
        if result["status"] != "act":
            return result
        risk = result["risk"]
        result["contract"] = issue(
            trace_id=trace, intent=iid, intents=plan_ids, params=params, references={"bbl": bbl},
            confidence=out["confidence"], alternates=out["candidates"][1:4],
            risk={"level": risk["level"], "flags": risk["flags"], "redactions": risk["redactions"], "approvals": []},
            tool_plan=tool_plan, model_profile=model,
            scope={"role": role, "surface": surface, "capabilities": caps},
            registry_version=self.registry_version, policy_pack_version=POLICY_PACK)
        steps.append(f"Signed contract {result['contract']['id']}, valid 5 minutes"
                     + (" (ephemeral key)" if EPHEMERAL else ""))
        return result

    def _clarify(self, out: dict[str, Any], finish: Any, question: str, options: list[str], trigger: str) -> dict[str, Any]:
        opts = [o for o in dict.fromkeys(options) if o in self.intents and o != "GENERAL"][:4]
        out["clarification"] = {"question": question, "trigger": trigger,
                                "options": [{"intent": o, "label": self.label(o)} for o in opts]}
        out["steps"].append(f"Interpret: not sure enough, asking ({trigger})")
        return finish("clarify")


_default: Scout | None = None


def get_scout() -> Scout:
    """Process-wide Scout, built on first use."""
    global _default
    if _default is None:
        _default = Scout()
    return _default
