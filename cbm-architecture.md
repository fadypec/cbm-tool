# BWC Confidence-Building Measures Structured Database Tool
## Technical Architecture Document

**Version:** 0.1 (Scoping)  
**Date:** March 2026  
**Classification:** Unclassified — for internal planning

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
- **Public:** States parties may opt to make their CBMs publicly accessible. Publicly available submissions are downloadable as PDFs from `bwc-cbm.un.org` by clicking the country name on the annual statistics page. No authentication required.
- **States Parties only:** Some submissions are restricted to credentialled states party representatives. These are inaccessible for this project without governmental cooperation.

**Coverage:** All CBMs received since 1987 are on the platform. However, early submissions (1987–~2005) are predominantly scanned paper documents. Post-2006 submissions are increasingly born-digital (Word/PDF). Post-2018 submissions may use the e-CBM platform's structured online entry, though many states still submit standalone PDFs.

**Public availability fraction:** Varies by year. The Hamburg 2023 Reader notes that the number of states making CBMs publicly available is volatile—many states oscillate between public and restricted without explanation. Based on available data, roughly 40–60% of annual submissions are publicly accessible in any given year. For high-submitting states (US, UK, Germany, Canada, Netherlands, Sweden, Finland, Norway, Australia), public availability is relatively consistent.

### 2.2 Secondary Sources (for enrichment, not core extraction)

| Source | Data | Access |
|---|---|---|
| Hamburg CBM Readers (2006–2023) | Annual narrative summaries of public CBMs | PDFs, publicly available from `biological-arms-control.org` |
| BWC Implementation Dashboard (`bwcimplementation.org`) | Selected CBM PDFs and supporting documents | Public |
| UNODA CBM statistics page | Annual submission lists, overview tables since 1987 | Public |
| UNODA CBM Guide | Template forms in 6 languages, field definitions | Public, PDF |
| VERTIC NIM Database | National implementing legislation | Public, structured |

### 2.3 Access Strategy

**Phase 1 (MVP):** Work exclusively with publicly available CBM PDFs downloadable from `bwc-cbm.un.org`. Build a scraper to systematically catalogue and download all publicly accessible submissions. Scope: estimated 1,500–2,500 documents across all years and states.

**Phase 2 (Partnership):** Approach the ISU to discuss formal cooperation. The December 2025 US State Department speech explicitly endorsing AI for CBM analysis creates a political opening. The ISU would likely welcome a tool that reduces burden on states parties, particularly if framed as supporting the Working Group on Strengthening the Convention (2023–2026 mandate). ISU cooperation would unlock the full corpus including restricted submissions.

**Phase 3 (Integration):** If ISU partnership is established, explore integration as an analytical layer on top of the e-CBM platform itself, with the ISU's endorsement.

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
[Document Acquisition] → [Classification] → [OCR/Text Extraction] → [LLM Structured Extraction] → [Validation] → [Database Ingestion] → [Query Interface]
```

### 4.2 Stage 1: Document Acquisition

**Tool:** Custom Python scraper using `requests` + `BeautifulSoup`

**Process:**
1. Scrape `bwc-cbm.un.org` statistics pages for all years
2. Identify publicly available submissions (clickable country names)
3. Download all available PDFs
4. Catalogue: assign unique identifier per document (`{country_iso3}_{year}_{form}.pdf`)
5. Store raw documents in cloud storage (S3 or equivalent)
6. Build metadata index: country, year, file size, page count, detected language (preliminary)

**Output:** Raw document corpus + metadata index (JSON/CSV)

**Estimated corpus size:** ~1,500–2,500 PDFs. At typical 10–200 pages each, total page count likely 20,000–80,000 pages. The US submissions alone run to ~190 pages/year.

### 4.3 Stage 2: Document Classification

**Purpose:** Determine per document: (a) which CBM forms are present, (b) language, (c) form era, (d) whether content is substantive or "nothing to declare".

**Tool:** LLM classification (Claude Sonnet via API) on first 2–3 pages + table of contents where present.

**Process:**
1. Extract first 3 pages of text (OCR if necessary — see Stage 3)
2. Submit to LLM with classification prompt:
   - Identify language(s) present
   - Identify which CBM forms are included
   - Classify form era (pre-1992, 1992–2011, post-2011)
   - Flag "nothing to declare"/"nothing new to declare" forms
3. Store classification metadata

**Fallback:** For documents where first pages are uninformative (e.g., cover letters only), expand to full document scan.

### 4.4 Stage 3: OCR and Text Extraction

**Decision tree:**

```
Is document born-digital (text-selectable PDF)?
├── YES → Extract text directly (PyMuPDF / pdfplumber)
└── NO → Is document in Latin script?
    ├── YES → Tesseract OCR (high confidence for EN/FR/ES)
    └── NO → Is document in Cyrillic?
        ├── YES → Tesseract OCR with rus/ukr language packs
        └── NO → Is document in Arabic or Chinese?
            └── YES → Cloud Vision API (Google) or Azure Document Intelligence
                       (superior CJK and Arabic performance)
```

**Tools:**
- **Born-digital extraction:** `PyMuPDF` (fitz) or `pdfplumber` (Python). Preference for `pdfplumber` for table extraction.
- **Latin/Cyrillic OCR:** Tesseract 5.x with appropriate language packs. Adequate for post-2000 scanned documents. For pre-1995 poor-quality scans, consider Google Cloud Vision API.
- **Arabic/Chinese OCR:** Google Cloud Vision API or Azure AI Document Intelligence. Tesseract's CJK and Arabic performance is insufficient for this use case.
- **Table extraction:** `pdfplumber` for born-digital; Camelot or `img2table` for scanned documents. Form A Part 1 and Form G are substantially tabular.

**LLM post-correction:** After OCR, submit extracted text to Claude with the prompt: "This is OCR output from a BWC Confidence-Building Measure submission in [language]. Correct any obvious OCR errors, preserving the original language. Flag low-confidence sections." This is particularly valuable for older scanned documents where OCR confidence is low.

**Output:** Clean text per document, segmented by form where possible, with language tags and OCR confidence metadata.

### 4.5 Stage 4: LLM-Powered Structured Extraction

**This is the core value-add of the tool.**

**Approach:** Form-specific extraction prompts submitted to Claude Sonnet API with structured JSON output.

**Process per form:**

1. Segment document text by CBM form (using form headers/numbers as delimiters)
2. For each form section, submit to LLM with:
   - The target extraction schema (JSON schema)
   - The template form in the detected language (as reference for field mapping)
   - The extracted text
   - Instruction to extract all structured fields, translating to English where the source is non-English
3. Parse returned JSON
4. Run validation checks (see Stage 5)

**Example extraction prompt (Form A, Part 1):**

```
You are extracting structured data from a BWC Confidence-Building 
Measure submission (Form A, Part 1: Research Centres and Laboratories).

Extract ALL facilities listed, returning a JSON array. For each 
facility, extract:
{
  "facility_name": string,
  "facility_name_original": string (if non-English, preserve original),
  "responsible_organisation": string,
  "location": {
    "address": string,
    "city": string,
    "country": string (ISO 3166-1 alpha-3)
  },
  "funding_source": string,
  "personnel": {
    "total": integer or null,
    "phd_level": integer or null,
    "support": integer or null
  },
  "floor_area_m2": {
    "bsl2": number or null,
    "bsl3": number or null,
    "bsl4": number or null,
    "total": number or null
  },
  "agents_worked_with": [string] or "redacted" or "not declared",
  "additional_notes": string or null
}

If a field is not present in the source, use null. If information is 
ambiguous, extract your best interpretation and flag in additional_notes.

Source text follows:
---
[EXTRACTED TEXT]
```

**Language handling:** The prompt instructs translation to English for standardised fields (facility name, location, agents) while preserving originals. For non-English submissions, the LLM performs simultaneous translation and extraction — this is the key capability that makes the tool feasible where it previously was not.

**Cost estimation:** At ~$3/MTok input, $15/MTok output for Claude Sonnet, processing 50,000 pages at ~500 tokens/page = 25M input tokens ≈ $75 input. Structured output likely 2–5M tokens ≈ $30–75. Total API cost for full corpus extraction: **~$100–200.** Very modest. The expensive part is human validation, not compute.

### 4.6 Stage 5: Validation

**Automated validation:**
1. **Schema validation:** Does the returned JSON conform to the target schema? (JSON Schema validation)
2. **Cross-field consistency:** Do personnel totals sum correctly? Is BSL-4 floor area ≤ total floor area?
3. **Temporal consistency:** For facilities appearing in consecutive years, flag dramatic changes (e.g., BSL-4 area doubling year-on-year) for human review
4. **Entity resolution:** Fuzzy-match facility names across years and across states' submissions to build a canonical facility registry. LLM-assisted: "Are 'Swedish Defence Research Agency (FOI)' and 'Totalförsvarets forskningsinstitut (FOI)' the same entity?" → Yes, confidence 0.99
5. **Completeness scoring:** Per submission, what fraction of expected fields were populated vs. null?

**Human-in-the-loop validation:**
- For Phase 1/MVP, manually validate a random 10% sample of extractions against source PDFs
- Calculate per-field extraction accuracy
- Identify systematic error patterns (e.g., misattribution of BSL levels, confusion between m² and ft²)
- Iterate extraction prompts based on error analysis

**Output:** Validated structured records with per-field confidence scores and provenance links back to source page numbers.

### 4.7 Stage 6: Database Ingestion

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

| Component | Technology | Rationale |
|---|---|---|
| Scraping/acquisition | Python (requests, BeautifulSoup) | Standard, reliable |
| OCR (Latin/Cyrillic) | Tesseract 5.x | Open source, sufficient for most documents |
| OCR (Arabic/Chinese) | Google Cloud Vision API | Superior CJK/Arabic performance |
| Text extraction (born-digital) | pdfplumber, PyMuPDF | Best-in-class PDF text/table extraction |
| LLM extraction | Claude Sonnet API | Cost-effective structured extraction with strong multilingual capability |
| LLM post-OCR correction | Claude Sonnet API | Same pipeline |
| Entity resolution | Claude Sonnet API + fuzzy matching (RapidFuzz) | LLM for semantic matching; algorithmic for scale |
| Database | PostgreSQL + PostGIS | Robust, geospatial-capable, open source |
| API layer | FastAPI (Python) | Lightweight, fast, auto-documented |
| Frontend | React + Leaflet/Mapbox | Standard for geospatial web dashboards |
| Geocoding | Nominatim (OSM) or Google Geocoding API | Facility address → coordinates |
| Hosting | AWS (EC2/RDS) or equivalent | Scalable, affordable for this data volume |
| CI/CD | GitHub Actions | Standard |
| Orchestration | Prefect or Airflow (lightweight) | For annual pipeline re-runs |

---

## 7. Development Phases and Timeline

### Phase 1: MVP (Weeks 1–6)

**Scope:** Forms A (Part 1) and G only. English-language public submissions only. 2012–present (post-revision forms only).

**Deliverables:**
- Document scraper and catalogue
- Extraction pipeline for Forms A1 and G
- PostgreSQL database with validated data
- Basic web dashboard with facility map and country profiles
- Validation report (accuracy metrics on 10% sample)

**Estimated corpus:** ~400–600 documents (English public submissions, 2012–2024, ~50 states/year at ~50% public availability, but many are single-country multi-form documents).

### Phase 2: Multilingual Expansion (Weeks 7–12)

**Scope:** Extend to all 6 UN languages. Add Forms A (Part 2), E, and Form 0.

**Deliverables:**
- Multilingual OCR pipeline
- Translation-integrated extraction
- Biodefence programme database (Form A Part 2)
- Legislation database (Form E)
- Submission compliance dashboard (Form 0 data)
- Entity resolution across languages

### Phase 3: Historical Corpus (Weeks 13–20)

**Scope:** Extend to 1987–2011 submissions. Handle form version discontinuities.

**Deliverables:**
- OCR pipeline for older scanned documents
- Era 1 and Era 2 form mapping logic
- Complete longitudinal database (1987–present)
- Longitudinal analysis dashboard
- Form B (outbreaks) and Form F (historical programmes) extraction

### Phase 4: Partnership and Deployment (Weeks 20+)

**Scope:** ISU engagement, restricted corpus access, production deployment.

**Deliverables:**
- Formal cooperation agreement with ISU
- Processing of restricted submissions (if access granted)
- Production web deployment
- Annual automated ingestion pipeline for new CBM submissions
- Public launch, ideally timed for BWC Working Group session or Tenth Review Conference preparation

---

## 8. Risks and Mitigations

### 8.1 Data Access
**Risk:** Publicly available CBMs may be an unrepresentative subset (states that are more transparent are also more likely to be compliant).  
**Mitigation:** Acknowledged limitation. Phase 2 ISU partnership addresses this. The public subset still has substantial analytical value — it includes all major Western states plus many others.

### 8.2 Extraction Accuracy
**Risk:** LLM extraction introduces errors, particularly for ambiguous or poorly structured submissions.  
**Mitigation:** Per-field confidence scoring, human-in-the-loop validation on sample, provenance links to source pages. Users can always access the underlying PDF.

### 8.3 Political Sensitivity
**Risk:** Some states may object to systematic analysis of their declarations, particularly if discrepancies with other sources are identified.  
**Mitigation:** Phase 1 focuses purely on structuring declared data, NOT on discrepancy detection or compliance assessment. The tool is framed as supporting transparency and reducing burden — consistent with the Working Group mandate. Avoid naming and shaming. Present data neutrally.

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
| Flag problematic research via CBMs | Not in scope (Phase 1) |

The tools are complementary: the State Department vision focuses on the *input* side (helping states submit better CBMs), while this tool focuses on the *output* side (making submitted CBMs analytically useful). A complete ecosystem would include both.

---

## 10. Stakeholder Engagement Strategy

| Stakeholder | Engagement | Timing |
|---|---|---|
| BWC ISU (Geneva) | Informal briefing → formal cooperation proposal | Phase 1 complete |
| UK FCDO BWC delegation | Brief on tool, seek endorsement | Phase 1 |
| US State Dept (ISN/CB) | Align with their AI-for-BWC initiative | Phase 2 |
| Hamburg Research Group (ZNF) | Collaboration — they have deep domain knowledge | Phase 1 |
| Johns Hopkins CHS (Shearer/Gronvall) | Academic collaboration, validation | Phase 2 |
| VERTIC | Integration with NIM Database (legislation) | Phase 2 |
| BWC Working Group | Present as contribution to transparency agenda | Phase 3–4 |
| Tenth Review Conference (2027) | Launch complete tool as side event | Phase 4 |

---

## 11. Licensing and Open Access

The tool should be **fully open source** (MIT or Apache 2.0 licence). The structured database should be published as **open data** (CC-BY 4.0) for all data derived from publicly available CBM submissions. This maximises:

- Credibility with the BWC community
- Uptake by researchers and delegations
- Sustainability (others can fork and maintain)
- Alignment with transparency norms the tool is designed to support
