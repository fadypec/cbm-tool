#!/usr/bin/env python3
"""
07_geocode.py — Geocode facility addresses using OpenStreetMap Nominatim.

Queries facility_years and defence_facilities for rows with no geometry,
builds an address string from the available location fields, calls the
Nominatim search API, and writes the result back as a PostGIS Point.

Nominatim usage policy: max 1 request/second, must identify the application.
Estimated runtime: ~45 minutes for ~2,500 addresses.

Usage:
    python scripts/07_geocode.py
    python scripts/07_geocode.py --table facility_years     # one table only
    python scripts/07_geocode.py --dry-run                  # print queries, no DB writes
    python scripts/07_geocode.py --limit 50                 # process first N rows
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent

NOMINATIM_URL      = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA       = "CBM-Tool/1.0 (BWC Confidence-Building Measures research; https://github.com/pecaf/cbm-tool)"
REQUEST_INTERVAL   = 1.1   # seconds between Nominatim calls (policy: ≤1 req/s)

# Importance thresholds for geocode_confidence classification
IMPORTANCE_HIGH   = 0.6
IMPORTANCE_MEDIUM = 0.3

# ISO3 → ISO2 mapping for Nominatim countrycodes parameter
# (Nominatim uses ISO 3166-1 alpha-2)
ISO3_TO_ISO2 = {
    "AUS": "au", "AUT": "at", "BEL": "be", "BGR": "bg", "BOL": "bo",
    "CAN": "ca", "CHE": "ch", "CIV": "ci", "COK": "ck", "CYP": "cy",
    "CZE": "cz", "DEU": "de", "DNK": "dk", "EST": "ee", "FIN": "fi",
    "GBR": "gb", "GRC": "gr", "HND": "hn", "HRV": "hr", "HUN": "hu",
    "IRL": "ie", "ISL": "is", "JPN": "jp", "KEN": "ke", "LTU": "lt",
    "LUX": "lu", "LVA": "lv", "MDA": "md", "MEX": "mx", "MUS": "mu",
    "MYS": "my", "NLD": "nl", "NOR": "no", "NZL": "nz", "PRT": "pt",
    "ROU": "ro", "SVK": "sk", "SVN": "si", "SWE": "se", "SWZ": "sz",
    "TLS": "tl", "TUV": "tv", "UGA": "ug", "UKR": "ua", "USA": "us",
    "VEN": "ve",
}


# ── Nominatim ─────────────────────────────────────────────────────────────────


def _build_query(address: str | None, city: str | None,
                 country_iso3: str | None) -> str | None:
    """Construct a search query from available location fields."""
    parts = [p for p in (address, city) if p]
    if not parts:
        return None
    iso2 = ISO3_TO_ISO2.get(country_iso3 or "", "")
    if iso2:
        parts.append(iso2.upper())
    return ", ".join(parts)


def _geocode_one(query: str, country_iso3: str | None,
                 session: requests.Session) -> dict | None:
    """
    Call Nominatim for a single query.  Returns a dict with lat, lon,
    importance, and display_name, or None if no result.
    """
    params: dict = {
        "q":      query,
        "format": "json",
        "limit":  1,
    }
    iso2 = ISO3_TO_ISO2.get(country_iso3 or "", "")
    if iso2:
        params["countrycodes"] = iso2

    try:
        resp = session.get(NOMINATIM_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json()
    except requests.RequestException as exc:
        log.warning("Nominatim request failed for %r: %s", query, exc)
        return None

    if not results:
        return None

    r = results[0]
    return {
        "lat":          float(r["lat"]),
        "lon":          float(r["lon"]),
        "importance":   float(r.get("importance", 0)),
        "display_name": r.get("display_name", ""),
    }


def _confidence(importance: float) -> str:
    if importance >= IMPORTANCE_HIGH:   return "high"
    if importance >= IMPORTANCE_MEDIUM: return "medium"
    return "low"


# ── Per-table geocoding ───────────────────────────────────────────────────────


def geocode_table(
    conn,
    table: str,
    dry_run: bool,
    limit: int | None,
    session: requests.Session,
) -> tuple[int, int, int]:
    """
    Geocode ungeocoded rows in `table`.

    Returns (attempted, succeeded, skipped_no_address).
    """
    limit_clause = f"LIMIT {limit}" if limit else ""

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(f"""
            SELECT id, address, city, country_iso3
            FROM   {table}
            WHERE  geom IS NULL
            ORDER  BY country_iso3, id
            {limit_clause}
        """)
        rows = cur.fetchall()

    log.info("%s: %d rows to geocode", table, len(rows))

    attempted = succeeded = skipped = 0
    last_call = 0.0

    for row in rows:
        row_id       = row["id"]
        address      = row["address"]
        city         = row["city"]
        country_iso3 = row["country_iso3"]

        query = _build_query(address, city, country_iso3)
        if not query:
            log.debug("[%s id=%s] no address or city; skipping", table, row_id)
            skipped += 1
            continue

        attempted += 1

        # Rate-limit
        elapsed = time.time() - last_call
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)

        result = _geocode_one(query, country_iso3, session)
        last_call = time.time()

        if result is None:
            # Try again with city only (drop street address noise)
            if address and city:
                fallback_query = _build_query(None, city, country_iso3)
                elapsed = time.time() - last_call
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                result = _geocode_one(fallback_query, country_iso3, session)
                last_call = time.time()

        if result is None:
            log.debug("[%s id=%s] no result for %r", table, row_id, query)
            continue

        conf = _confidence(result["importance"])
        log.debug("[%s id=%s] %s → (%.4f, %.4f) %s",
                  table, row_id, query,
                  result["lat"], result["lon"], conf)

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE {table}
                    SET    geom               = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                           geocode_source     = 'nominatim',
                           geocode_confidence = %s
                    WHERE  id = %s
                """, (result["lon"], result["lat"], conf, row_id))
            conn.commit()

        succeeded += 1

        if attempted % 100 == 0:
            log.info("%s: %d/%d geocoded so far …", table, succeeded, len(rows))

    return attempted, succeeded, skipped


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table",
                        choices=["facility_years", "defence_facilities", "vaccine_facility_years"],
                        help="Geocode only this table.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print geocoding results without writing to the database.")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Process at most N rows per table (for testing).")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    db_url = os.getenv("DATABASE_URL", "postgresql://cbm:cbm@localhost:5432/cbm")

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
    except psycopg2.OperationalError as exc:
        log.error("Cannot connect to database: %s", exc)
        log.error("Is the database running?  Try: docker compose up -d")
        sys.exit(1)

    tables = (
        [args.table] if args.table
        else ["facility_years", "defence_facilities", "vaccine_facility_years"]
    )

    if args.dry_run:
        log.info("DRY RUN — no database writes")

    session = requests.Session()
    session.headers["User-Agent"] = NOMINATIM_UA

    total_attempted = total_succeeded = total_skipped = 0

    for table in tables:
        attempted, succeeded, skipped = geocode_table(
            conn, table, args.dry_run, args.limit, session
        )
        total_attempted += attempted
        total_succeeded += succeeded
        total_skipped   += skipped
        log.info("%s: attempted=%d, geocoded=%d, no_address=%d",
                 table, attempted, succeeded, skipped)

    conn.close()

    print(f"\n── Geocoding summary ────────────────────────────────────────")
    print(f"  Rows attempted:       {total_attempted}")
    print(f"  Successfully geocoded:{total_succeeded}")
    print(f"  Skipped (no address): {total_skipped}")
    match_rate = total_succeeded / total_attempted if total_attempted else 0
    print(f"  Match rate:           {match_rate:.1%}")
    print(f"────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
