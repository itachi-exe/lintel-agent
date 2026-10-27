# Lintel v2: the agent that explains why (Open Agent Hackathon 2026, built Oct 23 to 27)

Everything in this file is NEW work inside the build window. v1 (Oct 3 to 6) is the
records card. v2 adds an agent that answers a renter's question about one building,
and shows why: the intent it understood, the records it read, the query it ran, and
the model that wrote the sentence.

Track: 02, The Agent That Can Explain Why.

## Required sponsor stack (event rules)

- Zetaris: the data layer. NYC open records are registered as Zetaris sources and the
  agent's tools run SQL through Zetaris (REST). `app/zetaris.py`.
  When no Zetaris instance is configured the same question runs straight against
  Socrata and the answer says so (`data.via = "direct"`). Never pretend.
- Meterless Scout Intent engine: every question passes Sense, Interpret, Guard, Route,
  Recommend and becomes a signed execution contract before anything runs. `app/scout/`.
  Proven by its eval harness: `python3 -m app.scout.evals` (exit 1 below threshold).
- Meterless Nightshift: the primary AI provider, OpenAI compatible, contract 0.90.0
  (`meterless-app: lintel`, routing mode `meterless/auto`, 180 s timeout, status read
  from GET /v1/models). NVIDIA Nemotron is connected inside Nightshift. `app/llm.py`.
  Fallback provider only when Nightshift is offline, and the answer says which ran.

## New endpoint

POST /api/ask
request:  {"bbl": "3012340056", "question": "will I have heat this winter?", "workplace": null}
response, always one of three shapes:

    {"status": "ok",
     "answer": "One short paragraph, plain words.",
     "evidence": [{"label": "No heat, Jan 12 2026", "detail": "inspector text or value",
                   "source": "HPD violations", "record": "15234567", "url": "https://...",
                   "date": "2026-01-12"}],
     "why": {"intent": "building.heat", "intent_label": "Heat and hot water",
             "confidence": 0.86, "band": "high", "risk": "low",
             "steps": ["Understood: heat and hot water", "Read 14 HPD violations ...", ...],
             "contract": {"id": "sc_...", "registry_version": 1, "tool_plan": [...],
                          "signature": "hmac-sha256:..."}},
     "data": {"via": "zetaris" | "direct", "sql": "SELECT ...", "rows": 14},
     "model": {"provider": "meterless" | "fallback" | "none", "mode": "meterless/auto",
               "model": "nemotron..." | null}}

    {"status": "clarify", "question": "Did you mean ...?",
     "options": [{"intent": "building.pests", "label": "Pests"}, ...], "why": {...}}

    {"status": "refused", "message": "One short line ending in what to ask instead.",
     "why": {...}}

## Front end (web/)

Landing page plus app on one page, his design system, accent kept brass (#c8a24a).
After a card loads, an "Ask about this building" box sits under the verdict with 4
suggestion chips. The answer renders as: the sentence, the evidence rows (each a link),
and a collapsible "Why this answer" panel showing the steps, intent + confidence, the
SQL, and the contract id. A small footer line: "Records via Zetaris, model via
Meterless Nightshift" reflecting the real `data.via` / `model.provider` values.
