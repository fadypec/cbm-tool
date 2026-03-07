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


# ── /api/map/facilities ───────────────────────────────────────────────────────

@app.get("/api/map/facilities", summary="GeoJSON: all geocoded Form A1 facility-year records")
def api_map_facilities():
    """Returns one feature per geocoded facility-year (all years).
    Client-side year filtering applies to this dataset."""
    with cursor() as cur:
        cur.execute("""
            SELECT
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
            ORDER BY fy.year, f.canonical_facility_id
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
            SELECT
                vf.id,
                vf.facility_name                              AS name,
                vf.country_iso3,
                vf.year,
                vf.city,
                vf.geocode_confidence,
                ST_X(vf.geom)                                 AS lon,
                ST_Y(vf.geom)                                 AS lat,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = vf.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1)     AS country_name
            FROM vaccine_facility_years vf
            WHERE vf.geom IS NOT NULL
            ORDER BY vf.year, vf.country_iso3
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
                f.canonical_facility_id,
                f.canonical_name,
                f.country_iso3,
                f.latest_containment,
                f.years_declared,
                (SELECT country_name FROM documents
                 WHERE  country_iso3 = f.country_iso3
                 AND    country_name IS NOT NULL LIMIT 1) AS country_name
            FROM facilities f
            WHERE f.canonical_name ILIKE %s
               OR EXISTS (
                   SELECT 1 FROM unnest(f.all_names) AS n(name)
                   WHERE n.name ILIKE %s
               )
            ORDER BY f.canonical_name NULLS LAST
            LIMIT 20
        """, (like, like))
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
                fy.geocode_confidence
            FROM facility_years fy
            WHERE fy.canonical_facility_id = %s
            ORDER BY fy.year DESC
        """, (entity_id,))
        fac["year_records"] = [dict(r) for r in cur.fetchall()]

    return _json(fac)
