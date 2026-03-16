-- FEATURE 9: Form B — disease outbreak declarations
CREATE TABLE IF NOT EXISTS outbreaks (
    id               SERIAL PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id),
    country_iso3     CHAR(3),
    year             SMALLINT,
    pathogen         TEXT,
    location         TEXT,
    date_range       TEXT,
    cases_estimate   TEXT,
    deaths_estimate  TEXT,
    suspected_source TEXT,
    notes            TEXT,
    confidence       REAL,
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbreaks_country ON outbreaks (country_iso3);
CREATE INDEX IF NOT EXISTS idx_outbreaks_year    ON outbreaks (year);
