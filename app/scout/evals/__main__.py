"""Scout eval runner: python3 -m app.scout.evals [--verbose]

Runs the v1 Lintel regression set through Scout.decide, prints a metrics table,
writes reports/latest.md, and exits 1 if any metric misses its target.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from .. import Scout

HERE = Path(__file__).parent
CORPUS = HERE / "v1"


def load_thresholds(path: Path) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Parse the flat `name: { key: number, ... }` lines of thresholds.yaml (no PyYAML needed)."""
    metrics, slices, section = {}, {}, None
    for line in path.read_text().splitlines():
        if re.match(r"^\w", line):
            section = line.split(":")[0]
        m = re.match(r"^\s+(\w+):\s*\{(.*)\}", line)
        if m:
            vals = {k.strip(): float(v) for k, v in (kv.split(":") for kv in m.group(2).split(","))}
            if section == "metrics":
                metrics[m.group(1)] = vals
            elif section == "slice_policy":
                slices[m.group(1)] = vals["slice_hard_floor"]
    return metrics, slices


def load(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (CORPUS / name).read_text().splitlines() if line.strip()]


def holdout_overlap(scout: Scout, examples: list[dict[str, Any]]) -> list[str]:
    """Eval prompts must not repeat registry examples (exact or near duplicate)."""
    norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", s.lower()))  # noqa: E731
    seeds = [norm(e) for it in scout.intents.values() for e in it["examples"]]
    return [ex["id"] for ex in examples
            if any(difflib.SequenceMatcher(None, norm(ex["prompt"]), s).ratio() >= 0.9 for s in seeds)]


def run(verbose: bool = False) -> int:
    scout = Scout(rate_limit=10**9)
    files = {"intents": load("intents.jsonl"), "injection": load("injection.jsonl"),
             "multi": load("multi_intent.jsonl"), "edge": load("edge_cases.jsonl")}
    every = [ex for rows in files.values() for ex in rows]

    results = []
    for kind, rows in files.items():
        for ex in rows:
            out = scout.decide(ex["prompt"], surface=ex.get("surface", "card"), role=ex.get("role", "renter"),
                               context=ex.get("context"), availability=ex.get("availability"))
            results.append((kind, ex, out))

    top1 = top3 = n_int = 0
    fam: dict[str, list[int]] = {}
    tp = fp = fn = 0
    tool_hit = tool_n = 0
    clar = n_clar = 0
    multi_hit = n_multi = 0
    edge_hit = n_edge = 0
    misses: list[str] = []
    for kind, ex, out in results:
        exp = ex["expected"]
        acted = out["intent"] if out["status"] == "act" else None
        if kind == "intents":
            n_int += 1
            ok = acted == exp["intent"]
            top1 += ok
            top3 += exp["intent"] in [c["intent"] for c in out["candidates"][:3]]
            fam.setdefault(exp["intent"].split(".")[0], []).append(ok)
            if not ok:
                misses.append(f"{ex['id']}: {ex['prompt']!r} -> {out['status']} {out['intent']} {out['confidence']}")
        if "injection" in exp:
            det = any(f.startswith("injection:") for f in out["risk"].get("flags", []))
            tp += det and exp["injection"]
            fp += det and not exp["injection"]
            fn += (not det) and exp["injection"]
            if det != exp["injection"]:
                misses.append(f"{ex['id']}: injection expected {exp['injection']} got {det}: {ex['prompt'][:60]!r}")
        if "tools" in exp:
            tool_n += 1
            got = sorted({s["tool"] for s in (out["contract"] or {}).get("tool_plan", [])})
            ok = got == sorted(set(exp["tools"]))
            tool_hit += ok
            if not ok:
                misses.append(f"{ex['id']}: tools expected {exp['tools']} got {got}")
        if kind != "injection":
            n_clar += 1
            clar += out["status"] == "clarify"
        if kind == "multi":
            n_multi += 1
            got = {out["intent"]} | {p["intent"] for p in out["plan"]} if out["status"] == "act" else set()
            ok = set(exp["intents"]) <= got
            multi_hit += ok
            if not ok:
                misses.append(f"{ex['id']}: multi expected {exp['intents']} got {sorted(got)} ({out['status']})")
        if kind == "edge":
            n_edge += 1
            ok = out["status"] == exp["status"]
            ok &= "intent" not in exp or out["intent"] == exp["intent"]
            ok &= all(out["params"].get(k) == v for k, v in exp.get("params", {}).items())
            ok &= "missing" not in exp or (out["clarification"] or {}).get("missing") == exp["missing"]
            ok &= not exp.get("redacted") or "[phone]" in (out["redacted_prompt"] or "")
            edge_hit += ok
            if not ok:
                misses.append(f"{ex['id']}: edge expected {exp} got {out['status']} {out['intent']}")
        for k, v in exp.get("params", {}).items():
            if kind == "intents" and out["params"].get(k) != v:
                misses.append(f"{ex['id']}: param {k} expected {v!r} got {out['params'].get(k)!r}")

    div = lambda a, b: a / b if b else 1.0  # noqa: E731
    metrics = {
        "intent_top1": div(top1, n_int), "intent_top3_recall": div(top3, n_int),
        "injection_precision": div(tp, tp + fp), "injection_recall": div(tp, tp + fn),
        "tool_precision": div(tool_hit, tool_n), "clarification_rate": div(clar, n_clar),
        "override_frequency": 0.0, "multi_intent_recall": div(multi_hit, n_multi),
        "edge_status_accuracy": div(edge_hit, n_edge),
    }
    counts = {"intent_top1": n_int, "intent_top3_recall": n_int, "injection_precision": tp + fp, "injection_recall": tp + fn,
              "tool_precision": tool_n, "clarification_rate": n_clar, "override_frequency": 0,
              "multi_intent_recall": n_multi, "edge_status_accuracy": n_edge}
    thresholds, slice_floor = load_thresholds(HERE / "thresholds.yaml")

    rows, failed = [], []
    for name, value in metrics.items():
        t = thresholds[name]
        ok = value <= t["target_max"] if "target_max" in t else value >= t["target"]
        goal = f"<= {t['target_max']:.2f}" if "target_max" in t else f">= {t['target']:.2f}"
        rows.append((name, f"{value:.3f}", goal, str(counts[name]), "pass" if ok else "FAIL"))
        if not ok:
            failed.append(name)
    for family, hits in sorted(fam.items()):
        value = sum(hits) / len(hits)
        ok = value >= slice_floor["intent_top1"]
        rows.append((f"intent_top1[{family}]", f"{value:.3f}", f">= {slice_floor['intent_top1']:.2f}", str(len(hits)),
                     "pass" if ok else "FAIL"))
        if not ok:
            failed.append(f"intent_top1[{family}]")
    blind = load("intents_blind.jsonl")
    b_hit = sum(scout.decide(ex["prompt"], context=ex["context"])["intent"] == ex["expected"]["intent"]
                and scout.decide(ex["prompt"], context=ex["context"])["status"] == "act" for ex in blind)
    rows.append(("intent_top1_blind", f"{b_hit / len(blind):.3f}", "info", str(len(blind)), "not gated"))
    every += blind
    overlap = holdout_overlap(scout, every)
    rows.append(("holdout_overlap", str(len(overlap)), "== 0", str(len(every)), "pass" if not overlap else "FAIL"))
    if overlap:
        failed.append("holdout_overlap")
        misses += [f"{i}: overlaps a registry example" for i in overlap]

    head = ("metric", "value", "target", "n", "result")
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(5)]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths))  # noqa: E731
    table = "\n".join([line(head), line(tuple("-" * w for w in widths))] + [line(r) for r in rows])
    summary = (f"{len(every)} examples ({n_int} intent, {len(blind)} blind, {tp + fn} injection + {fp + len(files['injection']) - tp - fn - fp} "
               f"benign in that file, {n_multi} multi-intent, {n_edge} edge). Stage 1 scan {scout.scan_ms:.2f} ms "
               f"(budget 16). override_frequency is 0 by construction: the corpus has no override data. "
               f"intent_top1_blind is a set written after tuning stopped and never tuned against.")
    print(table)
    print()
    print(summary)
    if misses and (verbose or failed):
        print("\nMisses:")
        print("\n".join("  " + m for m in misses))
    print("\nRESULT:", "FAIL " + ", ".join(failed) if failed else "all gates pass")

    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    report = (f"# Scout eval report\n\nGenerated {stamp} by `python3 -m app.scout.evals`, corpus v1, "
              f"registry v{scout.registry_version}.\n\n```\n{table}\n```\n\n{summary}\n\n"
              f"Result: {'FAIL ' + ', '.join(failed) if failed else 'all gates pass'}\n\n"
              + ("## Misses\n\n" + "\n".join(f"- {m}" for m in misses) + "\n" if misses else ""))
    (HERE / "reports").mkdir(exist_ok=True)
    (HERE / "reports" / "latest.md").write_text(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run("--verbose" in sys.argv))
