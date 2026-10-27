"""Sense: Stage 1 weighted trigger scan plus a stdlib Stage 2 similarity rescoring.

Canonical formula (spec 4.3): 0.40 lexical + 0.35 semantic + 0.20 contextual
+ 0.05 recency - riskPenalty. Terms that did not run are left out of the
denominator, so the merged score stays on the 0..1 scale the bands assume
(implementer's choice, see README).
"""

from __future__ import annotations

import difflib
import math
import re
import time
from functools import lru_cache
from typing import Any

WEIGHTS = {"lexical": 0.40, "semantic": 0.35, "contextual": 0.20, "recency": 0.05}
BANDS = (("high", 0.80), ("medium", 0.55), ("low", 0.40), ("reject", 0.0))
SCAN_BUDGET_MS = 16.0
SEM_SCALE = 0.55  # cosine at which an example counts as a full semantic match

_STOP_WORDS = (
    "a an the is are was were be been am do does did i me my we our you your it its this that these those "
    "here there of in on at to for with and or but if so any some there's what's whats how what when where "
    "which who whom why can could would should will shall may might must have has had not no yes just about "
    "really very much many more most get got than then too also ok okay please tell let lets know like "
    "place building live living"
)
TOKEN = re.compile(r"[a-z0-9]+")
CLAUSE = re.compile(r"[?.!;]+|,\s*(?:and\s+)?|\s+(?:and also|and then|and|also|plus|then)\s+")


def band(score: float) -> str:
    return next(name for name, floor in BANDS if score >= floor)


def stem(word: str) -> str:
    """Crude suffix stripper (plural, then -ing/-ed), applied alike to triggers, examples and prompts."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        word = word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed") and not word.endswith("eed"):
        word = word[:-2]
    return word


STOP = {stem(w) for w in _STOP_WORDS.split()}


def tokens(text: str) -> list[tuple[str, int, int]]:
    """(stem, start, end) for each word, with "'s" folded away."""
    text = text.lower().replace("\u2019", "'").replace("'s ", " ").replace("'", "")
    return [(stem(m.group()), m.start(), m.end()) for m in TOKEN.finditer(text)]


class Sense:
    def __init__(self, intents: list[dict[str, Any]]) -> None:
        self.intents = [i for i in intents if i["id"] != "GENERAL"]
        self.triggers: dict[str, list[tuple[tuple[str, ...], float]]] = {}
        vocab: set[str] = set()
        for it in self.intents:
            trig = [(tuple(t for t, _, _ in tokens(p)), float(w)) for p, w in it.get("triggers", {}).items()]
            self.triggers[it["id"]] = sorted(trig, key=lambda t: -len(t[0]))
            for seq, _ in trig:
                vocab.update(seq)
            for ex in it["examples"] + [it["description"]]:
                vocab.update(t for t, _, _ in tokens(ex))
        self.vocab = {v for v in vocab if v.isalpha() and len(v) >= 4}
        self._fix = lru_cache(maxsize=4096)(self._fix_uncached)
        # Stage 2 prototypes: tf-idf vectors of every registry example (for cosine), plus each
        # intent's whole vocabulary (examples, description, triggers) with idf over intents (for coverage).
        docs = [(it["id"], self._features(ex)) for it in self.intents for ex in it["examples"]]
        df: dict[str, int] = {}
        for _, feats in docs:
            for f in set(feats):
                df[f] = df.get(f, 0) + 1
        self.idf = {f: math.log((1 + len(docs)) / (1 + n)) + 1 for f, n in df.items()}
        self.protos: dict[str, list[dict[str, float]]] = {}
        for iid, feats in docs:
            self.protos.setdefault(iid, []).append(self._vec(feats))
        self.ivocab: dict[str, set[str]] = {}
        for it in self.intents:
            texts = it["examples"] + [it["description"]] + list(it.get("triggers", {}))
            self.ivocab[it["id"]] = {f for t in texts for f in self._features(t)}
        idf_df: dict[str, int] = {}
        for feats in self.ivocab.values():
            for f in feats:
                idf_df[f] = idf_df.get(f, 0) + 1
        n = len(self.intents)
        self.iidf = {f: math.log((1 + n) / (1 + d)) + 1 for f, d in idf_df.items()}
        top = max(self.iidf.values())
        # trigger specificity: a word every intent uses ("violation") decides less than one only pests uses ("roach")
        self.spec = {iid: {seq: sum(self.iidf.get(w, top) for w in seq) / (len(seq) * top) for seq, _ in trig}
                     for iid, trig in self.triggers.items()}

    # ---------------------------------------------------------------- text
    def _fix_uncached(self, word: str) -> str:
        """Typo repair: map an unknown word to a close registry word (same first letter)."""
        if word in self.vocab or len(word) < 4 or not word.isalpha():
            return word
        pool = [v for v in self.vocab if v[0] == word[0] and abs(len(v) - len(word)) <= 2]
        hit = difflib.get_close_matches(word, pool, n=1, cutoff=0.85)
        return hit[0] if hit else word

    def norm(self, text: str) -> list[tuple[str, int, int]]:
        return [(self._fix(t), s, e) for t, s, e in tokens(text)]

    def _features(self, text: str) -> list[str]:
        words = [t for t, _, _ in self.norm(text) if t not in STOP]
        return words + [a + "_" + b for a, b in zip(words, words[1:])]

    def _vec(self, feats: list[str]) -> dict[str, float]:
        vec: dict[str, float] = {}
        for f in feats:
            vec[f] = vec.get(f, 0.0) + self.idf.get(f, 1.0)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {f: v / norm for f, v in vec.items()}

    # ---------------------------------------------------------------- stage 1
    def scan(self, prompt: str) -> dict[str, tuple[float, float, list[tuple[int, int]]]]:
        """Weighted trigger scan over the full registry: intent -> (raw weight, specific weight, char spans)."""
        toks = self.norm(prompt)
        words = [t for t, _, _ in toks]
        out: dict[str, tuple[float, float, list[tuple[int, int]]]] = {}
        for iid, trig in self.triggers.items():
            used = [False] * len(words)
            raw, specific, spans = 0.0, 0.0, []
            for seq, weight in trig:  # longest phrases first, no word counted twice
                n = len(seq)
                for i in range(len(words) - n + 1):
                    if words[i : i + n] == list(seq) and not any(used[i : i + n]):
                        used[i : i + n] = [True] * n
                        raw += weight
                        specific += weight * self.spec[iid][seq]
                        spans.append((toks[i][1], toks[i + n - 1][2]))
            if raw:
                out[iid] = (raw, specific, spans)
        return out

    def lexical(self, prompt: str) -> tuple[dict[str, float], dict[str, tuple[int, int]]]:
        """Saturation (one strong trigger = 1.0) times dominance, the spec's fraction of matched
        weight, computed on specificity-weighted matches."""
        hits = self.scan(prompt)
        total = sum(sp for _, sp, _ in hits.values()) or 1.0
        lex = {iid: min(1.0, r) * (0.6 + 0.4 * sp / total) for iid, (r, sp, _) in hits.items()}
        spans = {iid: (min(s for s, _ in sp), max(e for _, e in sp)) for iid, (_, _, sp) in hits.items()}
        return lex, spans

    # ---------------------------------------------------------------- stage 2
    def semantic(self, prompt: str) -> dict[str, float]:
        """Per intent, the better of (a) best cosine vs its examples and (b) idf-weighted coverage of the
        prompt's content words by its vocabulary. Taken over the whole prompt and each clause."""
        pieces = [prompt] + [c for c in CLAUSE.split(prompt) if c and c.strip() and c.strip() != prompt.strip()]
        best: dict[str, float] = {}
        for piece in pieces:
            feats = self._features(piece)
            words = [f for f in feats if "_" not in f]
            mass = sum(self.iidf.get(w, 1.0) for w in words)  # unknown words weigh like common ones
            vec = self._vec(feats)
            for iid, protos in self.protos.items():
                sim = max(sum(vec.get(f, 0.0) * w for f, w in p.items()) for p in protos) / SEM_SCALE
                cov = sum(self.iidf.get(w, 1.0) for w in words if w in self.ivocab[iid]) / mass if mass else 0.0
                best[iid] = max(best.get(iid, 0.0), min(1.0, max(sim, cov)))
        return best

    # ---------------------------------------------------------------- merge
    def classify(
        self, prompt: str, *, recent: list[str] | None = None, penalty: dict[str, float] | None = None, k: int = 5
    ) -> list[dict[str, Any]]:
        lex, spans = self.lexical(prompt)
        sem = self.semantic(prompt)
        active = WEIGHTS["lexical"] + WEIGHTS["semantic"] + (WEIGHTS["recency"] if recent else 0.0)
        cands = []
        for it in self.intents:
            iid = it["id"]
            s = WEIGHTS["lexical"] * lex.get(iid, 0.0) + WEIGHTS["semantic"] * sem.get(iid, 0.0)
            if recent and iid in recent:
                s += WEIGHTS["recency"]
            s = max(0.0, s / active - (penalty or {}).get(iid, 0.0))
            span = spans.get(iid)
            cands.append({"intent": iid, "score": round(s, 3), "span": prompt[span[0] : span[1]] if span else "",
                          "_span": span, "_lex": lex.get(iid, 0.0)})
        cands.sort(key=lambda c: (-c["score"], -c["_lex"]))
        return cands[:k]

    def multi(self, prompt: str) -> list[dict[str, Any]]:
        """Spec 4.5: score each clause alone. Two High intents on disjoint clauses make a plan;
        then every other clause intent at or above the Medium floor joins as a secondary."""
        clauses = [c.strip() for c in CLAUSE.split(prompt) if c and len(c.strip()) > 2]
        if len(clauses) < 2:
            return []
        found: dict[str, dict[str, Any]] = {}
        for clause in clauses:
            best = self.classify(clause, k=1)[0]
            if best["score"] >= 0.55 and best["score"] > found.get(best["intent"], {}).get("score", 0):
                found[best["intent"]] = {"intent": best["intent"], "score": best["score"], "span": clause}
        plan = sorted(found.values(), key=lambda c: -c["score"])
        return plan if sum(c["score"] >= 0.80 for c in plan) >= 2 else []

    def self_test(self, probes: list[str]) -> float:
        """Startup latency check: worst best-of-5 Stage 1 full-registry scan, in ms. Raises over budget."""
        worst = 0.0
        for p in probes:
            runs = []
            for _ in range(5):
                t0 = time.perf_counter()
                self.lexical(p)
                runs.append((time.perf_counter() - t0) * 1000)
            worst = max(worst, min(runs))
        if worst > SCAN_BUDGET_MS:
            raise RuntimeError(f"Scout Stage 1 scan took {worst:.1f} ms, budget is {SCAN_BUDGET_MS} ms")
        return worst
