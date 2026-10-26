#!/usr/bin/env python3
"""
06_load_database.py — Load all extracted CBM data into PostgreSQL.

Reads data/output/*.csv and catalogue.json and loads them into the database.

Default (incremental) mode only adds documents that are not in the database
yet. Rows for new documents are matched to existing facility entities by name
(the same fuzzy rules as script 05) or given fresh IDs, so existing rows —
manual corrections (migrations 014–018), merged entities and geocodes — are
never touched. Afterwards run 07_geocode.py to geocode the new rows.

--full truncates every table and rebuilds from the CSVs. It reverts manual
corrections, and canonical IDs from script 05 are positional, so geocodes and
entity merges keyed on them can be lost. It asks for typed confirmation.

The database connection is read from DATABASE_URL in .env:
    DATABASE_URL=postgresql://cbm:cbm@localhost:5432/cbm

Usage:
    python scripts/06_load_database.py                 # add new documents only
    python scripts/06_load_database.py --dry-run       # show what would be added
    python scripts/06_load_database.py --full          # wipe + rebuild (confirms)
    python scripts/06_load_database.py --table NAME    # wipe + reload one table (confirms)

Prerequisites:
    docker compose up -d   # start the database
    ./db/migrate.sh        # apply schema migrations
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator
from rapidfuzz import fuzz

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

# ── Catalogue validation ──────────────────────────────────────────────────────


class CatalogueEntry(BaseModel):
    """Validated catalogue entry — catches missing/malformed fields early.

    Uses extra="allow" because the catalogue accumulates optional metadata
    fields over time (ocr_engine, page_count, etc.) that are not needed by
    the DB loader but should not cause validation failures.
    """
    model_config = ConfigDict(extra="allow")

    id: str
    country: str
    country_iso3: str
    year: int
    language: str | None = None
    source_url: str | None = None
    downloaded: bool = False
    is_amendment: bool = False

    @field_validator("country_iso3")
    @classmethod
    def validate_iso3(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError(f"Invalid ISO3 code: {v}")
        return v

    @field_validator("year")
    @classmethod
    def validate_year(cls, v: int) -> int:
        if not (1980 <= v <= 2099):
            raise ValueError(f"Year out of range: {v}")
        return v


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


def _csv_rows(name: str, docs: set[str] | None):
    """Rows of data/output/<name>, optionally only those from `docs`.
    Returns None (after a warning) if the file is missing."""
    path = OUTPUT_DIR / name
    if not path.exists():
        log.warning("%s not found; skipping", name)
        return None
    with path.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if docs is None or r["source_document"] in docs]


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


def load_facility_years(cur, docs=None, resolve=None) -> int:
    """resolve(row) -> canonical id overrides the CSV's positional id."""
    csv_rows = _csv_rows("all_facilities.csv", docs)
    if csv_rows is None:
        return 0
    rows = []
    for r in csv_rows:
        rows.append((
            resolve(r) if resolve else r["canonical_facility_id"],
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


def load_vaccine_facility_years(cur, docs=None, resolve=None) -> int:
    csv_rows = _csv_rows("all_vaccine_facilities.csv", docs)
    if csv_rows is None:
        return 0
    rows = []
    for r in csv_rows:
        rows.append((
            resolve(r) if resolve else _str(r.get("canonical_vaccine_facility_id")),
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


def load_defence_programmes(cur, docs=None) -> int:
    csv_rows = _csv_rows("defence_programmes.csv", docs)
    if csv_rows is None:
        return 0
    rows = []
    for r in csv_rows:
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


def load_defence_facilities(cur, docs=None, resolve=None) -> int:
    csv_rows = _csv_rows("defence_facilities.csv", docs)
    if csv_rows is None:
        return 0
    if resolve is None:
        # Entity rows must exist before facilities reference them (FK);
        # aggregates are filled in by refresh_defence_entities() afterwards.
        _insert_entity_stubs(cur, "defence_entities", "canonical_defence_facility_id", {
            r["canonical_defence_facility_id"]: (r["country_iso3"], _str(r["facility_name"]))
            for r in csv_rows if r.get("canonical_defence_facility_id")
        })
    rows = []
    for r in csv_rows:
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
            resolve(r) if resolve else _str(r.get("canonical_defence_facility_id")),
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


def load_past_programmes(cur, docs=None) -> int:
    csv_rows = _csv_rows("past_programmes.csv", docs)
    if csv_rows is None:
        return 0
    rows = []
    for r in csv_rows:
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


def load_legislation(cur, docs=None) -> int:
    csv_rows = _csv_rows("legislation.csv", docs)
    if csv_rows is None:
        return 0
    rows = []
    for r in csv_rows:
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


# ── Entity tables ─────────────────────────────────────────────────────────────


def _insert_entity_stubs(cur, table: str, id_col: str, entities: dict[str, tuple]) -> None:
    """Insert minimal rows for entity ids (id -> (country_iso3, name)) that
    don't exist yet, so child rows can reference them. Existing entity rows —
    including curated canonical names — are left alone."""
    psycopg2.extras.execute_batch(cur, f"""
        INSERT INTO {table} ({id_col}, country_iso3, canonical_name)
        VALUES (%s, %s, %s)
        ON CONFLICT ({id_col}) DO NOTHING
    """, [(eid, iso3, name) for eid, (iso3, name) in entities.items()])


def refresh_facilities(cur, ids: list[str]) -> None:
    """Recompute aggregates of research facility entities from facility_years.
    Appends new name variants (keeps curated ones and the canonical name)."""
    cur.execute("""
        UPDATE facilities f
        SET    all_names = ARRAY(
                   SELECT DISTINCT n FROM unnest(
                       f.all_names || ARRAY(SELECT facility_name FROM facility_years fy
                                            WHERE fy.canonical_facility_id = f.canonical_facility_id
                                            AND   fy.facility_name IS NOT NULL)) AS n),
               years_declared = ARRAY(
                   SELECT DISTINCT year::smallint FROM facility_years fy
                   WHERE fy.canonical_facility_id = f.canonical_facility_id ORDER BY 1),
               latest_containment = latest.highest_containment,
               latest_area_m2     = COALESCE(latest.bsl4_area_m2, latest.bsl3_area_m2)
        FROM  (SELECT DISTINCT ON (canonical_facility_id)
                      canonical_facility_id, highest_containment, bsl4_area_m2, bsl3_area_m2
               FROM   facility_years
               WHERE  canonical_facility_id = ANY(%(ids)s)
               ORDER  BY canonical_facility_id, year DESC) latest
        WHERE  f.canonical_facility_id = latest.canonical_facility_id
    """, {"ids": ids})


def refresh_vaccine_facilities(cur, ids: list[str]) -> None:
    cur.execute("""
        UPDATE vaccine_facilities v
        SET    first_year = agg.first_year, last_year = agg.last_year
        FROM  (SELECT canonical_vaccine_facility_id AS id,
                      MIN(year) AS first_year, MAX(year) AS last_year
               FROM   vaccine_facility_years
               WHERE  canonical_vaccine_facility_id = ANY(%(ids)s)
               GROUP  BY 1) agg
        WHERE  v.id = agg.id
    """, {"ids": ids})


def refresh_defence_entities(cur, ids: list[str] | None = None) -> None:
    """Recompute first/last year and name variants from defence_facilities
    (all entities when ids is None). Canonical names are left as they are."""
    cur.execute("""
        UPDATE defence_entities d
        SET    first_year = agg.first_year,
               last_year  = agg.last_year,
               all_names  = ARRAY(SELECT DISTINCT n FROM unnest(
                                COALESCE(d.all_names, '{}') || agg.names) AS n)
        FROM  (SELECT canonical_defence_facility_id AS id,
                      MIN(year) AS first_year, MAX(year) AS last_year,
                      ARRAY_AGG(facility_name) FILTER (WHERE facility_name IS NOT NULL) AS names
               FROM   defence_facilities
               WHERE  canonical_defence_facility_id IS NOT NULL
               AND    (%(ids)s::text[] IS NULL OR canonical_defence_facility_id = ANY(%(ids)s))
               GROUP  BY 1) agg
        WHERE  d.canonical_defence_facility_id = agg.id
    """, {"ids": ids})


# ── Incremental entity matching ───────────────────────────────────────────────


def _load_script05():
    """Import 05_assemble_output.py (not importable by name: leading digit)."""
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("assemble", PROJECT_ROOT / "scripts" / "05_assemble_output.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EntityMatcher:
    """Assign canonical ids to rows from new documents.

    A row joins the existing entity (same country) whose known name variant
    scores highest on token_sort_ratio, using script 05's thresholds (stricter
    when that variant was declared in the same year, since one submission
    lists distinct facilities) and its anchor merges. Otherwise it gets a new
    id numbered after every id the country has ever had — in the database and
    in script 05's positional output — so ids retired by entity merges are
    never reused. New entities join the pool, so later documents match them.

    Rows without a facility name join the country's one existing entity that
    has only ever been unnamed (e.g. DNK's yearly redacted entry), if any.
    """

    @staticmethod
    def _unnamed(name: str | None) -> bool:
        return not name or name.strip().lower() in ("", "[unnamed facility]")

    def __init__(self, id_format: str, known: list[tuple[str, str, str, int | None]],
                 used_ids: set[str], anchors=(), same_year_threshold=95, threshold=85):
        # known: (entity_id, country_iso3, name, year)
        self.id_format = id_format
        self.anchors = list(anchors)
        self.same_year_threshold = same_year_threshold
        self.threshold = threshold
        self.pool: dict[str, list[tuple[str, str, int | None]]] = {}
        named: set[str] = set()
        countries: dict[str, str] = {}
        for eid, iso3, name, year in known:
            countries[eid] = iso3
            if name:
                self.pool.setdefault(iso3, []).append((eid, name, year))
            if not self._unnamed(name):
                named.add(eid)
        self.unnamed_ids: dict[str, set[str]] = {}
        for eid, iso3 in countries.items():
            if eid not in named:
                self.unnamed_ids.setdefault(iso3, set()).add(eid)
        prefix_re = re.compile(re.escape(id_format.split("{n")[0]).replace(r"\{country\}", "([A-Z]{3})")
                               + r"(\d+)$")
        self.next_n: dict[str, int] = {}
        for eid in used_ids | {e for e, *_ in known}:
            m = prefix_re.match(eid or "")
            if m:
                self.next_n[m.group(1)] = max(self.next_n.get(m.group(1), 0), int(m.group(2)))
        self.created: list[tuple[str, str]] = []   # (new id, name)
        self.matched: dict[str, str] = {}          # name -> existing id

    def resolve(self, iso3: str, name: str | None, year: int | None) -> str:
        candidates = self.pool.setdefault(iso3, [])
        best_id, best = None, -1.0
        if self._unnamed(name) and len(self.unnamed_ids.get(iso3, ())) == 1:
            best_id = next(iter(self.unnamed_ids[iso3]))
        lname = (name or "").lower()
        for anchor_iso3, anchor in self.anchors:
            if anchor_iso3 == iso3 and anchor.lower() in lname:
                for eid, other, _ in candidates:
                    if anchor.lower() in other.lower():
                        best_id, best = eid, 101.0
                        break
        if name and best_id is None:
            for eid, other, other_year in candidates:
                need = self.same_year_threshold if year and other_year == year else self.threshold
                score = fuzz.token_sort_ratio(name, other)
                if score >= need and score > best:
                    best_id, best = eid, score
        if best_id is None:
            n = self.next_n.get(iso3, 0) + 1
            self.next_n[iso3] = n
            best_id = self.id_format.format(country=iso3, n=n)
            self.created.append((best_id, name or ""))
            if self._unnamed(name):
                self.unnamed_ids.setdefault(iso3, set()).add(best_id)
        elif name:
            self.matched.setdefault(name, best_id)
        if name:
            candidates.append((best_id, name, year))
        return best_id


def _csv_ids(name: str, col: str) -> set[str]:
    rows = _csv_rows(name, None) or []
    return {r[col] for r in rows if r.get(col)}


def load_incremental(cur, catalogue: list[dict], new_docs: set[str]) -> dict:
    """Load only `new_docs`; returns a summary of what was added."""
    s05 = _load_script05()
    summary: dict = {}

    summary["documents"] = load_documents(cur, [e for e in catalogue if e["id"] in new_docs])

    # Research facilities (A1)
    cur.execute("SELECT canonical_facility_id, country_iso3, facility_name, year FROM facility_years")
    known = list(cur.fetchall())
    cur.execute("SELECT canonical_facility_id, country_iso3, unnest(all_names), NULL FROM facilities")
    known += cur.fetchall()
    a1 = EntityMatcher("{country}_{n:03d}", known,
                       _csv_ids("all_facilities.csv", "canonical_facility_id"),
                       anchors=s05.ANCHOR_MERGES, same_year_threshold=s05.SAME_YEAR_THRESHOLD,
                       threshold=s05.SIMILARITY_THRESHOLD)
    a1_rows = _csv_rows("all_facilities.csv", new_docs) or []
    a1_ids = {id(r): a1.resolve(r["country_iso3"], _str(r["facility_name"]), _int(r["year"]))
              for r in a1_rows}
    _insert_entity_stubs(cur, "facilities", "canonical_facility_id", {
        a1_ids[id(r)]: (r["country_iso3"], _str(r["facility_name"])) for r in a1_rows})
    for eid, name in a1.created:
        cur.execute("UPDATE facilities SET all_names = ARRAY[%s] WHERE canonical_facility_id = %s",
                    (name, eid))
    by_key = {(r["source_document"], r["facility_name"], r["year"]): a1_ids[id(r)] for r in a1_rows}
    summary["facility_years"] = load_facility_years(
        cur, new_docs, lambda r: by_key[(r["source_document"], r["facility_name"], r["year"])])
    refresh_facilities(cur, sorted(set(a1_ids.values())))

    # Vaccine facilities (G)
    cur.execute("""SELECT canonical_vaccine_facility_id, country_iso3, facility_name, year
                   FROM vaccine_facility_years WHERE canonical_vaccine_facility_id IS NOT NULL""")
    known = list(cur.fetchall())
    cur.execute("SELECT id, country_iso3, canonical_name, NULL FROM vaccine_facilities")
    known += cur.fetchall()
    g = EntityMatcher("{country}_V{n:03d}", known,
                      _csv_ids("all_vaccine_facilities.csv", "canonical_vaccine_facility_id"),
                      same_year_threshold=s05.SAME_YEAR_THRESHOLD, threshold=s05.SIMILARITY_THRESHOLD)
    g_rows = _csv_rows("all_vaccine_facilities.csv", new_docs) or []
    g_ids = {id(r): g.resolve(r["country_iso3"], _str(r["facility_name"]), _int(r["year"]))
             for r in g_rows}
    _insert_entity_stubs(cur, "vaccine_facilities", "id", {
        g_ids[id(r)]: (r["country_iso3"], _str(r["facility_name"])) for r in g_rows})
    by_key = {(r["source_document"], r["facility_name"], r["year"]): g_ids[id(r)] for r in g_rows}
    summary["vaccine_facility_years"] = load_vaccine_facility_years(
        cur, new_docs, lambda r: by_key[(r["source_document"], r["facility_name"], r["year"])])
    refresh_vaccine_facilities(cur, sorted(set(g_ids.values())))

    # Defence facilities (A2)
    cur.execute("""SELECT canonical_defence_facility_id, country_iso3, facility_name, year
                   FROM defence_facilities WHERE canonical_defence_facility_id IS NOT NULL""")
    known = list(cur.fetchall())
    cur.execute("SELECT canonical_defence_facility_id, country_iso3, unnest(all_names), NULL FROM defence_entities")
    known += cur.fetchall()
    d = EntityMatcher("{country}_D{n:03d}", known,
                      _csv_ids("defence_facilities.csv", "canonical_defence_facility_id"),
                      same_year_threshold=s05.SAME_YEAR_THRESHOLD, threshold=s05.SIMILARITY_THRESHOLD)
    d_rows = _csv_rows("defence_facilities.csv", new_docs) or []
    d_ids = {id(r): d.resolve(r["country_iso3"], _str(r["facility_name"]), _int(r["year"]))
             for r in d_rows}
    _insert_entity_stubs(cur, "defence_entities", "canonical_defence_facility_id", {
        d_ids[id(r)]: (r["country_iso3"], _str(r["facility_name"])) for r in d_rows})
    by_key = {(r["source_document"], r["facility_name"], r["year"]): d_ids[id(r)] for r in d_rows}
    summary["defence_facilities"] = load_defence_facilities(
        cur, new_docs, lambda r: by_key[(r["source_document"], r["facility_name"], r["year"])])
    refresh_defence_entities(cur, sorted(set(d_ids.values())))

    summary["defence_programmes"] = load_defence_programmes(cur, new_docs)
    summary["past_programmes"]    = load_past_programmes(cur, new_docs)
    summary["legislation"]        = load_legislation(cur, new_docs)
    summary["form_compliance"]    = load_form_compliance(cur, new_docs)
    summary["matchers"] = {"research": a1, "vaccine": g, "defence": d}
    return summary


def load_form_compliance(cur, docs=None) -> int:
    csv_rows = _csv_rows("form_compliance.csv", docs)
    if csv_rows is None:
        return 0
    rows = []
    for r in csv_rows:
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
    "defence_entities",
    "defence_programmes",
    "vaccine_facility_years",
    "vaccine_facilities",
    "facility_years",
    "facilities",
    "documents",
]


# ── Main ──────────────────────────────────────────────────────────────────────


def _confirm_destructive(cur, what: str) -> bool:
    """Show what a destructive reload would affect; require the user to type 'wipe'."""
    counts = {}
    for t in TRUNCATE_ORDER:
        cur.execute(f"SELECT count(*) FROM {t}")
        counts[t] = cur.fetchone()[0]
    print(f"\n{what} will DELETE and reload from data/output/:")
    for t, n in counts.items():
        print(f"  {t:<24} {n:>6} rows")
    print("Manual corrections (migrations 014–018) and entity merges are reverted;")
    print("geocodes are restored only where (document, facility name) still match.")
    print("Usually you want the default incremental mode instead.")
    if not sys.stdin.isatty():
        print("Refusing: not an interactive terminal.")
        return False
    return input("Type 'wipe' to continue: ").strip() == "wipe"


def _full_reload(cur, catalogue: list[dict]) -> None:
    """Snapshot geocodes, truncate everything, reload from CSVs, restore geocodes."""
    # Full reload: snapshot geom columns, truncate, reload, restore geom
    log.info("Saving geocoded coordinates before truncation …")
    cur.execute("""
        SELECT fy.document_id, fy.facility_name,
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
    refresh_defence_entities(cur)

    # Restore geocoded geometry for facility_years.
    # After restoration, verify how many rows actually matched to
    # detect silent losses from entity ID changes between runs.
    if fy_geom:
        psycopg2.extras.execute_batch(cur, """
            UPDATE facility_years
            SET    geom               = %s,
                   geocode_source     = %s,
                   geocode_confidence = %s
            WHERE  document_id   = %s
            AND    facility_name = %s
        """, [
            (row[2], row[3], row[4], row[0], row[1])
            for row in fy_geom
        ])
        cur.execute("SELECT count(*) FROM facility_years WHERE geom IS NOT NULL")
        actual = cur.fetchone()[0]
        log.info("  Restored facility_years geom: %d/%d matched", actual, len(fy_geom))
        if actual < len(fy_geom):
            log.warning("  ⚠ %d facility_years geom rows not restored "
                        "(facility names may have changed)", len(fy_geom) - actual)

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true",
                      help="Truncate every table and rebuild from the CSVs (asks to confirm).")
    mode.add_argument("--table", metavar="NAME",
                      help="Truncate and reload one table (asks to confirm). CASCADE also "
                           "empties tables that reference it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Incremental mode: show what would be added, then roll back.")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    db_url = os.getenv("DATABASE_URL", "postgresql://cbm:cbm@localhost:5432/cbm")

    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        log.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    raw_catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    catalogue: list[dict] = []
    for i, entry in enumerate(raw_catalogue):
        try:
            validated = CatalogueEntry(**entry)
            catalogue.append(validated.model_dump())
        except Exception as exc:
            log.warning("Catalogue entry %d failed validation: %s — skipping", i, exc)
    log.info("Loaded catalogue: %d entries (%d skipped)",
             len(catalogue), len(raw_catalogue) - len(catalogue))

    # One transaction: any failure rolls everything back, so the database is
    # never left half-loaded. `with conn` commits on success.
    with conn:
        with conn.cursor() as cur:
            if args.table:
                names = {n for n, _, _ in ALL_TABLES}
                if args.table not in names:
                    log.error("Unknown table: %s (one of: %s)", args.table, ", ".join(sorted(names)))
                    sys.exit(1)
                if not _confirm_destructive(cur, f"Reloading {args.table}"):
                    sys.exit("Aborted; nothing changed.")
                cur.execute(f"TRUNCATE TABLE {args.table} CASCADE")
                for name, fn, needs_cat in ALL_TABLES:
                    if name == args.table:
                        n = fn(cur, catalogue) if needs_cat else fn(cur)
                        log.info("Loaded %s: %d rows", name, n)
                if args.table == "defence_facilities":
                    refresh_defence_entities(cur)
            elif args.full:
                if not _confirm_destructive(cur, "A full reload"):
                    sys.exit("Aborted; nothing changed.")
                _full_reload(cur, catalogue)
            else:
                cur.execute("SELECT id FROM documents")
                loaded = {r[0] for r in cur.fetchall()}
                new_docs = {e["id"] for e in catalogue
                            if e["id"] not in loaded and (e.get("downloaded") or e.get("is_amendment"))}
                if not new_docs:
                    log.info("No new documents — database is up to date.")
                    return
                log.info("%d new documents: %s", len(new_docs), ", ".join(sorted(new_docs)))
                summary = load_incremental(cur, catalogue, new_docs)
                for label, m in summary.pop("matchers").items():
                    for eid, name in m.created:
                        log.info("  new %-8s entity %-10s %s", label, eid, name)
                    for name, eid in sorted(m.matched.items()):
                        log.info("  matched %-8s %-10s <- %s", label, eid, name)
                for table, n in summary.items():
                    log.info("  %-28s +%d rows", table, n)
                if args.dry_run:
                    conn.rollback()
                    log.info("Dry run — rolled back; nothing changed.")
                    return
                log.info("Next: python3 scripts/07_geocode.py to geocode the new rows.")

    conn.close()
    log.info("Database load complete.")


if __name__ == "__main__":
    main()
