# CBM Tool — Feature & Code Quality Audit

**Date:** 2026-03-17
**Scope:** API endpoints, dashboard UI/UX, database design, extraction pipeline, dead code

---

## 1. UI/UX Issues

### 1A. Vaccine facilities have no entity drill-down (MEDIUM)
**Location:** `dashboard/static/app.js:1327-1339`
Research facilities and defence facilities both have full entity modals (history, yearly records, source links). Vaccine facility items in the sidebar are listed but **clicking does nothing** — there is no `showVaccineEntityModal()` function and no `/api/entity/vaccine/{id}` endpoint. Users can see vaccine facilities listed but cannot examine their history.

### 1B. Timeline dot hover tooltips are broken (LOW-MEDIUM)
**Location:** `dashboard/static/app.js:2954`
The function `initTimelineHover(container)` is defined but **never called**. The timeline SVG is rendered by `renderTimelineTab()` (line 2861), inserted via innerHTML at line 1502, but the hover event listeners are never attached. Timeline dots show `cursor:pointer` but hovering shows nothing.

### 1C. Report card export has hardcoded date (LOW)
**Location:** `dashboard/static/app.js:2839`
The report footer reads `"Data as of 2026-03-17"` — hardcoded. Should derive from `stats.year_max` or a server-side timestamp.

### 1D. Detail content has conflicting inline styles (COSMETIC)
**Location:** `dashboard/index.html:71`
`display:none` appears twice in the same inline style. Functionally overridden by JS, but confusing.

### 1E. Mobile filter panel has no affordance (LOW)
On mobile (app.js:114-121), the filter panel is collapsed by default with no visual hint that it exists or how to reopen it.

### 1F. Search dropdown dismissal is fragile (LOW)
**Location:** `dashboard/static/app.js:1565-1567`
Uses a 200ms `setTimeout` to allow clicking results before blur closes the dropdown. Unreliable on slow devices or with assistive technology.

### 1G. Comparison organism extraction is crude (LOW)
**Location:** `dashboard/static/app.js:2696-2707`
Splits `agents_summary` on whitespace/punctuation, takes words > 4 characters. Produces noise for real scientific text (e.g., "Bacillus anthracis" becomes two separate entries).

### 1H. Year bounds flash incorrect values on initial load (COSMETIC)
**Location:** `dashboard/index.html:176, 180, 183`
HTML hardcodes `min="1988" max="2025"` which are overridden by the API response. During the loading period, the slider shows stale bounds.

### 1I. BWC membership lists hardcoded in JS (LOW)
**Location:** `dashboard/static/app.js:7-12`
`RESTRICTED`, `BWC_SIGNATORIES`, and `BWC_NON_PARTIES` are hardcoded sets. These political facts change over time and are not served by the API.

---

## 2. API / Backend Issues

### 2A. N+1 query pattern in legislation and past-programmes endpoints (MEDIUM)
**Location:** `api/main.py:491-497, 518-521`
Both endpoints fetch all records, then loop over each making individual `SELECT source_url FROM documents WHERE id = %s` queries. Should be a single JOIN.

### 2B. Transparency score year anchor is hardcoded (LOW)
**Location:** `api/main.py:1299`
`current_year = 2026` — should use `datetime.date.today().year` or derive from dataset.

### 2C. `/api/map/compliance/{form}` returns column named `a1_rate` regardless of form (LOW)
**Location:** `api/main.py:853`
The response field is always `a1_rate` even when querying Form G or Form E rates. Should be `rate` or `{form}_rate`.

---

## 3. Database Inefficiencies

### 3A. Migration 004 inconsistently drops CHECK constraints (LOW)
**Location:** `db/migrations/004_relax_containment_checks.sql`
Removed `geocode_confidence` CHECK from `defence_facilities` but not from `facility_years` or `vaccine_facility_years`. Identical values may be accepted in one table but rejected in another.

### 3B. Defence entity table duplicates data available via self-join (LOW)
**Location:** `db/migrations/010_defence_entity_table.sql`
`defence_entities` is a materialized view of data already derivable from `defence_facilities`. The fallback self-join in the API (main.py:362-380) shows this was added as an optimization, but both code paths remain active.

---

## 4. Dead Code / Legacy Code

### 4A. `beautifulsoup4` dependency is unused (LOW)
**Location:** `requirements.txt:3`
Listed but never imported. Legacy from bwcimplementation.org scraping which is now dead.

### 4B. `HARDCODED_URLS` in script 01 are dead (LOW)
**Location:** `scripts/01_catalogue.py:73-79`
Contains bwcimplementation.org URL that returns empty HTML. Code can never succeed.

### 4C. `extract_form_b()` function is defined but never called (LOW)
**Location:** `scripts/04_extract_structured.py:1311-1355`
Dead code — the actual Form B path goes through `process_entry_b`. Also creates its own local rate-limiter state, bypassing the shared one.

### 4D. `FORM_B_MAX_CHARS = None` makes truncation branch unreachable (LOW)
**Location:** `scripts/04_extract_structured.py:103`
The guard `if FORM_B_MAX_CHARS and len(text) > FORM_B_MAX_CHARS` can never be true.

### 4E. `--no-e-truncate` flag is a no-op (LOW)
**Location:** `scripts/04_extract_structured.py:1507-1508`
`FORM_E_MAX_CHARS` is already `None`, so the flag has no effect.

### 4F. `_trendsCapacityData` variable declared but never used (COSMETIC)
**Location:** `dashboard/static/app.js:2532`
Declared but never assigned or read. Actual data stored in `_trendsCapacity`.

### 4G. `latest` variable unused in `loadLegislationTab` (COSMETIC)
**Location:** `dashboard/static/app.js:1364`
`const latest = data[0]` is assigned but never referenced.

### 4H. `sys` imported unnecessarily via `urllib.parse` in script 01 (COSMETIC)
`urllib.parse` is only used by `_url_stem` which only serves the dead hardcoded URL path.

---

## 5. Code Quality / Maintainability

### 5A. Model name hardcoded in 3 separate files (MEDIUM)
- `scripts/02_extract_text.py:97` — `claude-sonnet-4-20250514`
- `scripts/03_segment_forms.py:53` — `claude-sonnet-4-20250514`
- `scripts/04_extract_structured.py:59` — `claude-sonnet-4-20250514`
No single source of truth. Must change all three for model updates.

### 5B. Rate limit delays inconsistent across scripts (LOW)
- Script 04: `RATE_LIMIT_DELAY = 10.0`
- Script 03: `time.sleep(8)` (inline, not a constant)
- `check_form_e_truncation.py`: `RATE_LIMIT_DELAY = 12.0`

### 5C. Near-identical retry logic duplicated 6 times in script 04 (LOW)
**Location:** Lines 683-699, 823-839, 981-997, 1154-1170, 1248-1265, 1417-1434
Each form extractor independently implements the same parse→retry→parse pattern.

### 5D. Entity resolution functions duplicated 3 times in script 05 (LOW)
`resolve_entities`, `resolve_vaccine_entities`, `resolve_defence_facility_entities` are structurally identical except for ID prefix.

### 5E. Docker Compose uses PostGIS 16 while local dev uses pg17 (LOW)
**Location:** `docker-compose.yml:3` vs `CLAUDE.md` / `annual_update.sh`
Version mismatch between Docker and local development.

### 5F. API cost formula embeds specific per-token pricing (LOW)
**Location:** `scripts/04_extract_structured.py:1636, 1660`
Will silently produce wrong costs if pricing changes.

### 5G. `catalogue_from_existing` always sets language to "en" (MEDIUM)
**Location:** `scripts/01_catalogue.py:679`
In `--skip-download` mode, every document is assigned `"language": "en"` regardless of actual language. Affects downstream processing.

---

## 6. Incomplete Features

### 6A. Docker Compose lacks API service definition (LOW)
**Location:** `docker-compose.yml`
Only defines the `db` service. No service for FastAPI.

### 6B. `annual_update.sh` reprocesses everything (MEDIUM)
**Location:** `scripts/annual_update.sh:90-98`
Scripts 02 and 03 run against all documents, not just new ones. Wasteful for incremental updates.

### 6C. Form E `process_entry_e` lacks skip-if-done guard (LOW)
**Location:** `scripts/04_extract_structured.py:1219-1275`
Unlike other form extractors, Form E has no `if out_path.exists(): return skipped` check. The main loop partially mitigates this, but `--single` mode bypasses it.

---

## Priority Summary

| Priority | Finding | Impact |
|----------|---------|--------|
| 1 | Vaccine entity drill-down missing | Users cannot inspect vaccine facility history |
| 2 | N+1 query in legislation/past-programmes | Performance on countries with many years |
| 3 | Timeline hover broken | Reduced data exploration capability |
| 4 | Model name in 3 files | Maintenance burden on model updates |
| 5 | Report card hardcoded date | Stale date in exported reports |
| 6 | Dead beautifulsoup4 dependency | Bloated deployments |
| 7 | annual_update.sh not incremental | Slow annual refresh |
