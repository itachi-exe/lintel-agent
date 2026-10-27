"""Guard: injection, policy, scope, PII/sensitivity and rate limit layers.

Each layer returns pass | warn | block with flags. Any block blocks; each warn lifts
the level one band above the intent's riskClass floor. Injection payloads are never
logged, only the names of the patterns that fired.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from typing import Any

log = logging.getLogger("lintel.scout")
LEVELS = ["low", "medium", "high", "block"]
I = re.IGNORECASE

INJECTION: list[tuple[str, str, re.Pattern[str]]] = [
    ("pattern", "instruction-override", re.compile(
        r"\b(ignore|disregard|forget|override|bypass|skip|drop)\b[^.?!\n]{0,30}\b(previous|prior|above|earlier|preceding|original|"
        r"system|initial|your|safety|all|those|these)\b[^.?!\n]{0,20}\b(instruction|rule|prompt|direction|guideline|"
        r"constraint|guardrail|polic(?:y|ies)|programming|training|safety)", I)),
    ("pattern", "control-token", re.compile(
        r"</?\s*(system|assistant|user|developer|tool)\s*>|\[/?(inst|sys)\]|<<\s*sys\s*>>|<\|im_(start|end)\|>|"
        r"^\s*(system|assistant|developer)\s*:|#{2,}\s*(instruction|system)", I | re.MULTILINE)),
    ("role-drift", "persona", re.compile(
        r"\byou are (now|no longer|actually)\b|\b(act|behave|respond|roleplay) as (an?|the|if|my)\b|"
        r"\bpretend (to be|you are|you're|that you)\b|\bfrom now on\b[^.?!]{0,40}\b(you|answer|respond|always|only)\b|"
        r"\b(developer|god|dan|jailbreak|unrestricted|debug|admin|maintenance) mode\b|\bnew (system )?(instruction|persona|role)s?\b|"
        r"\bjailbr[eo]a?k|\bno (restrictions|filters|limits|guardrails)\b", I)),
    ("pattern", "exfiltration", re.compile(
        r"\b(reveal|print|show|repeat|output|leak|dump|share|what (is|are))\b[^.?!]{0,30}\b(system prompt|hidden (instruction|prompt)s?|"
        r"your (instruction|prompt|rule|config|configuration)s?\b|initial prompt|api keys?|secrets?|signing key|"
        r"env(ironment)? var|credentials|passwords?|tokens?)", I)),
    ("tool-ref", "sql-or-code", re.compile(
        r"\b(drop|truncate|alter|grant)\s+(table|database|schema|user)\b|\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b|"
        r";\s*--|\bunion\s+(all\s+)?select\b|'\s*or\s+'?1'?\s*=\s*'?1|\bselect\s+\*?\s*\w*\s*from\s+\w+|"
        r"\b(run|execute|call|invoke|exec|eval)\b[^.?!]{0,25}\b(shell|bash|terminal|command|curl|wget|python|script|sql|query|tool|"
        r"function|zetaris|socrata)\b|\brm\s+-rf\b|\bos\.system\b|`[^`]{3,}`", I)),
    ("tool-ref", "out-of-band-action", re.compile(
        r"\b(send|forward|post|upload|email|text|sms)\b[^.?!]{0,30}(\b[\w.+-]+@[\w-]+\.\w+|https?://|\bwebhook\b)|"
        r"\b(fetch|open|visit|browse|load|download|follow)\b[^.?!]{0,20}https?://|"
        r"(^|[.!?]\s*|\b(please|now|then|also|you must|you should|go ahead and)\s+)(delete|remove|erase|hide|suppress|scrub|wipe|"
        r"alter|edit|modify|change)\b[^.?!]{0,30}\b(violation|complaint|record|data|history|database|table|result)s?\b|"
        r"\bmark (it|this|the building) as (safe|clean|fine|ok)\b", I)),
    ("pattern", "steer-answer", re.compile(
        r"\b(tell|inform|assure|convince) (the )?(user|renter|tenant|reader|people|everyone)s? (that|it'?s|this)\b|"
        r"\b(always|only|must) (say|answer|respond|reply|output)\b|\b(say|state|claim|report|write|answer) that (this|the) "
        r"(building|apartment|place|landlord|owner) (is|has)\b|\brespond (only )?with\b", I)),
    ("pattern", "encoded-payload", re.compile(r"[A-Za-z0-9+/]{48,}={0,2}|(?:\\x[0-9a-f]{2}){6,}|(?:%[0-9a-f]{2}){8,}", I)),
]

OFF_TOPIC = re.compile(
    r"\b(poem|haiku|sonnet|limerick|song|lyrics|rap|short story|bedtime story|essay|joke|riddle|bitcoin|crypto|ethereum|stock|stocks|"
    r"share price|recipe|homework|weather (today|tomorrow|forecast)|forecast for|translate|horoscope|"
    r"capital of|who won|world cup|super bowl|nba|nfl|movie|netflix|dating|"
    r"write (me )?(a|an|some) (code|program|script|function|email|letter|tweet|post|cover letter)|python code)\b", I)
SENSITIVE = re.compile(
    r"\b(tenant|resident|neighbou?r|occupant|people living|people who live)s?'?\b"
    r"[^.?!]{0,40}\b(name|phone|cell|phone number|email|contact info|contact detail|ssn|social security|criminal|arrest|"
    r"immigration|immigrant|income|salary|religion|ethnicity|race|birthday|date of birth|instagram|facebook)s?\b|"
    r"\bwho (lives|lived|is living|stays|sleeps) (in|at|on|next)\b|\b(name|phone|number|email)s? of (the )?(tenant|resident|"
    r"neighbou?r|people|person|occupant|super)s?\b|\b(ssn|social security number|date of birth|criminal record|background check)\b|"
    r"\b(apartment|apt|unit)(\s*#\s*|\s+)(\d{1,4}[a-z]?|[a-z]\d{0,3})\b[^.?!]{0,20}\b(who|whose|name)\b|"
    r"\b(home address|personal (phone|cell|email|address)|where (does|do) (he|she|they|the owner|the landlord) live)\b", I)
REDACT = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<!\d)(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")),
]
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff]")


def clean(prompt: str) -> tuple[str, list[str]]:
    """NFKC fold and strip zero-width characters that hide payloads."""
    flags = ["unicode:zero-width"] if ZERO_WIDTH.search(prompt) else []
    return unicodedata.normalize("NFKC", ZERO_WIDTH.sub("", prompt)), flags


def injection(prompt: str) -> dict[str, Any]:
    hits = [(kind, name) for kind, name, rx in INJECTION if rx.search(prompt)]
    return {"detected": bool(hits), "kind": hits[0][0] if hits else None, "score": min(1.0, 0.6 + 0.2 * len(hits)) if hits else 0.0,
            "evidence": [name for _, name in hits]}


def redact(prompt: str) -> tuple[str, list[dict[str, Any]]]:
    out, redactions = prompt, []
    for kind, rx in REDACT:
        out, n = rx.subn(f"[{kind}]", out)
        if n:
            redactions.append({"kind": kind, "count": n})
    return out, redactions


class RateLimiter:
    """Sliding window per key. shortcut: in-process memory only, use a shared store if Lintel runs multiple workers."""

    def __init__(self, limit: int = 30, window_s: float = 60.0) -> None:
        self.limit, self.window = limit, window_s
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.hits.get(key, []) if now - t < self.window]
            ok = len(recent) < self.limit
            if ok:
                recent.append(now)
            self.hits[key] = recent
            if len(self.hits) > 10000:  # bound memory
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
            return ok


def assess(prompt: str, *, floor: str, layers: dict[str, tuple[str, str]], flags: list[str],
           redactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce layer verdicts {layer: (pass|warn|block, reason)} to the spec's risk object."""
    blocks = [(k, r) for k, (v, r) in layers.items() if v == "block"]
    warns = sum(v == "warn" for v, _ in layers.values())
    level = "block" if blocks else LEVELS[min(2, LEVELS.index(floor) + warns)]
    risk = {"level": level, "flags": flags, "redactions": redactions,
            "layers": {k: v for k, (v, _) in layers.items()}, "reason": blocks[0][1] if blocks else None}
    if blocks:
        log.info("scout.guard block layer=%s flags=%s chars=%d", blocks[0][0], ",".join(flags), len(prompt))
    return risk
