# Design: Audit Items 3B + 1I

**Date:** 2026-03-18
**Scope:** Two deferred audit findings from `documentation/AUDIT_FEATURES.md`

---

## 3B — Remove dead defence entity fallback

### Problem
`api/main.py` wraps the `defence_entities` query in a `try/except`, then falls back to a
slow self-join if the table doesn't exist. Migration 010 has been applied to both local
(confirmed via `\dt`) and production (confirmed via Supabase Table Editor — 205 rows).
The fallback is unreachable dead code.

### Change
Replace the try/except + `if not entities` double-path with a single direct query against
`defence_entities`. The self-join is deleted entirely.

**File:** `api/main.py` (~lines 354–394)

**Before (simplified):**
```python
entities = []
try:
    cur.execute("SELECT … FROM defence_entities …", (iso3,))
    rows = cur.fetchall()
    if rows:
        entities = [dict(r) for r in rows]
except Exception:
    pass

if not entities:
    cur.execute("SELECT … FROM defence_facilities … (self-join) …", (iso3,))
    entities = [dict(r) for r in cur.fetchall()]
```

**After:**
```python
cur.execute("SELECT … FROM defence_entities …", (iso3,))
entities = [dict(r) for r in cur.fetchall()]
```

### Note on migration numbering
The existing code comment reads "populated by migration 009". The migration that creates
the `defence_entities` *table* is `010_defence_entity_table.sql`; migration 009 only adds
the `canonical_defence_facility_id` column to `defence_facilities`. The code comment is
wrong and will be deleted along with the fallback.

### Risk
None. Table confirmed present in both environments. Error handling for a missing table
is no longer appropriate; any real DB error will surface normally.

---

## 1I — Move BWC membership from hardcoded JS to API endpoint

### Problem
`dashboard/static/app.js` lines 6–13 hardcode four JS constants:
- `RESTRICTED` — countries with no public CBM data (CHN, FRA, RUS, IND)
- `RESTRICTED_NAMES` — display-name form of the above
- `BWC_SIGNATORIES` — signed but not ratified (EGY, HTI, SOM, SYR)
- `BWC_NON_PARTIES` — not BWC members (TCD, COM, DJI, ERI, ISR, FSM, NAM, SSD, TUV)

These are used in three places: choropleth styling, tooltip text, country classification.
Updating membership facts requires a JS edit + frontend redeploy.

### Change

**API (`api/main.py`):**
- Add a module-level dict `BWC_MEMBERSHIP` mapping ISO3 → status string
  (`"restricted"`, `"signatory"`, `"non_party"`; absence means full member/submitter)
- Add `GET /api/bwc-membership` endpoint returning:
  ```json
  {
    "last_updated": "2025-01",
    "source": "https://www.un.org/disarmament/wmd/bio/",
    "membership": {
      "CHN": "restricted", "FRA": "restricted", "RUS": "restricted", "IND": "restricted",
      "EGY": "signatory", "HTI": "signatory", "SOM": "signatory", "SYR": "signatory",
      "TCD": "non_party", "COM": "non_party", "DJI": "non_party", "ERI": "non_party",
      "ISR": "non_party", "FSM": "non_party", "NAM": "non_party", "SSD": "non_party",
      "TUV": "non_party"
    }
  }
  ```

**Dashboard (`dashboard/static/app.js`):**
- Add `fetchBwcMembership()` async function that calls `/api/bwc-membership` and stores
  the result in a module-level `let bwcMembership = {}`
- Add `fetchBwcMembership()` to the **existing `Promise.all`** in `loadApp()` (the same
  block that fetches stats, countries, etc.) so that it resolves *before*
  `loadChoropleth()` is called — guaranteeing membership data is available when the
  choropleth first renders
- Replace all usages of `RESTRICTED`, `BWC_SIGNATORIES`, `BWC_NON_PARTIES` with
  lookups into `bwcMembership` (e.g. `bwcMembership[iso3] === 'restricted'`)
- **`RESTRICTED_NAMES` is a GeoJSON quirk**, not a membership fact: the Natural Earth
  dataset encodes France with feature `ISO_A3 = '-99'` instead of `'FRA'`. Keep this as
  a small client-side constant with an explanatory comment — it is not served by the
  API because it describes the GeoJSON data format, not political membership.

### Risk
Low. The endpoint is read-only with no DB dependency. If the fetch fails, `bwcMembership`
remains `{}` and the dashboard degrades as follows:
- Signatories / non-parties: rendered as "BWC state party — no public data" (conservative,
  not factually wrong)
- Restricted countries (CHN/FRA/RUS/IND): lose purple restricted styling, render as
  "no public CBM data" (acceptable — purple is a UI convenience, not a factual claim)
Seed `bwcMembership` from the fetch result only; no hardcoded fallback needed.
