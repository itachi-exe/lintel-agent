# Lintel

Know the building before you sign the lease.

Lintel reads New York City's public records about a building and tells a renter, in plain words, what they say. Then you can ask it a question ("will I have heat this winter?") and it answers from the actual records, showing exactly why: what it understood, which records it read, the query it ran, and which model wrote the sentence.

Live: **https://lintel-why.vercel.app**
Open Agent Hackathon 2026, Track 02: The Agent That Can Explain Why.

## How it works

1. Type an address. NYC GeoSearch resolves it to the tax lot (BBL) and building (BIN).
2. Lintel pulls the last 3 years of HPD housing violations, 311 complaints, DOB violations, the PLUTO lot record and the 2050s floodplain, and turns them into a verdict plus short plain lines, each linked to its source row.
3. Ask anything about the building. The answer comes with evidence rows and a **Why this answer** panel.

## The agent, and why you can trust it

Every question goes through the **Meterless Scout Intent engine** (`app/scout/`) before anything runs: Sense, Interpret, Guard, Route, Recommend. The result is a signed execution contract (HMAC) that lists the only tools the agent may call. Prompt injection is refused, vague questions get a clarifying choice instead of a guess.

The tools run SQL through the **Zetaris** data layer (`app/zetaris.py`, sources registered by `zetaris/register.sql`). When no Zetaris instance is configured, the same statement runs directly against NYC Open Data and the answer says so (`data.via = "direct"`).

The sentence is written by **Meterless Nightshift** (OpenAI compatible, `meterless/auto` routing) with a fallback provider, and if no model is reachable, by deterministic rules from the facts. The response always names what actually ran. Nothing is ever claimed that did not happen.

Eval harness for the intent engine: `python3.12 -m app.scout.evals` (exits 1 below threshold). Current: injection recall 1.000, tool precision 0.966, blind top 1 0.900.

## Run it

```bash
pip install -r requirements.txt
python3.12 -m uvicorn app.main:app --port 8740
# open http://127.0.0.1:8740
```

Optional environment: `METERLESS_BASE_URL`, `METERLESS_TOKEN`, `METERLESS_MODEL`, `ZETARIS_REST_URL`, `ZETARIS_API_KEY`, `ZETARIS_ORG_ID`, `SOCRATA_APP_TOKEN`, `LINTEL_LLM_BASE`, `LINTEL_LLM_KEY`, `LINTEL_LLM_MODEL`.

Tests: `python3.12 tests/test_core.py` and `python3.12 tests/test_scout.py`.

## API

| Route | What it does |
| --- | --- |
| `GET /api/health` | Which data layer and model are live |
| `GET /api/geocode?q=` | Address search, up to 5 matches with BBL and BIN |
| `POST /api/core` | The building card |
| `POST /api/ask` | Agent answer: `ok`, `clarify` or `refused`, always with `why` |

Full contract: `docs/contract.md`, ask response: `docs/spec.md`.

## Repo map

| Path | Contents |
| --- | --- |
| `app/scout/` | Meterless Scout Intent engine, registry, guard, signed contracts, evals |
| `app/ask.py` | The agent: contract gated tools, evidence, the why |
| `app/zetaris.py` | Zetaris SQL layer with honest direct fallback |
| `app/card.py`, `app/sources.py`, `app/resolve.py`, `app/plain.py` | Records card, fetchers, address resolution, plain words |
| `app/llm.py` | Meterless Nightshift client and fallbacks |
| `web/` | Landing page and app, plain HTML, CSS and JS |
| `api/` | Vercel Python function entry points |
| `deck/` | Pitch deck |

## Timeline

All code in this repository was written inside the build window (Oct 22 to 27, 2026). The Scout engine, the ask agent and the Zetaris layer were written on Oct 23; the backend and the front end were written fresh on Oct 27. An earlier prototype of the same idea existed before the event; none of its code is in this repository.

## Data

NYC Open Data: HPD Housing Maintenance Code Violations (`wvxf-dwi5`), 311 Service Requests (`erm2-nwe9`), DOB Violations (`3h2n-5cm9`), PLUTO (`64uk-42ks`), Future Floodplain 2050s (`27ya-gqtm`). NYC GeoSearch for addresses, Transitous for commute times.
