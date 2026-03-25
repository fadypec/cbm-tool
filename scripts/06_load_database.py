#!/usr/bin/env python3
"""
06_load_database.py — Load all extracted CBM data into PostgreSQL.

Reads data/output/*.csv and catalogue.json, then bulk-loads every table.
Re-running is safe: all tables are truncated and reloaded from scratch, so
the database always reflects the current state of the pipeline outputs.

The database connection is read from DATABASE_URL in .env:
    DATABASE_URL=postgresql://cbm:cbm@localhost:5432/cbm

Usage:
    python scripts/06_load_database.py
    python scripts/06_load_database.py --table facility_years   # reload one table

Prerequisites:
    docker compose up -d   # start the database
    ./db/migrate.sh        # apply schema migrations
"""

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
OUTPUT_DIR    = PROJECT_ROOT / "data" / "output"
CATALOGUE_PATH = PROJECT_ROOT / "data" / "catalogue.json"

# ── Type coercions ────────────────────────────────────────────────────────────


def _bool(v: str | None) -> bool | None:
    if v in ("True", "true", "1", "yes"):  return True
    if v in ("False", "false", "0", "no"): return False
    return None


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v else None
    except (ValueError, TypeError):
        return None


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def _str(v: str | None) -> str | None:
    return v if v else None


def _arr(v: str | None) -> list[str]:
    """Split a semicolon-separated string into a list; return [] for blank."""
    if not v:
        return []
    return [s.strip() for s in v.split(";") if s.strip()]


# ── Loader functions ──────────────────────────────────────────────────────────


def load_documents(cur, catalogue: list[dict]) -> int:
    rows = [
        (
            e["id"],
            e.get("country_iso3"),
            _str(e.get("country")),
            _int(str(e.get("year", ""))) ,
            _str(e.get("language")),
            _str(e.get("source_url")),
            bool(e.get("is_amendment", False)),
        )
        for e in catalogue
        if e.get("downloaded") and not e.get("is_amendment")
           or e.get("is_amendment")   # include amendments too (for FK completeness)
    ]
    # deduplicate on id
    seen: set[str] = set()
    deduped = []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)

    psycopg2.extras.execute_batch(cur, """
        INSERT INTO documents (id, country_iso3, country_name, year, language, source_url, is_amendment)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET country_name = EXCLUDED.country_name
    """, deduped)
    return len(deduped)


def load_facilities(cur) -> int:
    path = OUTPUT_DIR / "entity_registry.json"
    if not path.exists():
        log.warning("entity_registry.json not found; skipping facilities")
        return 0
    registry = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        (
            r["canonical_facility_id"],
            r.get("country_iso3"),
            _str(r.get("canonical_name")),
            r.get("all_names") or [],
            r.get("years_declared") or [],
            _str(r.get("latest_containment")),
            _float(str(r.get("latest_area_m2", ""))) if r.get("latest_area_m2") else None,
        )
        for r in registry
    ]
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO facilities
            (canonical_facility_id, country_iso3, canonical_name,
             all_names, years_declared, latest_containment, latest_area_m2)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (canonical_facility_id) DO NOTHING
    """, rows)
    return len(rows)


def load_facility_years(cur) -> int:
    path = OUTPUT_DIR / "all_facilities.csv"
    if not path.exists():
        log.warning("all_facilities.csv not found; skipping facility_years")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((
                r["canonical_facility_id"],
                r["source_document"],
                r["country_iso3"],
                _int(r["year"]),
                _str(r["facility_name"]),
                _str(r["responsible_org"]),
                _str(r["city"]),
                _str(r["address"]),
                _str(r["funding_sources"]),
                _bool(r["mod_funded"]),
                _bool(r["has_bsl4"]),
                _float(r["bsl4_area_m2"]),
                _bool(r["has_bsl3"]),
                _float(r["bsl3_area_m2"]),
                _str(r["highest_containment"]),
                _str(r["agents_summary"]),
                _bool(r["agents_redacted"]),
                _float(r["confidence"]),
                _bool(r["translated"]),
            ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO facility_years
            (canonical_facility_id, document_id, country_iso3, year,
             facility_name, responsible_org, city, address,
             funding_sources, mod_funded,
             has_bsl4, bsl4_area_m2, has_bsl3, bsl3_area_m2,
             highest_containment, agents_summary, agents_redacted,
             confidence, translated)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    return len(rows)


def load_vaccine_facilities(cur) -> int:
    path = OUTPUT_DIR / "vaccine_entity_registry.json"
    if not path.exists():
        log.warning("vaccine_entity_registry.json not found; skipping vaccine_facilities")
        return 0
    registry = json.loads(path.read_text(encoding="utf-8"))
    years_list = [r.get("years_declared") or [] for r in registry]
    rows = [
        (
            r["canonical_vaccine_facility_id"],
            r.get("country_iso3"),
            _str(r.get("canonical_name")),
            min(yl) if (yl := r.get("years_declared") or []) else None,
            max(yl) if yl else None,
        )
        for r in registry
    ]
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO vaccine_facilities (id, country_iso3, canonical_name, first_year, last_year)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """, rows)
    return len(rows)


def load_vaccine_facility_years(cur) -> int:
    path = OUTPUT_DIR / "all_vaccine_facilities.csv"
    if not path.exists():
        log.warning("all_vaccine_facilities.csv not found; skipping")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((
                _str(r.get("canonical_vaccine_facility_id")),
                r["source_document"],
                r["country_iso3"],
                _int(r["year"]),
                _str(r["facility_name"]),
                _str(r["city"]),
                _str(r["address"]),
                _str(r["diseases_covered"]),
                _str(r["vaccines_summary"]),
                _float(r["confidence"]),
                _bool(r["translated"]),
            ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO vaccine_facility_years
            (canonical_vaccine_facility_id, document_id, country_iso3, year,
             facility_name, city, address, diseases_covered, vaccines_summary,
             confidence, translated)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    return len(rows)


def load_defence_programmes(cur) -> int:
    path = OUTPUT_DIR / "defence_programmes.csv"
    if not path.exists():
        log.warning("defence_programmes.csv not found; skipping")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((
                r["source_document"],
                r["country_iso3"],
                _int(r["year"]),
                _str(r["programme_name"]),
                _str(r["responsible_org"]),
                _str(r["objectives_summary"]),
                _arr(r.get("research_areas")),
                _float(r["total_funding_amount"]),
                _str(r["total_funding_currency"]),
                _bool(r["uses_contractors"]),
                _float(r["contractor_proportion_pct"]),
                _float(r["confidence"]),
                _bool(r["translated"]),
            ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO defence_programmes
            (document_id, country_iso3, year, programme_name, responsible_org,
             objectives_summary, research_areas,
             total_funding_amount, total_funding_currency,
             uses_contractors, contractor_proportion_pct,
             confidence, translated)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    return len(rows)


def load_defence_facilities(cur) -> int:
    path = OUTPUT_DIR / "defence_facilities.csv"
    if not path.exists():
        log.warning("defence_facilities.csv not found; skipping")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((
                r["source_document"],
                r["country_iso3"],
                _int(r["year"]),
                _str(r["facility_name"]),
                _str(r["city"]),
                _str(r["address"]),
                _float(r["bsl2_area_m2"]),
                _float(r["bsl3_area_m2"]),
                _float(r["bsl4_area_m2"]),
                _float(r["total_lab_area_m2"]),
                _int(r["personnel_total"]),
                _int(r["personnel_military"]),
                _int(r["personnel_civilian"]),
                _int(r["personnel_scientists"]),
                _int(r["personnel_engineers"]),
                _int(r["personnel_technicians"]),
                _int(r["personnel_admin"]),
                _bool(r["mod_funded"]),
                _str(r["funding_source"]),
                _float(r["funding_research"]),
                _float(r["funding_development"]),
                _float(r["funding_te"]),
                _str(r["funding_currency"]),
                _str(r["work_description"]),
                _float(r["confidence"]),
                _bool(r["translated"]),
                _str(r.get("canonical_defence_facility_id")),
            ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO defence_facilities
            (document_id, country_iso3, year, facility_name, city, address,
             bsl2_area_m2, bsl3_area_m2, bsl4_area_m2, total_lab_area_m2,
             personnel_total, personnel_military, personnel_civilian,
             personnel_scientists, personnel_engineers, personnel_technicians,
             personnel_admin, mod_funded, funding_source,
             funding_research, funding_development, funding_te, funding_currency,
             work_description, confidence, translated, canonical_defence_facility_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    return len(rows)


def load_past_programmes(cur) -> int:
    path = OUTPUT_DIR / "past_programmes.csv"
    if not path.exists():
        log.warning("past_programmes.csv not found; skipping")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((
                r["source_document"],
                r["country_iso3"],
                _int(r["year"]),
                _str(r["convention_entry_date"]),
                _bool(r["has_offensive_programme"]),
                _str(r["offensive_period"]),
                _str(r["offensive_summary"]),
                _bool(r["has_defensive_programme"]),
                _str(r["defensive_period"]),
                _str(r["defensive_summary"]),
                _float(r["confidence"]),
                _bool(r["translated"]),
                _str(r["notes"]),
            ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO past_programmes
            (document_id, country_iso3, year,
             convention_entry_date,
             has_offensive_programme, offensive_period, offensive_summary,
             has_defensive_programme, defensive_period, defensive_summary,
             confidence, translated, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (document_id) DO NOTHING
    """, rows)
    return len(rows)


def load_legislation(cur) -> int:
    path = OUTPUT_DIR / "legislation.csv"
    if not path.exists():
        log.warning("legislation.csv not found; skipping")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((
                r["source_document"],
                r["country_iso3"],
                _int(r["year"]),
                _bool(r["prohibitions_legislation"]),
                _bool(r["prohibitions_regulations"]),
                _bool(r["prohibitions_other_measures"]),
                _bool(r["prohibitions_amended"]),
                _bool(r["exports_legislation"]),
                _bool(r["exports_regulations"]),
                _bool(r["exports_other_measures"]),
                _bool(r["exports_amended"]),
                _bool(r["imports_legislation"]),
                _bool(r["imports_regulations"]),
                _bool(r["imports_other_measures"]),
                _bool(r["imports_amended"]),
                _bool(r["biosafety_legislation"]),
                _bool(r["biosafety_regulations"]),
                _bool(r["biosafety_other_measures"]),
                _bool(r["biosafety_amended"]),
                _arr(r.get("key_laws")),
                _float(r["confidence"]),
                _bool(r["translated"]),
                _str(r["notes"]),
            ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO legislation
            (document_id, country_iso3, year,
             prohibitions_legislation, prohibitions_regulations,
             prohibitions_other_measures, prohibitions_amended,
             exports_legislation, exports_regulations,
             exports_other_measures, exports_amended,
             imports_legislation, imports_regulations,
             imports_other_measures, imports_amended,
             biosafety_legislation, biosafety_regulations,
             biosafety_other_measures, biosafety_amended,
             key_laws, confidence, translated, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (document_id) DO NOTHING
    """, rows)
    return len(rows)


def build_defence_entities(conn) -> int:
    """
    FEATURE 11: Rebuild the defence_entities canonical entity registry from defence_facilities.

    Groups by canonical_defence_facility_id, picks the most recent facility_name
    as canonical_name, computes first_year/last_year, and aggregates all_names.
    Only processes rows where canonical_defence_facility_id IS NOT NULL.

    This function is idempotent — safe to call multiple times.
    Returns the number of entities written.
    """
    with conn.cursor() as cur:
        # Check if the table exists (may not if migration 010 hasn't been applied yet)
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'defence_entities'
            )
        """)
        if not cur.fetchone()[0]:
            log.warning("defence_entities table does not exist — skipping build_defence_entities")
            log.warning("Apply db/migrations/010_defence_entity_table.sql first.")
            return 0

        # Truncate existing contents so rebuild is idempotent
        cur.execute("TRUNCATE TABLE defence_entities")

        # Aggregate from defence_facilities
        # all_names: collect all distinct non-null facility_name values per entity
        cur.execute("""
            INSERT INTO defence_entities
                (canonical_defence_facility_id, country_iso3, canonical_name,
                 first_year, last_year, all_names)
            SELECT
                sub.canonical_defence_facility_id,
                sub.country_iso3,
                sub.canonical_name,
                sub.first_year,
                sub.last_year,
                sub.all_names
            FROM (
                SELECT
                    canonical_defence_facility_id,
                    MAX(country_iso3)   AS country_iso3,
                    -- Most recent facility_name as canonical name
                    (ARRAY_AGG(facility_name ORDER BY year DESC NULLS LAST))[1] AS canonical_name,
                    MIN(year)           AS first_year,
                    MAX(year)           AS last_year,
                    -- Collect all distinct non-null names using subquery approach
                    ARRAY(
                        SELECT DISTINCT n
                        FROM unnest(ARRAY_AGG(facility_name)) AS n
                        WHERE n IS NOT NULL
                    )                   AS all_names
                FROM defence_facilities
                WHERE canonical_defence_facility_id IS NOT NULL
                GROUP BY canonical_defence_facility_id
            ) sub
        """)
        n = cur.rowcount

    conn.commit()
    log.info("build_defence_entities: wrote %d canonical defence entities", n)
    return n


def load_form_compliance(cur) -> int:
    path = OUTPUT_DIR / "form_compliance.csv"
    if not path.exists():
        log.warning("form_compliance.csv not found; skipping")
        return 0
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((r["source_document"], r["form"], r["status"]))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO form_compliance (document_id, form, status)
        VALUES (%s, %s, %s)
        ON CONFLICT (document_id, form) DO NOTHING
    """, rows)
    return len(rows)


# ── Table registry ────────────────────────────────────────────────────────────

# Order matters: parent tables before child tables (FK constraints)
ALL_TABLES = [
    ("documents",              load_documents,             True),   # True = needs catalogue arg
    ("facilities",             load_facilities,            False),
    ("facility_years",         load_facility_years,        False),
    ("vaccine_facilities",     load_vaccine_facilities,    False),
    ("vaccine_facility_years", load_vaccine_facility_years,False),
    ("defence_programmes",     load_defence_programmes,    False),
    ("defence_facilities",     load_defence_facilities,    False),
    ("past_programmes",        load_past_programmes,       False),
    ("legislation",            load_legislation,           False),
    ("form_compliance",        load_form_compliance,       False),
]

# Tables that depend on documents (to truncate in reverse FK order)
TRUNCATE_ORDER = [
    "form_compliance",
    "legislation",
    "past_programmes",
    "defence_facilities",
    "defence_programmes",
    "vaccine_facility_years",
    "vaccine_facilities",
    "facility_years",
    "facilities",
    "documents",
]


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table", metavar="NAME",
        help="Reload only this table (truncate + reload). Skips FK-dependent tables.",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    db_url = os.getenv("DATABASE_URL", "postgresql://cbm:cbm@localhost:5432/cbm")

    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        log.error("Cannot connect to database: %s", exc)
        log.error("Is the database running?  Try: docker compose up -d")
        sys.exit(1)

    catalogue: list[dict] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    log.info("Loaded catalogue: %d entries", len(catalogue))

    with conn:
        with conn.cursor() as cur:
            if args.table:
                # Single-table reload
                target = args.table
                log.info("Truncating %s …", target)
                cur.execute(f"TRUNCATE TABLE {target} CASCADE")
                for name, fn, needs_cat in ALL_TABLES:
                    if name == target:
                        n = fn(cur, catalogue) if needs_cat else fn(cur)
                        log.info("Loaded %s: %d rows", name, n)
                        break
                else:
                    log.error("Unknown table: %s", target)
                    sys.exit(1)
            else:
                # Full reload: snapshot geom columns, truncate, reload, restore geom
                log.info("Saving geocoded coordinates before truncation …")
                cur.execute("""
                    SELECT fy.canonical_facility_id, fy.document_id,
                           fy.geom, fy.geocode_source, fy.geocode_confidence
                    FROM   facility_years fy
                    WHERE  fy.geom IS NOT NULL
                """)
                fy_geom = cur.fetchall()
                log.info("  Saved %d facility_years geom rows", len(fy_geom))

                # defence_facilities uses a serial PK that resets on truncation, so
                # we cannot restore by PK. Use (document_id, facility_name) instead —
                # both are stable across reloads since they come directly from the CSV.
                cur.execute("""
                    SELECT df.document_id, df.facility_name,
                           df.geom, df.geocode_source, df.geocode_confidence
                    FROM   defence_facilities df
                    WHERE  df.geom IS NOT NULL
                """)
                df_geom = cur.fetchall()
                log.info("  Saved %d defence_facilities geom rows", len(df_geom))

                cur.execute("""
                    SELECT vfy.document_id,
                           vfy.facility_name,
                           vfy.geom, vfy.geocode_source, vfy.geocode_confidence
                    FROM   vaccine_facility_years vfy
                    WHERE  vfy.geom IS NOT NULL
                """)
                vfy_geom = cur.fetchall()
                log.info("  Saved %d vaccine_facility_years geom rows", len(vfy_geom))

                log.info("Truncating all tables …")
                cur.execute(
                    "TRUNCATE TABLE " + ", ".join(TRUNCATE_ORDER) + " CASCADE"
                )
                for name, fn, needs_cat in ALL_TABLES:
                    n = fn(cur, catalogue) if needs_cat else fn(cur)
                    log.info("  %-28s %d rows", name, n)

                # Restore geocoded geometry for facility_years.
                # After restoration, verify how many rows actually matched to
                # detect silent losses from entity ID changes between runs.
                if fy_geom:
                    psycopg2.extras.execute_batch(cur, """
                        UPDATE facility_years
                        SET    geom               = %s,
                               geocode_source     = %s,
                               geocode_confidence = %s
                        WHERE  canonical_facility_id = %s
                        AND    document_id           = %s
                    """, [
                        (row[2], row[3], row[4], row[0], row[1])
                        for row in fy_geom
                    ])
                    cur.execute("SELECT count(*) FROM facility_years WHERE geom IS NOT NULL")
                    actual = cur.fetchone()[0]
                    log.info("  Restored facility_years geom: %d/%d matched", actual, len(fy_geom))
                    if actual < len(fy_geom):
                        log.warning("  ⚠ %d facility_years geom rows not restored "
                                    "(entity IDs may have changed)", len(fy_geom) - actual)

                # Restore geocoded geometry for vaccine_facility_years.
                # Key on (document_id, facility_name) — canonical_vaccine_facility_id
                # may be NULL if geocoding ran before entity resolution.
                if vfy_geom:
                    psycopg2.extras.execute_batch(cur, """
                        UPDATE vaccine_facility_years
                        SET    geom               = %s,
                               geocode_source     = %s,
                               geocode_confidence = %s
                        WHERE  document_id   = %s
                        AND    facility_name = %s
                    """, [
                        (row[2], row[3], row[4], row[0], row[1])
                        for row in vfy_geom
                    ])
                    cur.execute("SELECT count(*) FROM vaccine_facility_years WHERE geom IS NOT NULL")
                    actual = cur.fetchone()[0]
                    log.info("  Restored vaccine_facility_years geom: %d/%d matched", actual, len(vfy_geom))
                    if actual < len(vfy_geom):
                        log.warning("  ⚠ %d vaccine_facility_years geom rows not restored "
                                    "(facility names may have changed)", len(vfy_geom) - actual)

                # Restore geocoded geometry for defence_facilities
                if df_geom:
                    psycopg2.extras.execute_batch(cur, """
                        UPDATE defence_facilities
                        SET    geom               = %s,
                               geocode_source     = %s,
                               geocode_confidence = %s
                        WHERE  document_id   = %s
                        AND    facility_name = %s
                    """, [
                        (row[2], row[3], row[4], row[0], row[1])
                        for row in df_geom
                    ])
                    cur.execute("SELECT count(*) FROM defence_facilities WHERE geom IS NOT NULL")
                    actual = cur.fetchone()[0]
                    log.info("  Restored defence_facilities geom: %d/%d matched", actual, len(df_geom))
                    if actual < len(df_geom):
                        log.warning("  ⚠ %d defence_facilities geom rows not restored "
                                    "(facility names may have changed)", len(df_geom) - actual)

    # FEATURE 11: Rebuild defence_entities canonical registry after defence_facilities load
    n_de = build_defence_entities(conn)
    log.info("  %-28s %d rows", "defence_entities", n_de)

    conn.close()
    log.info("Database load complete.")


if __name__ == "__main__":
    main()
