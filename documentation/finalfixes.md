# Final Fixes Audit (2026-03-24)

## Status Key
- [ ] Pending
- [x] Complete
- [~] Accepted as-is (no fix needed)

---

## 1. Data Corrections

### Entity Merges (dedup_entities.py)
- [x] FIN_009 / FIN_011 — merged into FIN_002 (THL)
- [x] LTU_003 / LTU_005 — merged (case difference)
- [x] PRT_001 / PRT_013 — merged (case difference)
- [x] SVN_003 / SVN_005 — merged (case difference)
- [x] IRL_002 / IRL_023 — merged (case difference)
- [x] LVA_004 / LVA_005 — merged into LVA_001
- [x] ROU_007 / ROU_011 / ROU_014 / ROU_016 / ROU_017 — merged (Pasteur Institute)
- [x] CZE_005 — Praha row split to new entity CZE_018; Techonín-only after merge with CZE_017
- Applied to both local and Supabase production (2026-03-24)

### Tier-1 Extraction Values
- [~] USA Plum Island 17,643 m² BSL-3 — confirmed matches source PDF
- [~] CIV CEPRIS 1,000 m² BSL-4 — confirmed matches source PDF
- [~] AUS AAHL 11,000 m² BSL-4 — confirmed matches source PDF

### Other Data Issues
- [ ] PRT_003 ghost record — zero confidence, no name, no address (delete if Form A1 blank)
- [ ] 3 defence facilities with NULL names — BGR 2025, TLS 2025 (conf 0.3), UGA 2024
- [ ] 11 legislation records with all-NULL booleans — NZL (2022-2024), DEU, HRV, MYS, NOR, PRT, SAU
- [ ] 28 geocoding failures with addresses — 12 DEU Friedrich-Loeffler-Institut, 10 PRT multi-address

---

## 2. Defensive Code Hardening

### API (api/main.py)
- [x] `api_natural_query` blocks event loop — now async with `asyncio.to_thread()`
- [x] No logging in natural-query exception handler — added `logger.exception()` before HTTPException
- [x] `fetchone()` not null-checked in `/api/stats` — added null guard with 503
- [x] Connection pool exhaustion returns raw 500 — `_getconn()` catches `PoolError` → returns 503
- [x] Scalar subqueries for `country_name` in GeoJSON endpoints — replaced with CTE + LEFT JOIN in all 6 endpoints

### Dashboard (dashboard/)
- [x] `aria-labelledby` references non-existent IDs on tab panels — added matching `id` attrs to tab buttons
- [x] Event listeners on SVG charts accumulate — `_timelineHoverInitialised` flag prevents duplicate listeners; reset on modal close (`hidden.bs.modal`) and on new modal render
- [x] `prefers-reduced-motion` — `startYearPlay()` now checks `matchMedia`; when active, jumps directly to the last data year instead of animating

### Database
- [x] Missing indexes on `document_id` in child tables — migration 019 (local + Supabase)
- [x] Composite index on `form_compliance(document_id, form)` — migration 019
- [x] Defence entity FK not enforced — migration 020 adds FK constraint (local + Supabase)

### Pipeline
- [x] Script 01: no retry on download failures — added 3-attempt exponential backoff to `download_pdf_from_un()`
- [x] Scripts 02-04: incremental skip doesn't validate output completeness:
  - Script 02: now requires both `.txt` AND `_pages.json` to exist
  - Script 03: now requires `manifest.json` inside seg dir (not just any file)
  - Script 04: all 6 form handlers now validate existing JSON is parseable; corrupt files are deleted and re-extracted
- [x] Script 06: geom restoration silently drops rows — now counts actual restored rows via `SELECT count(*) WHERE geom IS NOT NULL` and logs a warning if fewer than expected

---

## 3. Tests & Deployment (lower priority)

- [x] Test coverage for `/api/natural-query` — 10 tests added (503 no key, success w/ organisms, country multi-layer, empty filters, API failure, invalid JSON, code fence strip, 422 validation, rationale clamp, clean_list caps); total 76 tests, 93% coverage
- [x] Dockerfile CMD uses shell form — added `exec` prefix so uvicorn runs as PID 1 and receives SIGTERM directly
- [x] CI/CD lint checks — added ruff (lint + format) as separate job in `.github/workflows/test.yml`; created `ruff.toml` config
- [x] Test coverage thresholds — added pytest-cov to CI with `--cov-fail-under=80`; current coverage 93%

---

## 4. Remaining Roadmap Items (not planned)

- [~] Form B (outbreaks) — intentionally skipped, free-text with no structured schema
- [~] Form C (publications) — intentionally skipped, better sourced from PubMed/Scopus
- [~] ISU partnership + restricted corpus — external dependency, not a code task
