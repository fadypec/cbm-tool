#!/usr/bin/env python3
"""
CBM Facility Explorer — REST API

Serves structured data from the CBM PostgreSQL database and the static
web dashboard.

Run:
    uvicorn api.main:app --reload --port 8000

Then open http://localhost:8000 in a browser.
"""

from __future__ import annotations

import decimal
import json
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Config ──────────────────────────────────────────────────────────────────

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

load_dotenv(PROJECT_ROOT / ".env")
DB_URL = os.getenv("DATABASE_URL", "postgresql://cbm:cbm@localhost:5432/cbm")

# ── Connection pool ──────────────────────────────────────────────────────────

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DB_URL)
    yield
    if _pool:
        _pool.closeall()


@contextmanager
def cursor():
    """Yield a RealDictCursor, returning the connection to the pool on exit."""
    conn = _pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        _pool.putconn(conn)


# ── JSON serialisation ───────────────────────────────────────────────────────

class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)


def _json(data) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(data, cls=_Enc)))


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CBM Facility Explorer",
    version="1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.mount("/static", StaticFiles(directory=DASHBOARD_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(DASHBOARD_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


# ── /api/stats ───────────────────────────────────────────────────────────────

@app.get("/api/stats", summary="Global summary statistics")
def api_stats():
    with cursor() as cur:
        cur.execute("""
            SELECT
                (SELECT count(*)             FROM documents WHERE NOT is_amendment)                AS total_submissions,
                (SELECT count(DISTINCT country_iso3) FROM documents WHERE NOT is_amendment)        AS total_countries,
                (SELECT count(*)             FROM facility_years)                                   AS total_facility_years,
                (SELECT count(*)             FROM facilities)                                       AS total_unique_facilities,
                (SELECT count(*)             FROM facility_years WHERE geom IS NOT NULL)            AS geocoded_facility_years,
                (SELECT count(*)             FROM vaccine_facility_years)                           AS vaccine_facility_years,
                (SELECT count(*)             FROM defence_facilities)                               AS defence_facility_years,
                (SELECT min(year)            FROM documents WHERE NOT is_amendment)                 AS year_min,
                (SELECT max(year)            FROM documents WHERE NOT is_amendment)                 AS year_max
        """)
        return _json(dict(cur.fetchone()))


# ── /api/countries ───────────────────────────────────────────────────────────

@app.get("/api/countries", summary="All submitting countries with summary stats")
def api_countries():
    with cursor() as cur:
        cur.execute("""
            SELECT
                d.country_iso3,
                MAX(d.country_name)                                                          AS country_name,
                COUNT(DISTINCT d.id)                                                         AS submission_count,
                MAX(d.year)                                                                  AS latest_year,
                COUNT(DISTINCT fy.canonical_facility_id)                                     AS facility_count,
                COUNT(DISTINCT CASE WHEN fy.has_bsl4 THEN fy.canonical_facility_id END)     AS bsl4_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.id END)::numeric
                    / NULLIF(COUNT(DISTINCT d.id), 0), 3
                )                                                                            AS a1_rate
            FROM documents d
            LEFT JOIN facility_years fy  ON fy.document_id = d.id
            LEFT JOIN form_compliance fc ON fc.document_id = d.id AND fc.form = 'A1'
            WHERE NOT d.is_amendment
            GROUP BY d.country_iso3
            ORDER BY MAX(d.country_name)
        """)
        return _json([dict(r) for r in cur.fetchall()])


# ── /api/country/{iso3} ───────────────────────────────────────────────────────

@app.get("/api/country/{iso3}", summary="Compliance history and facility list for one country")
def api_country(iso3: str):
    iso3 = iso3.upper()
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT country_name FROM documents "
            "WHERE country_iso3 = %s AND country_name IS NOT NULL LIMIT 1",
            (iso3,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Country '{iso3}' not found")
        country_name = row["country_name"]

        cur.execute("""
            SELECT d.year, fc.form, fc.status
            FROM form_compliance fc
            JOIN documents d ON d.id = fc.document_id
            WHERE d.country_iso3 = %s AND NOT d.is_amendment
            ORDER BY d.year DESC, fc.form
        """, (iso3,))
        compliance = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT canonical_facility_id, canonical_name,
                   latest_containment, years_declared, latest_area_m2
            FROM facilities
            WHERE country_iso3 = %s
            ORDER BY canonical_name NULLS LAST
        """, (iso3,))
        facilities = [dict(r) for r in cur.fetchall()]

    return _json({
        "country_iso3": iso3,
        "country_name":  country_name,
        "compliance":    compliance,
        "facilities":    facilities,
    })


# ── /api/country/{iso3}/defence ──────────────────────────────────────────────

@app.get("/api/country/{iso3}/defence", summary="Defence programmes and facilities for one country")
def api_country_defence(iso3: str):
    iso3 = iso3.upper()
    with cursor() as cur:
        cur.execute("""
            SELECT year, programme_name, responsible_org, objectives_summary,
                   research_areas, total_funding_amount, total_funding_currency,
                   uses_contractors, contractor_proportion_pct, confidence
            FROM defence_programmes
            WHERE country_iso3 = %s
            ORDER BY year DESC
        """, (iso3,))
        programmes = [dict(r) for r in cur.fetchall()]

        # Canonical entity summary (one row per unique facility)
        cur.execute("""
            SELECT
                d.canonical_defence_facility_id AS canonical_id,
                (SELECT df2.facility_name
                 FROM defence_facilities df2
                 WHERE df2.canonical_defence_facility_id = d.canonical_defence_facility_id
                 ORDER BY df2.year DESC LIMIT 1) AS canonical_name,
                MIN(d.year) AS first_year,
                MAX(d.year) AS last_year,
                BOOL_OR(d.bsl4_area_m2 IS NOT NULL AND d.bsl4_area_m2 > 0) AS has_bsl4,
                BOOL_OR(d.bsl3_area_m2 IS NOT NULL AND d.bsl3_area_m2 > 0) AS has_bsl3
            FROM defence_facilities d
            WHERE d.country_iso3 = %s
              AND d.canonical_defence_facility_id IS NOT NULL
            GROUP BY d.canonical_defence_facility_id
            ORDER BY canonical_name
        """, (iso3,))
        entities = [dict(r) for r in cur.fetchall()]

        # All year records (for detail within the entity modal)
        cur.execute("""
            SELECT year, canonical_defence_facility_id, facility_name,
                   city, address, bsl2_area_m2, bsl3_area_m2, bsl4_area_m2,
                   total_lab_area_m2, personnel_total, personnel_military,
                   personnel_civilian, mod_funded, work_description, confidence
            FROM defence_facilities
            WHERE country_iso3 = %s
            ORDER BY year DESC, facility_name
        """, (iso3,))
        records = [dict(r) for r in cur.fetchall()]

    return _json({"programmes": programmes, "entities": entities, "records": records})


# ── /api/entity/defence/{id} ──────────────────────────────────────────────────

@app.get("/api/entity/defence/{entity_id}", summary="Full history for one canonical defence facility")
def api_defence_entity(entity_id: str):
    with cursor() as cur:
        cur.execute("""
            SELECT
                df.canonical_defence_facility_id,
                (SELECT df2.facility_name FROM defence_facilities df2
                 WHERE df2.canonical_defence_facility_id = df.canonical_defence_facility_id
                 ORDER BY df2.year DESC LIMIT 1) AS canonical_name,
                df.country_iso3,
                (SELECT d.country_name FROM documents d
                 WHERE d.country_iso3 = df.country_iso3
                 AND d.country_name IS NOT NULL LIMIT 1) AS country_name,
                array_agg(DISTINCT df.facility_name ORDER BY df.facility_name)
                    FILTER (WHERE df.facility_name IS NOT NULL) AS all_names,
                MIN(df.year) AS first_year,
                MAX(df.year) AS last_year
            FROM defence_facilities df
            WHERE df.canonical_defence_facility_id = %s
            GROUP BY df.canonical_defence_facility_id, df.country_iso3
        """, (entity_id,))
        entity = cur.fetchone()
        if not entity:
            raise HTTPException(status_code=404, detail=f"Defence entity '{entity_id}' not found")
        entity = dict(entity)

        cur.execute("""
            SELECT df.year, df.facility_name, df.city, df.address,
                   df.bsl2_area_m2, df.bsl3_area_m2, df.bsl4_area_m2, df.total_lab_area_m2,
                   df.personnel_total, df.personnel_military, df.personnel_civilian,
                   df.personnel_scientists, df.personnel_engineers, df.personnel_technicians,
                   df.personnel_admin, df.mod_funded, df.work_description,
                   df.funding_source, df.funding_research, df.funding_development,
                   df.funding_te, df.funding_currency, df.confidence, df.geocode_confidence,
                   d.source_url
            FROM defence_facilities df
            JOIN documents d ON d.id = df.document_id
            WHERE df.canonical_defence_facility_id = %s
            ORDER BY df.year DESC
        """, (entity_id,))
        entity["year_records"] = [dict(r) for r in cur.fetchall()]

    return _json(entity)


# ── /api/country/{iso3}/vaccine ───────────────────────────────────────────────

@app.get("/api/country/{iso3}/vaccine", summary="Vaccine facilities for one country")
def api_country_vaccine(iso3: str):
    iso3 = iso3.upper()
    with cursor() as cur:
        cur.execute("""
            SELECT vf.id AS canonical_id, vf.canonical_name,
                   vf.first_year, vf.last_year
            FROM vaccine_facilities vf
            WHERE vf.country_iso3 = %s
            ORDER BY vf.canonical_name
        """, (iso3,))
        entities = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT year, canonical_vaccine_facility_id, facility_name,
                   city, address, diseases_covered, vaccines_summary, confidence
            FROM vaccine_facility_years
            WHERE country_iso3 = %s
            ORDER BY year DESC, facility_name
        """, (iso3,))
        records = [dict(r) for r in cur.fetchall()]

    return _json({"entities": entities, "records": records})


# ── /api/country/{iso3}/legislation ──────────────────────────────────────────

@app.get("/api/country/{iso3}/legislation", summary="Biosafety legislation history for one country")
def api_country_legislation(iso3: str):
    iso3 = iso3.upper()
    with cursor() as cur:
        cur.execute("""
            SELECT year,
                   prohibitions_legislation, prohibitions_regulations,
                   prohibitions_other_measures, prohibitions_amended,
                   exports_legislation, exports_regulations,
                   exports_other_measures, exports_amended,
                   imports_legislation, imports_regulations,
                   imports_other_measures, imports_amended,
                   biosafety_legislation, biosafety_regulations,
                   biosafety_other_measures, biosafety_amended,
                   key_laws, notes, confidence, document_id
            FROM legislation
            WHERE country_iso3 = %s
            ORDER BY year DESC
        """, (iso3,))
        records = [dict(r) for r in cur.fetchall()]
        # Attach source URL
        for rec in records:
            cur.execute("SELECT source_url FROM documents WHERE id = %s", (rec["document_id"],))
            row = cur.fetchone()
            rec["source_url"] = row["source_url"] if row else None

    return _json(records)


# ── /api/country/{iso3}/past-programmes ──────────────────────────────────────

@app.get("/api/country/{iso3}/past-programmes", summary="Past offensive/defensive programme declarations for one country")
def api_country_past_programmes(iso3: str):
    iso3 = iso3.upper()
    with cursor() as cur:
        cur.execute("""
            SELECT year, convention_entry_date,
                   has_offensive_programme, offensive_period, offensive_summary,
                   has_defensive_programme, defensive_period, defensive_summary,
                   confidence, notes, document_id
            FROM past_programmes
            WHERE country_iso3 = %s
            ORDER BY year DESC
        """, (iso3,))
        records = [dict(r) for r in cur.fetchall()]
        for rec in records:
            cur.execute("SELECT source_url FROM documents WHERE id = %s", (rec["document_id"],))
            row = cur.fetchone()
            rec["source_url"] = row["source_url"] if row else None

    return _json(records)


# ── /api/map/facilities ───────────────────────────────────────────────────────

@app.get("/api/map/facilities", summary="GeoJSON: all geocoded Form A1 facility-year records")
def api_map_facilities():
    """Returns one feature per geocoded facility-year (all years).
    Client-side year filtering applies to this dataset."""
    with cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (f.canonical_facility_id, fy.year)
                f.canonical_facility_id,
                COALESCE(f.canonical_name, fy.facility_name) AS name,
                fy.country_iso3,
                fy.highest_containment                        AS containment,
                fy.year,
                fy.city,
                fy.geocode_confidence,
                ST_X(fy.geom)                                 AS lon,
                ST_Y(fy.geom)                                 AS lat,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = fy.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1)     AS country_name
            FROM facility_years fy
            JOIN facilities f ON f.canonical_facility_id = fy.canonical_facility_id
            WHERE fy.geom IS NOT NULL
            ORDER BY f.canonical_facility_id, fy.year, fy.document_id
        """)
        rows = cur.fetchall()

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r["lon"]), float(r["lat"])],
            },
            "properties": {
                "id":           r["canonical_facility_id"],
                "name":         r["name"],
                "country_iso3": r["country_iso3"],
                "country_name": r["country_name"],
                "containment":  r["containment"],
                "year":         r["year"],
                "city":         r["city"],
                "geocode_conf": r["geocode_confidence"],
            },
        }
        for r in rows
    ]
    return _json({"type": "FeatureCollection", "features": features})


@app.get("/api/map/defence", summary="GeoJSON: all geocoded Form A2 defence facility records")
def api_map_defence():
    with cursor() as cur:
        cur.execute("""
            SELECT
                df.id,
                df.facility_name                              AS name,
                df.country_iso3,
                df.year,
                df.city,
                df.geocode_confidence,
                ST_X(df.geom)                                 AS lon,
                ST_Y(df.geom)                                 AS lat,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = df.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1)     AS country_name
            FROM defence_facilities df
            WHERE df.geom IS NOT NULL
            ORDER BY df.year, df.country_iso3
        """)
        rows = cur.fetchall()

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r["lon"]), float(r["lat"])],
            },
            "properties": {
                "id":           r["id"],
                "name":         r["name"],
                "country_iso3": r["country_iso3"],
                "country_name": r["country_name"],
                "year":         r["year"],
                "city":         r["city"],
                "geocode_conf": r["geocode_confidence"],
            },
        }
        for r in rows
    ]
    return _json({"type": "FeatureCollection", "features": features})


@app.get("/api/map/vaccines", summary="GeoJSON: all geocoded Form G vaccine facility records")
def api_map_vaccines():
    with cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (vfy.canonical_vaccine_facility_id, vfy.year)
                vfy.canonical_vaccine_facility_id             AS id,
                COALESCE(vf.canonical_name, vfy.facility_name) AS name,
                vfy.country_iso3,
                vfy.year,
                vfy.city,
                vfy.geocode_confidence,
                ST_X(vfy.geom)                                AS lon,
                ST_Y(vfy.geom)                                AS lat,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = vfy.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1)     AS country_name
            FROM vaccine_facility_years vfy
            LEFT JOIN vaccine_facilities vf
                   ON vf.id = vfy.canonical_vaccine_facility_id
            WHERE vfy.geom IS NOT NULL
            ORDER BY vfy.canonical_vaccine_facility_id, vfy.year, vfy.document_id
        """)
        rows = cur.fetchall()

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r["lon"]), float(r["lat"])],
            },
            "properties": {
                "id":           r["id"],
                "name":         r["name"],
                "country_iso3": r["country_iso3"],
                "country_name": r["country_name"],
                "year":         r["year"],
                "city":         r["city"],
                "geocode_conf": r["geocode_confidence"],
            },
        }
        for r in rows
    ]
    return _json({"type": "FeatureCollection", "features": features})


# ── /api/map/compliance ───────────────────────────────────────────────────────

@app.get("/api/map/compliance", summary="Per-country Form A1 submission rates (for choropleth)")
def api_map_compliance():
    with cursor() as cur:
        cur.execute("""
            SELECT
                d.country_iso3,
                MAX(d.country_name)  AS country_name,
                COUNT(DISTINCT d.id) AS submission_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.id END)::numeric
                    / NULLIF(COUNT(DISTINCT d.id), 0), 3
                )                    AS a1_rate
            FROM documents d
            LEFT JOIN form_compliance fc ON fc.document_id = d.id AND fc.form = 'A1'
            WHERE NOT d.is_amendment
            GROUP BY d.country_iso3
        """)
        return _json([dict(r) for r in cur.fetchall()])


# ── /api/search ───────────────────────────────────────────────────────────────

@app.get("/api/search", summary="Search facilities by name (max 20 results)")
def api_search(q: str = Query(default="", min_length=2, description="Substring to search")):
    with cursor() as cur:
        like = f"%{q}%"
        cur.execute("""
            SELECT
                f.canonical_facility_id        AS id,
                f.canonical_name               AS name,
                f.country_iso3,
                f.latest_containment,
                f.years_declared,
                'A1'                           AS layer,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = f.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1) AS country_name
            FROM facilities f
            WHERE f.canonical_name ILIKE %s
               OR EXISTS (
                   SELECT 1 FROM unnest(f.all_names) AS n(name)
                   WHERE n.name ILIKE %s
               )
            UNION ALL
            SELECT
                vf.id::text                    AS id,
                vf.canonical_name              AS name,
                vf.country_iso3,
                NULL                           AS latest_containment,
                ARRAY(SELECT generate_series(vf.first_year::int, vf.last_year::int)) AS years_declared,
                'G'                            AS layer,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = vf.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1) AS country_name
            FROM vaccine_facilities vf
            WHERE vf.canonical_name ILIKE %s
            UNION ALL
            SELECT DISTINCT ON (df.country_iso3, df.facility_name)
                NULL                           AS id,
                df.facility_name               AS name,
                df.country_iso3,
                NULL                           AS latest_containment,
                NULL                           AS years_declared,
                'A2'                           AS layer,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = df.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1) AS country_name
            FROM defence_facilities df
            WHERE df.facility_name ILIKE %s
            ORDER BY name NULLS LAST
            LIMIT 20
        """, (like, like, like, like))
        return _json([dict(r) for r in cur.fetchall()])


# ── /api/entity/{id} ──────────────────────────────────────────────────────────

@app.get("/api/entity/{entity_id}", summary="Full history for one canonical facility")
def api_entity(entity_id: str):
    with cursor() as cur:
        cur.execute("""
            SELECT
                f.canonical_facility_id,
                f.canonical_name,
                f.country_iso3,
                f.all_names,
                f.years_declared,
                f.latest_containment,
                f.latest_area_m2,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = f.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1) AS country_name
            FROM facilities f
            WHERE f.canonical_facility_id = %s
        """, (entity_id,))
        fac = cur.fetchone()
        if not fac:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
        fac = dict(fac)

        cur.execute("""
            SELECT
                fy.year,
                fy.document_id,
                fy.facility_name,
                fy.responsible_org,
                fy.city,
                fy.address,
                fy.has_bsl4,
                fy.bsl4_area_m2,
                fy.has_bsl3,
                fy.bsl3_area_m2,
                fy.highest_containment,
                fy.agents_summary,
                fy.mod_funded,
                fy.confidence,
                fy.geocode_confidence,
                d.source_url
            FROM facility_years fy
            JOIN documents d ON d.id = fy.document_id
            WHERE fy.canonical_facility_id = %s
            ORDER BY fy.year DESC
        """, (entity_id,))
        fac["year_records"] = [dict(r) for r in cur.fetchall()]

    return _json(fac)
