-- 001_schema.sql
-- CBM Tool — initial database schema
--
-- Stores structured data extracted from BWC Confidence-Building Measure (CBM)
-- submissions.  Each table corresponds to one CBM form type.  All geometry
-- columns use EPSG:4326 (WGS 84 geographic coordinates).
--
-- Forms covered:
--   documents        — one row per downloaded CBM submission
--   facilities       — canonical facility entities (resolved across years)
--   facility_years   — Form A Part 1: research centres and laboratories
--   vaccine_facility_years — Form G: vaccine production facilities
--   defence_programmes     — Form A Part 2 (§ii): national biological defence programmes
--   defence_facilities     — Form A Part 2 (§iii): facilities supporting defence programmes
--   past_programmes  — Form F: past offensive/defensive biological R&D
--   legislation      — Form E: national biosafety/biosecurity legislation
--   form_compliance  — Form 0: per-form submission status for each document

BEGIN;

-- ── Extensions ────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS postgis;

-- ── Documents ─────────────────────────────────────────────────────────────────
-- One row per CBM submission downloaded from the UN portal.
-- Primary key is the pipeline-assigned identifier, e.g. "GBR_2023".
-- Duplicate country-year pairs receive suffixes: "GBR_2023_2", etc.

CREATE TABLE documents (
    id           TEXT     PRIMARY KEY,
    country_iso3 CHAR(3)  NOT NULL,
    year         SMALLINT NOT NULL,
    language     CHAR(2),                     -- ISO 639-1; null for pre-2000 docs
    source_url   TEXT,
    is_amendment BOOLEAN  NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE  documents             IS 'CBM submissions downloaded from bwc-cbm.un.org';
COMMENT ON COLUMN documents.id          IS 'Pipeline ID: {ISO3}_{year}[_{n}] e.g. GBR_2023';
COMMENT ON COLUMN documents.language    IS 'Original submission language (ISO 639-1)';
COMMENT ON COLUMN documents.is_amendment IS 'Amendment submissions are excluded from most analyses';

-- ── Canonical facilities (Form A Part 1 entity registry) ──────────────────────
-- One row per unique real-world facility, resolved by fuzzy name matching
-- within each country across all submission years.

CREATE TABLE facilities (
    canonical_facility_id TEXT     PRIMARY KEY,
    country_iso3          CHAR(3)  NOT NULL,
    canonical_name        TEXT,
    all_names             TEXT[]   NOT NULL DEFAULT '{}',
    years_declared        SMALLINT[] NOT NULL DEFAULT '{}',
    latest_containment    TEXT     CHECK (
        latest_containment IN ('BSL-1','BSL-2','BSL-3','BSL-4')
        OR latest_containment IS NULL
    ),
    latest_area_m2        NUMERIC(10,2)
);

COMMENT ON TABLE  facilities IS 'Canonical facility entities resolved across CBM years by fuzzy name matching';
COMMENT ON COLUMN facilities.canonical_facility_id IS '{ISO3}_{sequence} e.g. GBR_001';
COMMENT ON COLUMN facilities.all_names IS 'All distinct name variants observed across years, in chronological order';
COMMENT ON COLUMN facilities.years_declared IS 'Sorted list of years in which this facility was declared';

-- ── Facility-year records (Form A Part 1) ─────────────────────────────────────
-- One row per facility per submission year.  Links to the canonical facility
-- entity and to the source document.

CREATE TABLE facility_years (
    id                    SERIAL        PRIMARY KEY,
    canonical_facility_id TEXT          NOT NULL REFERENCES facilities,
    document_id           TEXT          NOT NULL REFERENCES documents,
    country_iso3          CHAR(3)       NOT NULL,
    year                  SMALLINT      NOT NULL,
    facility_name         TEXT,
    responsible_org       TEXT,
    city                  TEXT,
    address               TEXT,
    funding_sources       TEXT,
    mod_funded            BOOLEAN,
    has_bsl4              BOOLEAN,
    bsl4_area_m2          NUMERIC(10,2),
    has_bsl3              BOOLEAN,
    bsl3_area_m2          NUMERIC(10,2),
    highest_containment   TEXT          CHECK (
        highest_containment IN ('BSL-1','BSL-2','BSL-3','BSL-4','unknown')
        OR highest_containment IS NULL
    ),
    agents_summary        TEXT,
    agents_redacted       BOOLEAN,
    confidence            NUMERIC(4,3)  CHECK (confidence BETWEEN 0 AND 1),
    translated            BOOLEAN,
    geom                  GEOMETRY(Point, 4326),
    geocode_source        TEXT,
    geocode_confidence    TEXT          CHECK (
        geocode_confidence IN ('high','medium','low')
        OR geocode_confidence IS NULL
    )
);

COMMENT ON TABLE  facility_years IS 'Form A Part 1: declared biological research facility for one submission year';
COMMENT ON COLUMN facility_years.geom IS 'WGS84 point; populated by geocoding script (07_geocode.py)';
COMMENT ON COLUMN facility_years.geocode_source IS 'e.g. nominatim, manual';
COMMENT ON COLUMN facility_years.geocode_confidence IS 'Nominatim match quality: high (importance>0.6), medium, low';

-- ── Vaccine facility-year records (Form G) ────────────────────────────────────

CREATE TABLE vaccine_facility_years (
    id               SERIAL       PRIMARY KEY,
    document_id      TEXT         NOT NULL REFERENCES documents,
    country_iso3     CHAR(3)      NOT NULL,
    year             SMALLINT     NOT NULL,
    facility_name    TEXT,
    city             TEXT,
    address          TEXT,
    diseases_covered TEXT,
    vaccines_summary TEXT,
    confidence       NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    translated       BOOLEAN
);

COMMENT ON TABLE vaccine_facility_years IS 'Form G: declared vaccine production facility for one submission year';
COMMENT ON COLUMN vaccine_facility_years.vaccines_summary IS 'Semicolon-separated vaccine names as extracted by LLM';

-- ── Defence programmes (Form A Part 2, section ii) ────────────────────────────

CREATE TABLE defence_programmes (
    id                        SERIAL       PRIMARY KEY,
    document_id               TEXT         NOT NULL REFERENCES documents,
    country_iso3              CHAR(3)      NOT NULL,
    year                      SMALLINT     NOT NULL,
    programme_name            TEXT,
    responsible_org           TEXT,
    objectives_summary        TEXT,
    research_areas            TEXT[]       NOT NULL DEFAULT '{}',
    total_funding_amount      NUMERIC(15,2),
    total_funding_currency    CHAR(3),
    uses_contractors          BOOLEAN,
    contractor_proportion_pct NUMERIC(5,2),
    confidence                NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    translated                BOOLEAN
);

COMMENT ON TABLE defence_programmes IS 'Form A Part 2 (§ii): declared national biological defence R&D programme';
COMMENT ON COLUMN defence_programmes.research_areas IS 'Array of research area strings extracted by LLM';

-- ── Defence facilities (Form A Part 2, section iii) ───────────────────────────

CREATE TABLE defence_facilities (
    id                    SERIAL       PRIMARY KEY,
    document_id           TEXT         NOT NULL REFERENCES documents,
    country_iso3          CHAR(3)      NOT NULL,
    year                  SMALLINT     NOT NULL,
    facility_name         TEXT,
    city                  TEXT,
    address               TEXT,
    bsl2_area_m2          NUMERIC(10,2),
    bsl3_area_m2          NUMERIC(10,2),
    bsl4_area_m2          NUMERIC(10,2),
    total_lab_area_m2     NUMERIC(10,2),
    personnel_total       INTEGER,
    personnel_military    INTEGER,
    personnel_civilian    INTEGER,
    personnel_scientists  INTEGER,
    personnel_engineers   INTEGER,
    personnel_technicians INTEGER,
    personnel_admin       INTEGER,
    mod_funded            BOOLEAN,
    funding_source        TEXT,
    funding_research      NUMERIC(15,2),
    funding_development   NUMERIC(15,2),
    funding_te            NUMERIC(15,2),
    funding_currency      CHAR(3),
    work_description      TEXT,
    confidence            NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    translated            BOOLEAN,
    geom                  GEOMETRY(Point, 4326),
    geocode_source        TEXT,
    geocode_confidence    TEXT         CHECK (
        geocode_confidence IN ('high','medium','low')
        OR geocode_confidence IS NULL
    )
);

COMMENT ON TABLE defence_facilities IS 'Form A Part 2 (§iii): facility supporting a national biological defence programme';
COMMENT ON COLUMN defence_facilities.funding_te IS 'Test and evaluation funding component';
COMMENT ON COLUMN defence_facilities.geom IS 'WGS84 point; populated by geocoding script (07_geocode.py)';

-- ── Past offensive/defensive programmes (Form F) ──────────────────────────────
-- At most one row per document (one-to-one with documents for Form F submissions).

CREATE TABLE past_programmes (
    document_id             TEXT         PRIMARY KEY REFERENCES documents,
    country_iso3            CHAR(3)      NOT NULL,
    year                    SMALLINT     NOT NULL,
    convention_entry_date   TEXT,        -- partial dates common: "1975", "1975-03"
    has_offensive_programme BOOLEAN,
    offensive_period        TEXT,
    offensive_summary       TEXT,
    has_defensive_programme BOOLEAN,
    defensive_period        TEXT,
    defensive_summary       TEXT,
    confidence              NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    translated              BOOLEAN,
    notes                   TEXT
);

COMMENT ON TABLE past_programmes IS 'Form F: declaration of past offensive and/or defensive biological R&D programmes';
COMMENT ON COLUMN past_programmes.convention_entry_date IS 'Date of BWC entry into force for this state; stored as text to accommodate partial dates';

-- ── Biosafety/biosecurity legislation (Form E) ────────────────────────────────
-- At most one row per document.  The four categories correspond to the Form E
-- table rows: (a) Article I prohibitions, (b) exports, (c) imports, (d) biosafety.

CREATE TABLE legislation (
    document_id                 TEXT         PRIMARY KEY REFERENCES documents,
    country_iso3                CHAR(3)      NOT NULL,
    year                        SMALLINT     NOT NULL,
    -- (a) Development, production, stockpiling, acquisition/retention (Article I)
    prohibitions_legislation    BOOLEAN,
    prohibitions_regulations    BOOLEAN,
    prohibitions_other_measures BOOLEAN,
    prohibitions_amended        BOOLEAN,
    -- (b) Export controls
    exports_legislation         BOOLEAN,
    exports_regulations         BOOLEAN,
    exports_other_measures      BOOLEAN,
    exports_amended             BOOLEAN,
    -- (c) Import controls
    imports_legislation         BOOLEAN,
    imports_regulations         BOOLEAN,
    imports_other_measures      BOOLEAN,
    imports_amended             BOOLEAN,
    -- (d) Biosafety and biosecurity
    biosafety_legislation       BOOLEAN,
    biosafety_regulations       BOOLEAN,
    biosafety_other_measures    BOOLEAN,
    biosafety_amended           BOOLEAN,
    key_laws                    TEXT[]       NOT NULL DEFAULT '{}',
    confidence                  NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    translated                  BOOLEAN,
    notes                       TEXT
);

COMMENT ON TABLE legislation IS 'Form E: declaration of national biosafety/biosecurity legislation and regulations';
COMMENT ON COLUMN legislation.key_laws IS 'Array of short law/regulation names identified in the submission text';
COMMENT ON COLUMN legislation.prohibitions_amended IS 'True if legislation/measures were amended since the previous submission';

-- ── Form compliance (derived from Form 0 cover page) ──────────────────────────
-- One row per document × form.  Status is derived from the segmentation
-- manifests (forms_present, forms_nothing_to_declare) produced by script 03.

CREATE TABLE form_compliance (
    document_id TEXT NOT NULL REFERENCES documents,
    form        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('substantive','nothing_to_declare','absent')),
    PRIMARY KEY (document_id, form)
);

COMMENT ON TABLE form_compliance IS 'Per-form completion status derived from Form 0 (cover page) declarations';
COMMENT ON COLUMN form_compliance.form IS 'CBM form identifier: A1, A2, B, C, E, F, or G';
COMMENT ON COLUMN form_compliance.status IS
    'substantive: form present with data; '
    'nothing_to_declare: state declared nothing under this form; '
    'absent: form not included in submission';

COMMIT;
