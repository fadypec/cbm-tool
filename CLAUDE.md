# CBM Facility Explorer

Extracts structured data from BWC Confidence-Building Measure (CBM) PDF submissions
and serves it via a REST API + interactive web dashboard.

## Commands

```bash
# Activate venv (always required)
source .venv/bin/activate

# Extraction pipeline (run in order)
python3 scripts/01_catalogue.py --skip-download   # check for new entries
python3 scripts/01_catalogue.py                    # download new PDFs
python3 scripts/02_extract_text.py                 # OCR + text extraction
python3 scripts/03_segment_forms.py                # split into form sections
python3 scripts/04_extract_structured.py           # Form A1 (research facilities)
python3 scripts/04_extract_structured.py --form-g  # Form G (vaccine facilities)
python3 scripts/04_extract_structured.py --form-a2 # Form A2 (defence programmes)
python3 scripts/04_extract_structured.py --form-f  # Form F (past programmes)
python3 scripts/04_extract_structured.py --form-e  # Form E (legislation)
python3 scripts/05_assemble_output.py              # build all CSVs/JSON

# Database
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
bash db/migrate.sh                                 # apply migrations
python3 scripts/06_load_database.py                # load (preserves geom if already geocoded)
python3 scripts/dedup_entities.py --apply          # merge fragmented canonical entities
python3 scripts/07_geocode.py                      # geocode (~2 hrs); only needed after fresh load

# Annual update (runs full pipeline interactively)
bash scripts/annual_update.sh

# API + dashboard
uvicorn api.main:app --port 8000 --reload
# Then open http://localhost:8000

# Tests
pytest tests/test_api.py -v
```

## Architecture

```
cbm-tool/
├── scripts/          01–07: extraction pipeline
│   ├── dedup_entities.py   entity deduplication (run after 06, before 07)
│   └── annual_update.sh    interactive pipeline runner for annual updates
├── api/
│   └── main.py       FastAPI app: REST endpoints + serves dashboard/
├── dashboard/
│   ├── index.html
│   └── static/       app.js, style.css, countries.geojson (choropleth base)
├── db/
│   └── migrations/   001–005 SQL migrations (applied via db/migrate.sh)
├── data/
│   ├── raw_pdfs/
│   ├── extracted_text/
│   ├── segmented/
│   ├── structured/   per-document JSON from script 04
│   └── output/       final CSVs/JSON
├── Dockerfile        Railway deployment (CMD uses $PORT env var)
├── requirements-api.txt   API-only deps (used in Docker)
└── requirements.txt       full dev deps (includes OCR tools)
```

## Key technical facts about CBM documents

CBM PDFs are submitted annually by BWC states parties. The 2011 revised
template (used from 2012 onwards) has these forms:
- Form 0: Cover page with "nothing to declare" table
- Form A Part 1: Research centres and laboratories (PRIORITY)
- Form A Part 2: National biological defence programmes
- Form B: Outbreaks (deferred — free-text, no use case)
- Form C: Publications
- Form E: Legislation
- Form F: Past offensive/defensive programmes
- Form G: Vaccine production facilities (PRIORITY)

Form A Part 1 facility entries use numbered fields 1–7 (name, org, address,
financing, containment units + size, highest BSL if no max-containment, activity/organisms).
Multiple facilities appear sequentially, each restarting at field 1.

Primary PDF source: bwc-cbm.un.org — public JSON search API (no auth required)
  - Enumerate: POST https://bwc-cbm.un.org/api/search/ with {from, size, search:"", filter:{country:[]}}
  - Download: POST https://cms-bwc-cbm.un.org/api/getDocument with {reportId: <int>, language: null}
  - 517 public records; China/France/Russia/India absent (restricted — not a pipeline failure)
  - bwcimplementation.org is dead (empty HTML, JS-rendered) — do not use

## API (api/main.py)

FastAPI + psycopg2 ThreadedConnectionPool (1–10 connections). The pool is
initialized in a lifespan context manager and accessed via `cursor()` /
`cursor_write()` context managers.

Key endpoints:
- `GET /health` — lightweight healthcheck (no DB), used by Railway
- `GET /api/stats` — global summary counts
- `GET /api/countries` — all submitting countries
- `GET /api/country/{iso3}` — compliance history + facility list
- `GET /api/map/facilities` — GeoJSON for choropleth (A1)
- `GET /api/map/compliance[/{form}]` — per-country submission rates
- `GET /api/search?q=` — facility search (max 400 chars)
- `GET /api/entity/{id}` — canonical facility history
- `POST /api/natural-query` — AI facility search (rate-limited 10/min)
- `POST /api/entity/{id}/flag/{year}` — flag for review (requires X-Review-Key)
- `GET /api/flagged` — review queue (requires X-Review-Key)

Security: CSP, X-Frame-Options, HSTS-compatible headers via SecurityHeadersMiddleware.
Rate limiting via slowapi (reads X-Forwarded-For rightmost entry behind Railway proxy).
Docs disabled in production (set ENVIRONMENT=dev to enable /api/docs).

## Database (PostgreSQL 17 + PostGIS)

9 tables: `documents`, `facilities`, `facility_years`, `vaccine_facility_years`,
`defence_programmes`, `defence_facilities`, `past_programmes`, `legislation`,
`form_compliance`.

Geom columns on `facility_years` + `defence_facilities` (EPSG:4326, Nominatim geocoding).
Script 06 preserves `facility_years` geom on full reload; `defence_facilities` geom must
be re-geocoded via script 07 after reload.

Entity resolution: Union-Find within-country on `facility_name` using rapidfuzz
token_sort_ratio ≥ 85. Canonical IDs: `{ISO3}_{N:03d}`.

## Deployment

- **Production**: Railway (Amsterdam) — auto-deploys from GitHub `main`
- **Database**: Supabase PostgreSQL (EU West / Ireland)
- **Env vars required**: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `REVIEW_API_KEY`
- **Healthcheck**: Railway checks `GET /health` (set in Railway service settings)
- **Port**: Dockerfile CMD uses `${PORT:-8000}`; Railway injects `$PORT`

## Dashboard (dashboard/)

Leaflet 1.9 + Bootstrap 5 (CDN), plain JS (no build step).
Choropleth loaded from `/static/countries.geojson`.
BSL colours: 4=#c0392b, 3=#e67e22, 2=#f39c12, 1=#27ae60, unknown=#95a5a6.
Choropleth styles are module-level constants (CHORO_STYLE_*) in app.js.

## Extraction model

`claude-sonnet-4-20250514`, MAX_TOKENS=8192, CHUNK_MAX_CHARS=4000,
RATE_LIMIT_DELAY=10s (≤6 RPM). `parse_json_response` returns `dict | None`.

## Gotchas

- **Script 04 is incremental**: skips documents whose output JSON already exists.
  Use `--force` to re-extract. Re-run script 05 after any re-extraction.
- **dedup_entities.py order**: must run after `06_load_database.py` (so the
  `facilities` table is populated) and before `07_geocode.py`.
- **PostgreSQL PATH**: Homebrew pg17 requires `export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"`.
- **China/France/Russia/India** have no public CBM data — this is expected, not a bug.
- **Year regex** in 01_catalogue.py uses `(?<!\d)(20\d{2})(?!\d)` not `\b`
  (underscore counts as `\w` so `\b` fails on filenames like `GBR_2023.pdf`).
- **OCR correction** (script 02): every page is sent to Claude for error correction
  after Tesseract. Use `--correct-ocr` flag to backfill already-OCR'd docs.
- **Form E**: no truncation (`FORM_E_MAX_CHARS = None`) because some countries use
  literal "Yes/No" strings requiring full law listings for inference.
- **Non-English docs**: French (26), Russian (7) use char-split fallback in script 04.
- **Geocoding**: ~2 hrs for a full run; 97.3% match rate (191 no-address skips).
