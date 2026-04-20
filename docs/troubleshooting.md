# Troubleshooting Guide

Common pipeline and deployment failure modes with recovery steps.

---

## Extraction Pipeline (Scripts 01-05)

### Anthropic API rate limit during extraction (script 04)

**Symptoms:** `anthropic.RateLimitError` or HTTP 429 in logs.

**Recovery:**
1. The retry logic (added 2026-04-18) handles transient 429s with exponential backoff (60s, 120s, 240s).
2. If the run is killed mid-extraction, it resumes from the last checkpoint (`.partial.json` files in `data/structured/`).
3. If you need to force re-extraction of specific documents: delete their output JSON in `data/structured/` and re-run, or use `--force`.
4. To reduce rate pressure: increase `RATE_LIMIT_DELAY` in `scripts/model_config.py` (default 10s = ~6 RPM).

### Corrupt extraction JSON

**Symptoms:** `json.JSONDecodeError` when running script 05 or 06.

**Recovery:**
1. Script 04 auto-detects corrupt output on re-run and re-extracts.
2. To manually fix: delete the corrupt file in `data/structured/<ID>.json` and re-run script 04.
3. Check for `.partial.json` files — these are incomplete checkpoints. Delete them to force a clean re-extraction.

### OCR produces garbage text (script 02)

**Symptoms:** Extracted text is garbled; segmentation (script 03) fails to find form headers.

**Recovery:**
1. Check the source PDF: `python3 -c "import pdfplumber; pdf = pdfplumber.open('data/raw_pdfs/XXX.pdf'); print(len(pdf.pages), pdf.pages[0].extract_text()[:200])"`
2. If the PDF is scanned/image-based: Tesseract OCR should handle it. Re-run with `--correct-ocr` flag for Claude-based OCR correction.
3. If the PDF is encrypted or password-protected: pdfplumber will return empty text. These must be manually processed or skipped.
4. For non-Latin scripts (Russian, Arabic): the pipeline uses character-split fallback. Results may be lower quality.

### Script 03 segmentation misses forms

**Symptoms:** `form_a1`, `form_g`, etc. are `null` in segmented output.

**Recovery:**
1. 98.4% of documents are handled by regex; 8 use LLM fallback.
2. Check if the document uses unusual formatting (e.g. no standard form headers).
3. Re-run script 03 with `--force` on the specific document.
4. If the document truly has no Form A1 content, the null is correct — not all countries submit all forms.

---

## Geocoding (Script 07)

### Nominatim rate limit or timeout

**Symptoms:** `requests.Timeout` or HTTP 429 in logs. Incomplete geocoding.

**Recovery:**
1. Retry logic (added 2026-04-18) handles transient failures with 3 attempts and exponential backoff.
2. Script 07 is incremental — re-running only processes rows where `geom IS NULL`.
3. If Nominatim is consistently slow: check https://operations.osmfoundation.org/policies/nominatim/ for usage policies. The 1-second delay between requests is mandatory.
4. For bulk geocoding (>2000 addresses): expect ~2 hours. Run in a screen/tmux session.

### Geocoded coordinates obviously wrong

**Symptoms:** Facility markers appear in the wrong country or in the ocean.

**Recovery:**
1. Check the `geocode_confidence` field — `low` confidence means city-level or country-level match.
2. Use the review queue (`/api/flagged`) to flag incorrect facilities for manual correction.
3. Manually update coordinates: `UPDATE facility_years SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326) WHERE canonical_facility_id = 'XXX' AND year = YYYY;`
4. The dashboard's "Hide low-confidence geocodes" filter helps users avoid unreliable markers.

---

## Database

### Migration fails

**Symptoms:** `psql` error during `bash db/migrate.sh`.

**Recovery:**
1. Check which migrations have been applied: `psql $DATABASE_URL -c "SELECT * FROM schema_migrations ORDER BY applied_at;"`
2. Fix the failing SQL file in `db/migrations/`.
3. Re-run `bash db/migrate.sh` — it skips already-applied migrations.
4. If a migration partially applied (e.g. created a table but not an index): manually clean up, remove the entry from `schema_migrations`, and re-run.
5. **For production (Supabase):** Always apply locally first: `bash db/migrate.sh`, then `DATABASE_URL="$SUPABASE_URL" bash db/migrate.sh`.

### Connection pool exhaustion

**Symptoms:** HTTP 503 "Service temporarily unavailable" from the API. Log: `PoolError`.

**Recovery:**
1. Check active connections: `SELECT count(*) FROM pg_stat_activity WHERE datname = 'cbm';`
2. Kill idle connections if needed: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'cbm' AND state = 'idle' AND query_start < now() - interval '5 minutes';`
3. Increase pool size via env vars: `DB_POOL_MIN=5 DB_POOL_MAX=20`
4. Check for connection leaks — the `cursor()` context manager should always return connections.

### Geometry lost after reload

**Symptoms:** Map shows fewer markers after running script 06.

**Recovery:**
1. Script 06 preserves `facility_years` geometry automatically during reload.
2. If entity IDs changed (e.g. after re-running dedup), some coordinates can't be matched — check logs for "geom rows not restored" warnings.
3. Re-run script 07 to geocode missing addresses: `python3 scripts/07_geocode.py`
4. Defence facility geometry (`defence_facilities.geom`) is NOT preserved on reload — always re-geocode after reloading.

---

## Deployment (Railway)

### Health check failing

**Symptoms:** Railway shows service as unhealthy. `/health` returns non-200.

**Recovery:**
1. `/health` is a lightweight check (no DB) — if it fails, the process itself is unhealthy.
2. Check Railway logs for startup errors (missing env vars, import errors).
3. Verify `PORT` env var is set in Railway (should be injected automatically).
4. Check `/ready` for DB connectivity issues — it queries `SELECT 1`.

### Deploy succeeds but app crashes

**Symptoms:** Railway deploy shows success but service restarts repeatedly.

**Recovery:**
1. Check for missing env vars: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `REVIEW_API_KEY` must all be set.
2. Check if a new migration needs to be applied to production: `DATABASE_URL="$SUPABASE_URL" bash db/migrate.sh`
3. Run locally to reproduce: `uvicorn api.main:app --port 8000 --reload`
4. Check the Docker build: `docker build -t cbm-test . && docker run -p 8000:8000 --env-file .env cbm-test`

---

## Common Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: psycopg2` | Wrong venv or missing install | `source .venv/bin/activate && pip install -r requirements-api.txt` |
| `psql: command not found` | Homebrew pg17 not in PATH | `export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"` |
| Script 04 skips documents | Output JSON already exists | Use `--force` flag to re-extract |
| China/France/Russia/India missing | Restricted at UN portal | Expected — not a bug |
| `\b` fails in year regex | Underscore is `\w` in filenames | Use `(?<!\d)(20\d{2})(?!\d)` instead |
| `dedup_entities.py` finds 0 facilities | Ran before script 06 | Must run after `06_load_database.py` |
