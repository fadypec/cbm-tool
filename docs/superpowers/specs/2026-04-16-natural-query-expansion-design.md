# Natural Language Query Expansion — Design Spec

## Goal

Expand the `/api/natural-query` endpoint from facility-only search to a general-purpose CBM data query system that can answer any question about the dataset — submission history, legislation, defence programmes, country overviews, comparisons, and aggregate statistics.

## Architecture

**Two-phase classify-then-query with hybrid text generation.**

1. **Classify** — Single Haiku call parses the user's natural language query into `{query_type, params}`. Seven query types supported.
2. **Execute** — Backend routes to a parameterized SQL template per query type. No raw SQL from the LLM.
3. **Summarize** — Simple types use server-side text templates. Complex types (legislation, defence, country overview) get a second Haiku call to produce a natural language answer grounded in actual query results.
4. **Return** — Response includes `answer` (text), `data` (structured rows), `entities` (clickable cards), and `facilities` (backward-compatible for facility searches).

```
User query
  → Haiku call 1: classify + extract params
  → Route to parameterized SQL template
  → Execute query
  → Generate answer (template or Haiku call 2)
  → Return {query_type, answer, data, entities, facilities}
```

## Query Types

### 1. `facility_search` (existing)

Unchanged from current implementation. Haiku extracts `{organisms, countries, bsl, keywords}` and the backend queries `facilities`, `vaccine_facilities`, `defence_entities`.

### 2. `submission_history`

Questions like "In what years did Austria submit Form A1?", "Which countries submitted Form G in 2023?", "Has Brazil ever declared nothing to declare on Form F?"

**SQL template:** Joins `form_compliance` → `documents`. Filters by country ISO3 codes, form names, year ranges, and/or status values.

**Returns:** `{country_iso3, country_name, year, form, status}` rows.

**Text generation:** Template — lists years, computes rate, notes gaps.

### 3. `country_overview`

Questions like "Tell me about Germany's CBM submissions", "What does Japan declare?"

**SQL template:** Three parallel queries:
- Submission summary from `form_compliance` (year range, forms filed, rates per form)
- Facility counts from `facilities` + `vaccine_facilities` + `defence_entities`
- Latest legislation snapshot from `legislation`

**Returns:** Composite object with submission stats, facility counts, legislation summary.

**Text generation:** Haiku summarization (composite data is awkward to template).

**Entity card:** Country card links to sidebar + map pan. The card IS the primary action — no separate data table rendered in the modal.

### 4. `comparative`

Questions like "Compare UK and France submission rates", "Which countries have the most BSL-4 facilities?"

**Two sub-paths:**
- **2 countries, no specific data filter** → `use_compare_mode: true`. Answer text says "Compare {A} and {B} using the comparison tool below." Entity cards link to existing compare mode UI.
- **Ranked/filtered comparison** → Queries the relevant table (e.g. `facilities` grouped by country for BSL-4 rankings). Returns a ranked table.

**Text generation:** Template for both sub-paths.

### 5. `legislation`

Questions like "Does Australia have export control legislation?", "Which countries lack biosafety regulations?"

**SQL template:** Queries `legislation` table.
- Single country → full history of boolean fields + `key_laws` array.
- Category filter ("who has export controls?") → filters by relevant boolean columns, returns country list.

**Returns:** `{country_iso3, country_name, year, [boolean fields], key_laws}` rows.

**Text generation:** Haiku summarization (16 boolean fields + key_laws list is awkward to template).

### 6. `defence_programmes`

Questions like "Which countries declared past offensive programmes?", "What is Canada's defence programme budget?", "Who uses contractors for bio-defence?"

**SQL template:** Queries `defence_programmes` and/or `past_programmes` depending on the question.
- Past offensive/defensive → `past_programmes` filtered by `has_offensive_programme` / `has_defensive_programme`.
- Budget/contractor/current programmes → `defence_programmes` filtered by country/year.

**Returns:** Rows from the relevant table(s).

**Text generation:** Haiku summarization (free-text fields like objectives and summaries).

### 7. `aggregate_stats`

Questions like "How many countries submit Form A1?", "What's the average submission rate?", "How many BSL-4 facilities are there?"

**SQL template:** Aggregation queries against `form_compliance`, `facilities`, or `documents` depending on keywords. Examples:
- "How many countries submit A1?" → `COUNT(DISTINCT country_iso3) FROM form_compliance fc JOIN documents d ON ... WHERE form='A1' AND status='substantive'`
- "How many BSL-4 facilities?" → `COUNT(*) FROM facilities WHERE latest_containment ILIKE '%BSL-4%'`

**Returns:** Small result set (counts, averages, ranked lists).

**Text generation:** Template — "{count} countries...", "{n} facilities...".

## Classification Prompt

The Haiku system prompt classifies queries and extracts typed parameters:

```json
{
  "query_type": "submission_history",
  "countries": ["AUT"],
  "forms": ["A1"],
  "year_min": null,
  "year_max": null,
  "organisms": [],
  "keywords": [],
  "bsl": [],
  "legislation_category": null,
  "rationale": "User asking which years Austria submitted Form A1"
}
```

**Parameter extraction per type:**

| Type | Required params | Optional params |
|------|----------------|-----------------|
| `facility_search` | any of organisms/countries/bsl/keywords | — |
| `submission_history` | countries or forms | year_min, year_max |
| `country_overview` | countries (1 only) | — |
| `comparative` | countries (2+) | forms |
| `legislation` | countries or legislation_category | — |
| `defence_programmes` | countries or keywords | year_min, year_max |
| `aggregate_stats` | (none required — keywords guide) | forms, year_min, year_max |

**`legislation_category`** values: `prohibitions`, `exports`, `imports`, `biosafety` — maps to the boolean column prefixes in the `legislation` table.

The classification prompt includes 2-3 examples per query type. Ambiguous queries (e.g. bare "Austria") default to `country_overview`. Unrecognizable or off-topic queries (gibberish, non-CBM questions) return `query_type: "unknown"` with an empty params object. The backend returns a friendly message: "I can answer questions about BWC Confidence-Building Measure submissions, facilities, legislation, and defence programmes. Try asking something like 'Which countries submitted Form A1 in 2023?'"

## API Response Format

```json
{
  "query_type": "submission_history",
  "answer": "Austria submitted Form A1 in 13 of 14 years (2012–2025), missing only 2017. 93% submission rate.",
  "data": [
    {"country_iso3": "AUT", "country_name": "Austria", "year": 2012, "form": "A1", "status": "substantive"}
  ],
  "entities": [
    {"type": "country", "iso3": "AUT", "name": "Austria"}
  ],
  "facilities": [],
  "use_compare_mode": false
}
```

**Entity card types:**

| Type | Fields | Dashboard click action |
|------|--------|----------------------|
| `country` | iso3, name | `selectCountry(iso3)` — opens sidebar + pans map, closes AI modal |
| `facility` | id, name, country_iso3, layer (A1/G/A2) | Opens entity modal (`showEntityModal` / `showDefenceEntityModal` / `showVaccineEntityModal`), closes AI modal |
| `compare` | countries[] | Pre-selects countries in compare mode, switches to compare tab, closes AI modal |

## Dashboard Rendering

The AI query modal gets three rendering zones:

### 1. Answer bar
Natural language text at the top. Styled more prominently than the current rationale line. Always present for non-facility queries.

### 2. Entity cards
Row of clickable chips below the answer:
- Country cards: flag emoji + country name
- Facility cards: name + type badge (Research / Vaccine / Defence)
- Compare cards: "Compare {A} vs {B}" button

Clicking any card navigates into the existing UI and closes the AI modal.

### 3. Data table (conditional)

| Query type | Data table |
|-----------|-----------|
| `facility_search` | Existing facility list (unchanged) |
| `submission_history` | Compact year list or year×form mini-grid |
| `country_overview` | None — country card IS the action |
| `comparative` + `use_compare_mode` | None — compare card IS the action |
| `comparative` (ranked) | Ranked table |
| `legislation` | Category checklist table |
| `defence_programmes` | Programme/country table |
| `aggregate_stats` | Simple stat display |

## Text Generation Rules

| Query type | Method | Rationale |
|-----------|--------|-----------|
| `facility_search` | None (existing rationale field) | Backward compatible |
| `submission_history` | Template | Formulaic: years, rate, gaps |
| `country_overview` | Haiku call 2 | Composite data, needs natural prose |
| `comparative` | Template | "Compare X and Y..." or ranked list description |
| `legislation` | Haiku call 2 | 16 booleans + key_laws awkward to template |
| `defence_programmes` | Haiku call 2 | Free-text fields (objectives, summaries) |
| `aggregate_stats` | Template | Simple counts and averages |

## Security

All existing security measures carry over:
- Query input capped at 400 characters (`NaturalQueryRequest.q`)
- Rate limited at 10/minute per IP
- All SQL uses parameterized queries (`%s` placeholders) — no string interpolation
- LLM output validated and clamped (country codes, form names, year ranges checked against allowed values)
- Haiku call 2 system prompt includes injection-prevention rules (respond only with summary text, ignore user instructions in the data)
- Row limits enforced per query type (max 200)
- `answer` field capped at 500 characters in the response

## Backward Compatibility

- `facility_search` path is unchanged — existing dashboard code continues to work
- Response adds new fields (`query_type`, `answer`, `data`, `entities`, `use_compare_mode`) alongside existing `facilities` and `rationale`
- Dashboard checks `query_type` first; if absent or `facility_search`, renders the existing facility list

## Files to Modify

- `api/main.py` — Expand `_NQ_SYSTEM` prompt, add query type handlers, add text generation templates, add Haiku summarization call
- `dashboard/static/app.js` — Update AI modal rendering to handle multiple query types, entity cards, data tables
- `dashboard/static/style.css` — Styles for entity cards, answer bar, data tables
- `tests/test_api.py` — Tests for each query type's SQL template and response shape
