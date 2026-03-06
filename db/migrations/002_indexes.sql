-- 002_indexes.sql
-- CBM Tool — indexes for common query patterns
--
-- Covers:
--   • Country + year filtering (most dashboard queries)
--   • Canonical facility lookup (entity history)
--   • Containment level filtering (BSL map layer)
--   • Spatial queries (PostGIS GIST indexes)
--   • Compliance dashboard queries

BEGIN;

-- ── documents ─────────────────────────────────────────────────────────────────
CREATE INDEX ON documents (country_iso3, year);

-- ── facility_years ────────────────────────────────────────────────────────────
CREATE INDEX ON facility_years (country_iso3, year);
CREATE INDEX ON facility_years (canonical_facility_id);
CREATE INDEX ON facility_years (highest_containment)
    WHERE highest_containment IS NOT NULL;
CREATE INDEX ON facility_years USING GIST (geom)
    WHERE geom IS NOT NULL;

-- ── defence_facilities ────────────────────────────────────────────────────────
CREATE INDEX ON defence_facilities (country_iso3, year);
CREATE INDEX ON defence_facilities USING GIST (geom)
    WHERE geom IS NOT NULL;

-- ── defence_programmes ────────────────────────────────────────────────────────
CREATE INDEX ON defence_programmes (country_iso3, year);

-- ── vaccine_facility_years ────────────────────────────────────────────────────
CREATE INDEX ON vaccine_facility_years (country_iso3, year);

-- ── past_programmes ───────────────────────────────────────────────────────────
CREATE INDEX ON past_programmes (country_iso3)
    WHERE has_offensive_programme = TRUE;
CREATE INDEX ON past_programmes (country_iso3)
    WHERE has_defensive_programme = TRUE;

-- ── legislation ───────────────────────────────────────────────────────────────
CREATE INDEX ON legislation (country_iso3, year);

-- ── form_compliance ───────────────────────────────────────────────────────────
-- Used by compliance dashboard: "which countries submitted Form X this year?"
CREATE INDEX ON form_compliance (form, status);
-- Used for per-document compliance overview
CREATE INDEX ON form_compliance (document_id);

COMMIT;
