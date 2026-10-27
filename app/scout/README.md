# Scout (app/scout)

Scout decides what Lintel does with a renter's question before any record is fetched or any model is called. It is a Python implementation of the **Meterless Scout Intent** engine spec (Apache License 2.0, `engines/scout-intent` in the Meterless repo), sized for one product: questions about one New York City building.

Every question passes five stages (Sense, Interpret, Guard, Route, Recommend) and leaves as exactly one of `act` (with a signed execution contract), `clarify`, or `refuse`. Pure standard library, no extra dependencies.

## Use

```python
from app.scout import get_scout, verify_contract, authorize_tool

scout = get_scout()
d = scout.decide("is there heat in winter?", surface="card", role="renter",
                 context={"bbl": "1008350041"}, availability={"zetaris.sql": True})
d["status"]        # "act" | "clarify" | "refuse"
d["intent"], d["confidence"], d["band"], d["params"], d["plan"]
d["risk"]          # {level: low|medium|high|block, flags, reason, redactions, layers}
d["clarification"] # {question, options[{intent,label}], trigger, missing?} or None
d["contract"]      # signed, or None
d["steps"]         # short sentences for the UI, one per stage

scout.decide(prompt, context=ctx, forced_intent="building.heat")  # the user picked a clarify option
scout.classify("any roaches?")  # Sense only: [{intent, score, span}]
verify_contract(d["contract"])  # signature and expiry
authorize_tool(d["contract"], "zetaris.sql", "records.hpd_violations")  # raises ContractRefused if not in the plan
```

Set `SCOUT_SIGNING_KEY` in production. Without it each process makes a random key; contracts then carry `ephemeral_key: true` and stop verifying after a restart.

## Files

| file | stage |
|---|---|
| `registry.json` | 14 intents plus `GENERAL`, spec record shape, 6 to 8 examples each |
| `capabilities.json` | capability to tool graph, tool metadata, model profiles |
| `sense.py` | Stage 1 trigger scan, Stage 2 similarity, merge, bands, multi-intent, latency self-test |
| `guard.py` | injection, scope, PII and sensitivity, redaction, rate limit, risk reduction |
| `route.py` | tool selection and model profile choice |
| `contract.py` | contract issue, HMAC signing, verification, executor scope check |
| `__init__.py` | `Scout.decide`, the pipeline and the decision rules |
| `evals/` | v1 corpus, thresholds, runner |

## Spec sections implemented

- **Registry (spec 3):** record shape `id`, `category`, `description`, `parameters` (JSON Schema), `capabilities`, `riskClass`, `surfaces`, `roles`, `examples`, `version`, plus `registryVersion`. Load fails on an intent without examples or with an unknown capability. `GENERAL` is the safe fallback and never routes to tools.
- **Sense (spec 4):** Stage 1 is a deterministic weighted trigger scan over the full registry with matched spans. The merge uses the canonical weights (0.40 lexical, 0.35 semantic, 0.20 contextual, 0.05 recency, minus risk penalty). Canonical bands: high at 0.80, medium at 0.55, low at 0.40 (clarify with the top 3), below 0.40 reject to `GENERAL`. Tie window 0.05 in the medium band triggers a clarify. High risk intents need 0.95 to proceed (none of Lintel's intents are high risk). Startup self-test raises if a full-registry scan exceeds 16 ms (measured about 3 ms).
- **Multi-intent (spec 4.5):** each clause is scored alone. Two high-band intents on disjoint clauses make a plan; other clause intents at medium or above join it. Contract `intents` lists them all and the tool plan covers the union of their capabilities.
- **Interpret:** `bbl` is a required reference (missing or malformed gives `clarify` with `missing: "bbl"`). `workplace` is bound from the prompt for `area.commute` (or taken from `context.workplace`), missing gives `clarify` with `missing: "workplace"`.
- **Guard (spec 5, risk-and-policy):** layers run in the spec's order of concern: rate, injection (on the raw text, NFKC folded, zero-width characters stripped), PII redaction, scope, sensitivity, policy (registry roles and surfaces). Each layer is pass, warn or block. Any block refuses; each warn raises the level one band above the intent's `riskClass`. Injection payloads are never logged, only pattern names and length.
- **Route (spec 7, capability-graph):** tool chosen by permissions, availability (passed at `decide()` time), latency budget per surface, cost, then local-first, then graph order. If no tool can serve a capability the decision is `refuse`. Scout never substitutes a tool from outside the capability's list.
- **Recommend and contract (execution-contract):** fields `id` (ULID shaped), `trace_id`, `intent`, `intents`, `params`, `references`, `confidence`, `alternates`, `risk`, `tool_plan`, `model_profile`, `scope`, `registry_version`, `policy_pack_version`, `issued_at`, `expires_at` (5 minutes), `signed_by`, `signature` (HMAC-SHA256 over canonical JSON). `authorize_tool` is the executor side: invalid, expired, or out-of-plan tools are refused and logged as `contract.refused`.
- **Evals (evals/README, thresholds):** `python3 -m app.scout.evals` with the spec's thresholds; exits 1 when any metric misses.

## Implementer's choices (not in the spec)

- **Normalization.** Lexical score per intent is `min(1, matched trigger weight) x (0.6 + 0.4 x share)`, where share is the intent's fraction of all specificity-weighted matched weight in the prompt. One strong, specific trigger is enough for a confident answer, and a prompt that hits several intents is discounted. Terms that did not run are dropped from the denominator (no contextual signal in Lintel, recency only when `context.recent_intents` is set), so scores stay on the 0 to 1 scale the bands assume.
- **Stage 2 is cheap, not neural.** "Semantic" is the better of (a) tf-idf cosine against the intent's examples (unigrams plus bigrams) and (b) idf-weighted coverage of the prompt's content words by the intent's vocabulary. Stdlib only. It runs on every request (Lintel has 14 intents, so it costs well under a millisecond), not only for medium-band prompts.
- **Typos.** Unknown words of 4 or more letters are mapped to a registry word with `difflib` (cutoff 0.85, same first letter). A crude suffix stemmer folds plurals and `-ing`/`-ed`.
- **Trigger specificity.** Each trigger phrase is weighted by the idf of its words across intents, so "roach" decides more than "violation".
- **`label` and `triggers`** are extra registry fields (UI label and the Stage 1 trigger table).
- **Reject band clarifies** with suggested intents, and explicit off-topic requests (poems, crypto prices, code) are refused by the scope layer. Questions about private people (tenant names, phone numbers, who lives in 4B) are refused; the owner of record is public and allowed.
- **Rate limit** is in-process memory, keyed by `context.client` (or role) and surface. Use a shared store if Lintel runs several workers.
- **Model profile:** `answer.compose` maps to `meterless/auto` for public data, with `local/template` as the fallback.
- **Not implemented:** the contextual signal, override and correction capture, approvals for high risk intents (Lintel has none), multi-turn conversation state beyond `recent_intents`.

## Evals

Corpus `evals/v1/`: `intents.jsonl` (105 labeled renter questions with typos and casual phrasing), `injection.jsonl` (27 attacks including subtle ones, 11 benign lookalikes), `multi_intent.jsonl` (7), `edge_cases.jsonl` (14: off-topic, private people, missing bbl or workplace, vague, redaction), and `intents_blind.jsonl` (30, written after tuning stopped, reported but not gated). The runner checks that no eval prompt duplicates or nearly duplicates a registry example (similarity 0.9 or higher fails the run). Latest results are in `evals/reports/latest.md`.

```
python3 -m app.scout.evals [--verbose]
python3 tests/test_scout.py        # or python3 -m pytest tests/test_scout.py -q
```
