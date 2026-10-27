# Lintel Agent: API contract (written Oct 27 2026, inside the build window)

Lintel Agent: paste a New York City address, get a plain-words building check from
public records, then ask the agent a question about that building and see why it
answered the way it did. Track 02, The Agent That Can Explain Why.

Backend: Python 3.12, FastAPI, httpx. Runs locally with
`python3.12 -m uvicorn app.main:app --port 8740` and on Vercel as Python functions
(one file per route in api/, each just `from app.main import app`).
Frontend: plain static HTML/CSS/JS in web/ (no build step), served by FastAPI locally
and copied to public/ for Vercel.

## GET /api/health
`{"ok": true, "zetaris": {"configured": bool}, "llm": {"provider": "meterless"|"fallback"|"none"}}`

## GET /api/geocode?q=<text>
Address search via NYC GeoSearch (https://geosearch.planninglabs.nyc/v2/search).
`{"results": [{"label": "123 Main St, Brooklyn, NY 11201", "bbl": "3012340056",
"bin": "3001234", "lat": 40.7, "lon": -73.9, "borough": "Brooklyn"}]}` (max 5)

## POST /api/core  body `{"bbl": "3012340056"}`
The building card. All records from NYC Open Data (Socrata), last 3 years.
```
{"bbl": "...", "bin": "...", "address": "...", "borough": "...",
 "lot": {"year_built": 1931, "floors": 6, "homes": 48, "owner": "...", "building_class": "..."},
 "verdict": {"level": "good"|"mixed"|"bad", "line": "One short plain sentence."},
 "sections": [
   {"id": "hpd", "title": "Housing violations", "count_open": 12, "count_total": 40,
    "items": [{"line": "plain words", "date": "2026-01-12", "class": "C", "open": true,
               "source": "HPD violations", "url": "https://..."}]},   // max 8 items
   {"id": "311", "title": "311 complaints", ...},
   {"id": "dob", "title": "Building department violations", ...},
   {"id": "flood", "title": "Flood risk 2050s", "line": "...", ...}
 ],
 "links": {"zola": "...", "hpd": "...", "dob": "..."}}
```
Errors: `{"error": "One short line ending in the fix."}` with HTTP 400/404/502.

## POST /api/ask  (existing in-window module app/ask.py, keep its response shape)
body `{"bbl": "...", "question": "...", "workplace": null}`
Returns status ok | clarify | refused, see docs/spec.md. The `why` panel and
`data.via` / `model.provider` must reflect what really ran. Never pretend.

## Rules
- No em dashes or dashes as asides in any user-facing copy.
- Error copy: one short line ending in the fix.
- No emoji in UI. Custom inline SVG icons only.
