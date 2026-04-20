-- 022: Add trigram index on facility_years.agents_summary for fast ILIKE search
--
-- The /api/search endpoint uses ILIKE %term% on agents_summary, which causes
-- sequential scans.  A GIN trigram index makes these sub-millisecond.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_facility_years_agents_summary_trgm
    ON facility_years USING gin (agents_summary gin_trgm_ops);
