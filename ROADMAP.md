# CBM Tool — Build Roadmap

## Current state (as of 2026-03)

Scripts 01–05 are complete and producing outputs:
- **1,553** Form A Part 1 facility-year records, **410** unique facilities, **45** countries
- **599** Form G vaccine facility-year records
- Entity registry with canonical IDs and fuzzy name resolution
- Summary statistics JSON

The pipeline covers the two highest-value forms (A1 and G). All 517 public CBM
documents in the UN portal have been processed.

---

## Agreed build sequence

### Stage 1 — Form 0 compliance matrix (immediate, ~1 day)
**Rationale:** Nearly free — data is already in the segmentation manifests
(`forms_present`, `forms_nothing_to_declare`). No new LLM calls.

Output: `data/output/compliance_matrix.csv` and `data/output/form_compliance.csv`
showing, for each country × year × form, whether the submission was:
- **substantive** — form present and not flagged as "nothing to declare"
- **nothing_to_declare** — country declared nothing under this form
- **absent** — form not present in this submission at all

This gives an immediate overview of treaty participation patterns across all forms.

### Stage 2 — Form A Part 2 extraction (~1 week)
**Rationale:** National biological defence programmes. High strategic value alongside
Form A Part 1 facilities. Second-highest priority after A1.

Add `--form-a2` flag to script 04. Design prompt targeting:
- Programme name and responsible agency
- Declared objectives
- Overlap with civilian research

### Stage 3 — Form F extraction (~2–3 days)
**Rationale:** Past offensive/defensive programmes. Many states declare "nothing",
but the substantive submissions (USA, UK, Russia) are analytically significant.
Compact form — straightforward extraction.

### Stage 4 — Form E extraction (~2–3 days)
**Rationale:** National biosafety/biosecurity legislation. Useful for cross-country
policy comparison. Relatively structured and concise.

### Stage 5 — Skip Form B (deferred)
Form B covers disease outbreaks. It is free-text, variable structure, and would
require significant NLP effort for low analytical yield relative to other forms.
Revisit if an outbreak-tracking use case emerges.

### Stage 6 — PostgreSQL + geocoding (~1 week)
**Rationale:** Do this only once the full data schema is known across all forms,
so the relational model can be designed correctly in one pass.

Tasks:
- Design schema: facilities, facility_years, vaccine_facilities, compliance,
  form_a2_programmes, form_f_programmes, form_e_legislation
- Migrate CSVs into PostgreSQL
- Geocode facility addresses (OpenStreetMap Nominatim, batch)
- Add spatial index (PostGIS)

### Stage 7 — REST API + web dashboard (~2–3 weeks)
**Rationale:** Required before ISU outreach — they need a clearly interpretable
interface, not CSV files.

API (FastAPI):
- `/facilities` — filterable by country, year, BSL level, form
- `/compliance` — country participation over time
- `/entities/{id}` — full facility history

Dashboard (lightweight, e.g. Svelte or plain JS + Leaflet):
- World map: countries coloured by submission completeness
- Facility map: clickable BSL-level markers
- Country drill-down: timeline + form compliance grid
- Search: fuzzy facility name search

---

## What comes after (Phase 3 / ISU partnership)

Once the dashboard is live:
- Reach out to ISU (Implementation Support Unit) with a working demo
- Propose data-sharing agreement for the restricted corpus
  (China, France, Russia, India — submitted to ISU but not public)
- Explore Form C (publications) linking to open-access databases
- Consider longitudinal analysis features (facility appearance/disappearance trends)
