# BWC Confidence-Building Measures Structured Database Tool
## Technical Architecture Document

**Version:** 0.2
**Date:** March 2026
**Classification:** Unclassified — for internal planning

> **Status note (v0.2):** The extraction pipeline (scripts 01–05) is substantially complete. This document has been updated to reflect what was actually built and to reframe the development phases around remaining work: additional form extraction, database ingestion, and the web interface.

---

## 1. Problem Statement

BWC Confidence-Building Measures represent the only systematic transparency mechanism under the Biological Weapons Convention. Since 1987, states parties have submitted annual declarations covering research facilities, biodefence programmes, disease outbreaks, legislation, historical offensive programmes, and vaccine production facilities.

These submissions exist as unstructured documents—PDFs, scanned papers, and Word files—in up to six UN languages (English, French, Spanish, Russian, Arabic, Chinese), with no standardised machine-readable schema. No tool exists to systematically extract, structure, query, or analyse this corpus. The BWC ISU (staff of ~3) lacks capacity to perform even basic aggregation. The Hamburg Research Group produced manual annual "Readers" (2006–2023) but this is labour-intensive narrative summary, not structured data.

**The goal is to produce a queryable, structured database of all publicly available CBM submissions, enabling longitudinal and cross-national analysis for the first time.**

---

## 2. Data Sources and Access

### 2.1 Primary Source: e-CBM Platform

**URL:** `https://bwc-cbm.un.org` (public interface); `https://bwc-ecbm.unog.ch` (submission portal, restricted)

**Access tiers:**
- **Public:** States parties may opt to make their CBMs publicly accessible. No authentication required for public submissions.
- **States Parties only:** Some submissions are restricted to credentialled states party representatives. These are inaccessible for this project without governmental cooperation.

**Coverage:** All CBMs received since 1987 are on the platform. Early submissions (1987–~2005) are predominantly scanned paper documents. Post-2006 submissions are increasingly born-digital (Word/PDF). Post-2018 submissions may use the e-CBM platform's structured online entry, though many states still submit standalone PDFs.

**Public availability fraction:** Varies by year. The Hamburg 2023 Reader notes that the number of states making CBMs publicly available is volatile. For high-submitting states (US, UK, Germany, Canada, Netherlands, Sweden, Finland, Norway, Australia), public availability is relatively consistent.

**Actual public corpus (as of March 2026):** 517 publicly available submissions were enumerated and downloaded. This is smaller than the initial 1,500–2,500 estimate, which incorrectly assumed per-form separate files; in practice each state submits one consolidated document per year. Notable absences: China, France, Russia, and India do not make their CBMs publicly accessible.

**API access (implemented):** The e-CBM platform exposes a public JSON search API that was used for acquisition — no scraping of HTML was required:
- Enumerate: `POST https://bwc-cbm.un.org/api/search/` with `{"from": N, "size": 50, "search": "", "filter": {"country": []}}`
- Download: `POST https://cms-bwc-cbm.un.org/api/getDocument` with `{"reportId": <int>, "language": null}`

`language: null` returns the original-language PDF; passing an ISO language code returns a machine-translated version (not used — we translate during extraction via Claude instead).

### 2.2 Secondary Sources (for enrichment, not core extraction)

| Source | Data | Access |
|---|---|---|
| Hamburg CBM Readers (2006–2023) | Annual narrative summaries of public CBMs | PDFs, publicly available from `biological-arms-control.org` |
| BWC Implementation Dashboard (`bwcimplementation.org`) | Selected CBM PDFs and supporting documents | Public |
| UNODA CBM statistics page | Annual submission lists, overview tables since 1987 | Public |
| UNODA CBM Guide | Template forms in 6 languages, field definitions | Public, PDF |
| VERTIC NIM Database | National implementing legislation | Public, structured |

### 2.3 Access Strategy

**Public corpus (complete):** All 517 publicly available submissions have been downloaded, catalogued, and processed through the full extraction pipeline. This covers all years (1988–2026), all publicly accessible states (45 countries in the structured output), and all four languages present in the public corpus: English, French, Spanish, and Russian. Arabic and Chinese would require additional OCR tooling, but neither language appears in the public corpus — the states that submit in those languages (principally China) do not make their CBMs public.

**ISU partnership (next priority for access):** Formal cooperation with the ISU remains the key to unlocking restricted submissions. The December 2025 US State Department speech explicitly endorsing AI for CBM analysis creates a political opening. ISU cooperation would unlock the full corpus including restricted submissions, and would give the tool an authoritative institutional home. This is Phase 3 in the revised roadmap below.

---

## 3. Document Characterisation

### 3.1 CBM Form Types and Fields

The current CBM forms (revised at the Seventh Review Conference, 2011) comprise:

#### Form 0 (Cover Page)
- State party name, year, national point of contact
- Per-form declaration: "Nothing to declare" / "Nothing new to declare" / substantive submission
- Year of last declaration if nothing new

#### Form A, Part 1: Research Centres and Laboratories
**Priority: HIGH — most structured, most extractable**
- Facility name
- Responsible organisation
- Location and address
- Source of funding (government/private/other)
- Number of personnel: total, PhD-level, support
- Floor area by containment level (BSL-2, BSL-3, BSL-4) in m²
- Whether facility handles listed agents/toxins
- Pathogens/toxins worked with (sometimes listed, sometimes redacted — cf. US approach of declaring "Select Agents and Toxins" generically)

#### Form A, Part 2: National Biological Defence Programmes
**Priority: HIGH**
- Part 2(i): Declaration of existence (yes/no)
- Part 2(ii): Programme description — objectives, responsible agencies, funding
- Part 2(iii): Facility-level detail — facility name, location, floor areas by containment level, agents worked with, organisational affiliations

#### Form B: Outbreaks of Infectious Diseases
**Priority: MEDIUM**
- Unusual outbreaks of infectious diseases or toxin-related events
- Free-text descriptions with variable structure

#### Form C: Publication of Results / Promotion of Knowledge Use
**Priority: LOW**
- Publication lists (largely duplicating PubMed)
- Promotion of contacts (removed in 2011 as Form D)

#### Form E: Legislation, Regulations, and Other Measures
**Priority: MEDIUM**
- National implementing legislation
- Regulatory measures
- Largely duplicates VERTIC NIM Database but with state self-reporting

#### Form F: Past Activities in Offensive/Defensive BW R&D
**Priority: HIGH (but rarely submitted substantively)**
- Historical programme declarations
- Few states have declared past offensive programmes

#### Form G: Vaccine Production Facilities
**Priority: HIGH — highly structured**
- Facility name, location, ownership
- Licensed products
- Production capacity
- Organisms/toxins used

### 3.2 Document Format Heterogeneity

| Era | Typical Format | OCR Required? | Language Distribution |
|---|---|---|---|
| 1987–1995 | Scanned paper, often poor quality | Yes (complex) | Predominantly EN, FR, RU |
| 1996–2005 | Scanned paper, improving quality | Yes | EN, FR, RU, ES |
| 2006–2017 | Mixed born-digital PDF and scans | Partial | All 6 UN languages |
| 2018–present | Born-digital PDF, Word, or e-CBM online entry | Rarely | All 6 UN languages |

**Actual OCR experience:** Of the 517 public submissions, only 8 required Tesseract OCR (avg chars/page < 100 threshold). LLM post-OCR correction (Claude Sonnet, one call per page) was applied to all 8; this revealed form A Part 1 content in 3 documents that Tesseract alone had rendered unreadable. Arabic and Chinese are absent from the public corpus. Pre-1995 scanned documents from certain states (e.g., Germany 1988–1990) are so severely degraded that regex-based form segmentation is not feasible; LLM segmentation is used as fallback for these 3 documents.

### 3.3 Form Version Discontinuities

Critical consideration: CBM form structure changed at the Third Review Conference (1991, major expansion) and the Seventh Review Conference (2011, revised forms, deletion of Form D). Any schema must accommodate three form eras:

- **Era 1 (1987–1991):** Original 4 forms (A, B, C, D)
- **Era 2 (1992–2011):** Expanded forms (A–G, with current structure taking shape)
- **Era 3 (2012–present):** Revised 2011 forms (A, B, C, E, F, G — D deleted)

The extraction schema should be designed around Era 3 as the canonical structure, with mapping logic for Eras 1 and 2.

---

## 4. Processing Pipeline

### 4.1 Architecture Overview

```
[Document Acquisition] → [Text Extraction + OCR] → [Form Segmentation] → [LLM Structured Extraction] → [Assembly + Entity Resolution] → [Database Ingestion] → [Query Interface]
```

Stages 1–5 are implemented as scripts 01–05 and are complete for Forms A Part 1 and G across the full public corpus. Stages 6–7 (database and query interface) are the primary remaining engineering work.

### 4.2 Stage 1: Document Acquisition (script 01 — complete)

**Tool:** `requests` + UN e-CBM JSON API (not HTML scraping — the public-facing site is fully JS-rendered)

**Process:**
1. Enumerate all publicly available submissions via `POST bwc-cbm.un.org/api/search/`
2. Download each PDF via `POST cms-bwc-cbm.un.org/api/getDocument` with `language: null` (original language)
3. Assign unique identifiers (`{ISO3}_{year}.pdf`), deduplicate amendments with `_2`, `_3` suffixes
4. Build `data/catalogue.json` — one entry per document with country, year, language, file size, download status

**Output:** 517 PDFs in `data/raw_pdfs/`; `data/catalogue.json`

### 4.3 Stage 2: Text Extraction and OCR (script 02 — complete)

**Decision tree (implemented):**

```
Is avg text density ≥ 100 chars/page?
├── YES → pdfplumber extraction (with TSV table blocks where tables detected)
└── NO → Tesseract OCR (language-appropriate pack: eng/fra/spa/rus/ukr/...)
         └── Claude Sonnet post-correction (one API call per page)
             Corrects misread characters and OCR artifacts while
             preserving original language
```

**Notes:**
- `pdfplumber` is preferred over PyMuPDF for its superior table extraction (TSV `[TABLE]...[/TABLE]` blocks)
- Arabic/Chinese OCR (Google Cloud Vision) is plumbed but not exercised — neither language appears in the public corpus
- LLM post-correction was the decisive factor for 3 documents (Uganda, Côte d'Ivoire, Croatia, Bolivia among others) where raw Tesseract output was unreadable to downstream scripts
- 8 of 517 documents required OCR; all 8 were LLM-corrected

**Output:** `data/extracted_text/{id}.txt` and `{id}_pages.json` per document

### 4.4 Stage 3: Form Segmentation (script 03 — complete)

**Purpose:** Split each document's text into per-form files (form_a1.txt, form_b.txt, etc.) for targeted extraction.

**Method:**
- **Primary (98.4% of documents):** Regex matching against the first 5 non-empty lines of each page. Patterns cover all 6 UN languages for forms A1, A2, B, C, E, F, G and Form 0, including pre-2011 descriptive headers, French guillemet variants, Cyrillic headers, numbered-list format (pre-2011 Norwegian style), and the short "Measure X" format
- **Fallback (1.6% of documents):** Claude Sonnet classifies form boundaries from a compact page index (first 200 chars/page). Used for documents where OCR corruption or pre-template structure defeats regex

**Output:** `data/segmented/{id}/` with per-form text files and `manifest.json`

### 4.5 Stage 4: LLM-Powered Structured Extraction (script 04 — complete for Forms A1 and G)

**This is the core value-add of the tool.**

**Implemented forms:**
- **Form A Part 1** (research facilities): 7-field extraction schema per facility, with automatic translation for non-English submissions. Text is chunked at 4,000 chars, split at facility boundaries. 1,553 facility-year records extracted.
- **Form G** (vaccine production facilities): 3-field extraction schema. 599 vaccine facility-year records extracted.

**Not yet extracted:** Forms A Part 2 (biodefence programmes), B (outbreaks), E (legislation), F (historical programmes). These are segmented and ready; extraction prompts and schemas remain to be written.

**Language handling:** All four languages in the public corpus (EN, FR, ES, RU) are processed without a language filter. Claude translates to English during extraction while preserving original-language names in a parallel field. This simultaneous translation-and-extraction approach was the design decision that made multilingual processing tractable without separate translation infrastructure.

**JSON recovery:** Four-strategy parse cascade handles malformed Claude output, including a specific fix for the doubled-closing-quote bug (U+201D followed by ASCII `"`) that appears when source text ends with a curly quotation mark.

**Output:** `data/structured/{id}_form_a1.json` and `{id}_form_g.json` per document

### 4.6 Stage 5: Assembly, Entity Resolution, and Output (script 05 — complete)

**Entity resolution:** Union-Find within each country, matching on facility names using RapidFuzz `token_sort_ratio ≥ 85`. Produces 410 canonical facility entities across 45 countries.

**Output files:**
- `data/output/all_facilities.csv` / `.json` — 1,553 Form A Part 1 facility-year records
- `data/output/all_vaccine_facilities.csv` / `.json` — 599 Form G vaccine facility-year records
- `data/output/entity_registry.json` — 410 canonical facility entities
- `data/output/summary_stats.json` — aggregate statistics

**Validation (implemented so far):** LLM-assigned confidence scores (mean 0.871); provenance tracking (source document ID on every record). Cross-field and temporal consistency checks (Stage 5 in the original plan) remain to be implemented formally, though the entity registry already surfaces year-on-year inconsistencies implicitly.

### 4.7 Stage 6: Database Ingestion (planned)

**Database:** PostgreSQL with PostGIS extension (for facility geolocation).

**Schema design (simplified):**

```
submissions
├── submission_id (PK)
├── state_party (ISO 3166-1 alpha-3)
├── reporting_year
├── submission_date
├── language
├── source_url
├── form_era (1/2/3)
├── public_access (boolean)
└── raw_document_hash (SHA-256)

facilities (from Form A Part 1)
├── facility_id (PK)
├── canonical_facility_id (FK → canonical_facilities)
├── submission_id (FK)
├── facility_name
├── facility_name_original
├── responsible_organisation
├── location_address
├── location_city
├── location_country
├── location_coords (PostGIS POINT, geocoded)
├── funding_source
├── personnel_total
├── personnel_phd
├── personnel_support
├── floor_area_bsl2_m2
├── floor_area_bsl3_m2
├── floor_area_bsl4_m2
├── floor_area_total_m2
├── agents_worked_with (JSONB array)
├── agents_redacted (boolean)
├── extraction_confidence (float)
└── source_page_numbers (integer array)

canonical_facilities (entity-resolved)
├── canonical_facility_id (PK)
├── canonical_name
├── country
├── location_coords
├── first_declared_year
├── last_declared_year
└── facility_type (research/defence/vaccine/other)

biodefence_programmes (from Form A Part 2)
├── programme_id (PK)
├── submission_id (FK)
├── programme_name
├── responsible_agencies (JSONB array)
├── objectives_summary
├── total_funding
├── funding_currency
├── funding_source
└── facilities (JSONB array of facility references)

vaccine_facilities (from Form G)
├── vaccine_facility_id (PK)
├── canonical_facility_id (FK)
├── submission_id (FK)
├── facility_name
├── location
├── ownership
├── licensed_products (JSONB array)
├── production_capacity
├── organisms_used (JSONB array)
└── extraction_confidence (float)

legislation (from Form E)
├── legislation_id (PK)
├── submission_id (FK)
├── legislation_title
├── legislation_type (primary/secondary/guidance)
├── year_enacted
├── scope_description
└── url_if_available

form_zero_declarations
├── declaration_id (PK)
├── submission_id (FK)
├── form_type (A/B/C/E/F/G)
├── declaration_status (substantive/nothing_to_declare/nothing_new)
└── year_of_last_declaration (integer, nullable)
```

**Geocoding:** Facility addresses geocoded via Nominatim (OpenStreetMap) or Google Geocoding API. Store coordinates for map-based visualisations.

---

## 5. Output Products

### 5.1 Structured Database (Core)
- PostgreSQL database as described above
- REST API (FastAPI) for programmatic querying
- Data export: CSV, JSON, GeoJSON

### 5.2 Web Dashboard (Priority)
- **Technology:** React + Mapbox/Leaflet for geospatial visualisation
- **Features:**
  - Global map of declared facilities, colour-coded by containment level
  - Country profiles: submission history, completeness scores, facilities timeline
  - Longitudinal analysis: how declared BSL-3/4 capacity has changed over time, globally and per state
  - Submission compliance tracker: which states submitted, which years, which forms
  - Cross-state comparison: normalised metrics (facilities per capita, BSL-4 area per state)
  - Full-text search across structured and unstructured fields

### 5.3 Analytical Reports (Derived)
- Annual automated "CBM Reader" — structured equivalent of the Hamburg manual Reader
- Gap analysis: which states have never submitted, which have declining participation
- Facility registry: canonical list of all declared BSL-3/4 facilities worldwide, with declared agents
- Vaccine production capacity mapping (Form G data)

### 5.4 Data for Policy Use
- Exportable briefing materials for BWC Working Group and Review Conference delegations
- Structured evidence base for proposals to reform CBM forms
- Longitudinal data supporting arguments for increased ISU analytical capacity

---

## 6. Technology Stack

| Component | Technology | Status | Rationale |
|---|---|---|---|
| Acquisition | Python + UN e-CBM JSON API | **Implemented** | Direct API access; scraping not viable (site is JS-rendered) |
| OCR (Latin/Cyrillic) | Tesseract 5.x | **Implemented** | Open source, sufficient for the 8 documents that required it |
| OCR (Arabic/Chinese) | Google Cloud Vision API | Plumbed, not exercised | Neither language present in public corpus |
| Text extraction (born-digital) | pdfplumber | **Implemented** | Superior table extraction vs. PyMuPDF; TSV table blocks |
| LLM post-OCR correction | Claude Sonnet API | **Implemented** | Page-level correction; decisive for 3 previously unreadable docs |
| Form segmentation | Regex + Claude Sonnet fallback | **Implemented** | 98.4% regex; LLM for 8 edge-case documents |
| LLM structured extraction | Claude Sonnet API | **Implemented (A1, G)** | Simultaneous extraction + translation; Forms A2/B/E/F pending |
| Entity resolution | RapidFuzz (token_sort_ratio ≥ 85) | **Implemented** | Algorithmic within-country matching; 410 canonical facilities |
| Flat-file output | CSV + JSON | **Implemented** | Interim output format pending database ingestion |
| Database | PostgreSQL + PostGIS | **Planned (Phase 2)** | Robust, geospatial-capable, open source |
| Geocoding | Nominatim (OSM) / Google Geocoding API | **Planned (Phase 2)** | Facility address → coordinates for map visualisation |
| API layer | FastAPI (Python) | **Planned (Phase 3)** | Lightweight, fast, auto-documented |
| Frontend | React + Leaflet/Mapbox | **Planned (Phase 3)** | Standard for geospatial web dashboards |
| Hosting | AWS (EC2/RDS) or equivalent | **Planned (Phase 3)** | Scalable, affordable for this data volume |
| CI/CD | GitHub Actions | **Planned (Phase 3)** | Standard |
| Orchestration | Prefect or Airflow (lightweight) | **Planned (Phase 3)** | For annual pipeline re-runs |

---

## 7. Development Phases

The original phased plan—English first, then multilingual; post-2011 first, then historical—was abandoned in practice. The extraction pipeline was built to handle all languages, all eras, and all publicly available documents in a single pass. Phases are now organised around what remains to be built on top of the completed extraction layer.

### Phase 1: Extraction Pipeline — COMPLETE

**What was built:**
- Full acquisition of all 517 publicly available CBM submissions via the UN e-CBM API
- Text extraction pipeline: pdfplumber for born-digital PDFs, Tesseract + LLM correction for scanned documents
- Form segmentation across all form types, all languages (EN, FR, ES, RU), and all eras (1988–2026), with regex primary method (98.4%) and LLM fallback
- Structured extraction for **Form A Part 1** (research facilities) and **Form G** (vaccine production facilities) across the complete corpus in all four languages, with simultaneous translation for non-English submissions
- Entity resolution: canonical facility registry with fuzzy name matching
- Flat-file outputs: CSV and JSON for all extracted data

**Current dataset (March 2026):**
- 1,553 Form A Part 1 facility-year records, 410 unique facilities, 45 countries, 1988–2026
- 599 Form G vaccine facility-year records
- Mean extraction confidence: 0.871

### Phase 2: Complete Form Extraction and Database Ingestion — NEXT

**Scope:** Extract remaining forms from already-segmented text; ingest all structured data into PostgreSQL; add geocoding.

**Deliverables:**

*Form extraction (building on existing script 04 infrastructure):*
- **Form A Part 2** (biodefence programmes) — structured extraction of programme descriptions, responsible agencies, funding; adds the biodefence dimension alongside the facility dimension
- **Form B** (outbreaks) — free-text extraction with structured fields for pathogen, location, date, and scale; analytically valuable for biosurveillance context
- **Form E** (legislation) — extraction of national implementing legislation references; complements the VERTIC NIM database with self-reported state data
- **Form F** (historical offensive programmes) — low yield (few substantive submissions) but high policy salience; extract where declared
- **Form 0 compliance tracking** — extract declaration status per form per year to build a submission compliance matrix across all states and years

*Infrastructure:*
- PostgreSQL + PostGIS database (schema defined in Section 4.7)
- Geocoding of facility addresses via Nominatim (OSM) with Google Geocoding API fallback
- Formal cross-field validation: personnel totals, BSL area consistency, year-on-year change flags
- Human validation of a 10% random sample; per-field accuracy report

### Phase 3: Web Interface and Annual Automation

**Scope:** Build the analyst-facing query interface; automate annual updates.

**Deliverables:**
- REST API (FastAPI) over the PostgreSQL database with endpoints for facilities, submissions, compliance, and statistics
- Web dashboard (React + Leaflet/Mapbox):
  - Global map of declared facilities, colour-coded by containment level
  - Country profiles: submission history, completeness, facilities timeline
  - Longitudinal view: how declared BSL-3/4 capacity and vaccine production has changed over time
  - Submission compliance tracker: which states submitted which forms in which years
  - Full-text search across structured and unstructured fields
- Annual update pipeline: automated ingestion of new CBM submissions (expected each spring following the BWC inter-sessional meeting)
- Data export in CSV, JSON, and GeoJSON formats

### Phase 4: ISU Partnership and Restricted Corpus

**Scope:** Formal engagement with the BWC Implementation Support Unit; processing of restricted submissions if access is granted; production deployment.

**Deliverables:**
- Formal cooperation agreement with the ISU
- Processing of restricted CBM submissions (those not publicly available, representing ~50% of all annual submissions)
- Production deployment with appropriate security for any restricted data
- Annual automated ingestion aligned with ISU workflow
- Public launch, ideally timed for a BWC Working Group session or as a contribution to Tenth Review Conference (2027) preparation

---

## 8. Risks and Mitigations

### 8.1 Data Access
**Risk:** Publicly available CBMs may be an unrepresentative subset (states that are more transparent are also more likely to be compliant).
**Mitigation:** Acknowledged limitation. Phase 4 ISU partnership addresses this for the restricted corpus. The public subset still has substantial analytical value — it covers all major Western states, the full 1988–2026 longitudinal record, and 45 countries.

### 8.2 Extraction Accuracy
**Risk:** LLM extraction introduces errors, particularly for ambiguous or poorly structured submissions.
**Mitigation:** Per-field confidence scores are already assigned at extraction time (mean 0.871 across the corpus). Source document provenance is recorded on every record. Formal human-in-the-loop validation of a 10% random sample is planned for Phase 2, along with cross-field consistency checks. Users can always access the underlying PDF.

### 8.3 Political Sensitivity
**Risk:** Some states may object to systematic analysis of their declarations, particularly if discrepancies with other sources are identified.
**Mitigation:** The tool structures declared data only — it does not perform discrepancy detection or compliance assessment. It is framed as supporting transparency and reducing burden on states parties, consistent with the Working Group on Strengthening the Convention mandate. Data is presented neutrally. This framing should be maintained consistently in stakeholder communications.

### 8.4 Form Heterogeneity
**Risk:** States interpret form fields inconsistently (e.g., some declare all BSL-2 labs, others only BSL-3+; some list specific agents, others redact).  
**Mitigation:** Record what is declared, flag interpretive variability in metadata. The heterogeneity itself is an analytically interesting finding.

### 8.5 Sustainability
**Risk:** Tool becomes unmaintained after initial development.  
**Mitigation:** Design for annual re-run with minimal manual intervention. If ISU partnership is established, they could absorb the pipeline. Alternatively, host with an established civil society organisation (e.g., VERTIC, Johns Hopkins CHS, or CLTR itself).

---

## 9. Comparison with US State Department Vision

The December 2025 State Department speech articulated a vision for AI-assisted CBMs. This tool aligns with but is distinct from that vision:

| State Dept Vision | This Tool |
|---|---|
| AI to help states *complete* CBMs | AI to *analyse* completed CBMs |
| Dashboards presenting CBM information | Yes — structured database + dashboard |
| Increase quantity and quality of submissions | Indirectly — by demonstrating analytical value of data |
| Flag problematic research via CBMs | Not in scope (by design — see Section 8.3) |

The tools are complementary: the State Department vision focuses on the *input* side (helping states submit better CBMs), while this tool focuses on the *output* side (making submitted CBMs analytically useful). A complete ecosystem would include both.

---

## 10. Stakeholder Engagement Strategy

| Stakeholder | Engagement | Timing |
|---|---|---|
| BWC ISU (Geneva) | Informal briefing → formal cooperation proposal | **Now** — extraction pipeline complete; Phase 4 targets formal access |
| UK FCDO BWC delegation | Brief on tool, seek endorsement | **Now** |
| Hamburg Research Group (ZNF) | Collaboration — they have deep domain knowledge and may wish to adopt or co-publish | **Now** |
| US State Dept (ISN/CB) | Align with their AI-for-BWC initiative | Phase 3 (once web interface exists) |
| Johns Hopkins CHS (Shearer/Gronvall) | Academic collaboration, validation | Phase 2–3 |
| VERTIC | Integration with NIM Database (Form E / legislation data) | Phase 2 |
| BWC Working Group | Present as contribution to transparency agenda | Phase 3 (once dashboard available) |
| Tenth Review Conference (2027) | Launch complete tool as side event | Phase 4 |

---

## 11. Licensing and Open Access

The tool should be **fully open source** (MIT or Apache 2.0 licence). The structured database should be published as **open data** (CC-BY 4.0) for all data derived from publicly available CBM submissions. This maximises:

- Credibility with the BWC community
- Uptake by researchers and delegations
- Sustainability (others can fork and maintain)
- Alignment with transparency norms the tool is designed to support
