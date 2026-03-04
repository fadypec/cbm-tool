#!/usr/bin/env python3
"""
05_assemble_output.py — Assemble structured facility data into final outputs.

Reads data/structured/*_form_a1.json, performs within-country entity
resolution using rapidfuzz name matching, and writes four output files:
  data/output/all_facilities.csv
  data/output/all_facilities.json
  data/output/summary_stats.json
  data/output/entity_registry.json

Also reads data/structured/*_form_g.json (if present) and writes:
  data/output/all_vaccine_facilities.csv
  data/output/all_vaccine_facilities.json

Usage:
    python scripts/05_assemble_output.py
"""

import csv
import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT   = Path(__file__).resolve().parent.parent
STRUCTURED_DIR = PROJECT_ROOT / "data" / "structured"
OUTPUT_DIR     = PROJECT_ROOT / "data" / "output"

# ── Constants ────────────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD = 85   # token_sort_ratio score to merge two facility names

CSV_FIELDS = [
    "country_iso3", "year", "facility_name", "responsible_org",
    "city", "address", "funding_sources", "mod_funded",
    "has_bsl4", "bsl4_area_m2", "has_bsl3", "bsl3_area_m2",
    "highest_containment", "agents_summary", "agents_redacted",
    "confidence", "translated", "canonical_facility_id", "source_document",
]

CSV_FIELDS_G = [
    "country_iso3", "year", "facility_name",
    "city", "address", "diseases_covered", "vaccines_summary",
    "confidence", "translated", "source_document",
]


# ── Union-Find ────────────────────────────────────────────────────────────────


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        if x not in self._parent:
            self._parent[x] = x
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])  # path compression
        return self._parent[x]

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self._parent[ry] = rx


# ── Data loading ──────────────────────────────────────────────────────────────


def load_catalogue_index() -> dict[str, str]:
    """Return {source_id: language} from catalogue.json."""
    cat_path = PROJECT_ROOT / "data" / "catalogue.json"
    if not cat_path.exists():
        return {}
    catalogue = json.loads(cat_path.read_text(encoding="utf-8"))
    return {e["id"]: e.get("language", "en") for e in catalogue}


def load_all_facilities() -> list[dict]:
    """Return a flat list of all facility dicts from every structured JSON."""
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_a1.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records.extend(data.get("facilities", []))
    return records


def load_all_vaccine_facilities() -> list[dict]:
    """Return a flat list of all vaccine facility dicts from every Form G JSON."""
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_g.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records.extend(data.get("vaccine_facilities", []))
    return records


# ── Entity resolution ─────────────────────────────────────────────────────────


def resolve_entities(records: list[dict]) -> dict[int, str]:
    """
    Assign a canonical_facility_id to each record.

    Facilities within the same country whose names score ≥ SIMILARITY_THRESHOLD
    on rapidfuzz.fuzz.token_sort_ratio are merged into one canonical entity.

    Returns a dict: record_index → canonical_facility_id (e.g. "USA_001").
    """
    # Group record indices by country
    by_country: dict[str, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        country = rec.get("extraction_metadata", {}).get("country_iso3", "UNK")
        by_country[country].append(i)

    uf = UnionFind()

    for indices in by_country.values():
        names = [(i, records[i].get("facility_name") or "") for i in indices]
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                ia, na = names[a]
                ib, nb = names[b]
                if not na or not nb:
                    continue
                if fuzz.token_sort_ratio(na, nb) >= SIMILARITY_THRESHOLD:
                    uf.union(ia, ib)

    # Group by country → root → member indices
    country_groups: dict[str, dict[int, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for country, indices in by_country.items():
        for i in indices:
            country_groups[country][uf.find(i)].append(i)

    # Assign stable IDs sorted by the earliest-appearing member index per group
    id_map: dict[int, str] = {}
    for country in sorted(country_groups.keys()):
        # Sort groups by minimum record index so ordering is deterministic
        groups = sorted(country_groups[country].values(), key=min)
        for n, members in enumerate(groups, 1):
            cid = f"{country}_{n:03d}"
            for i in members:
                id_map[i] = cid

    return id_map


# ── Flattening ────────────────────────────────────────────────────────────────


def flatten_record(rec: dict, cid: str, cat_index: dict[str, str]) -> dict:
    """Flatten one facility dict into a CSV-ready dict."""
    meta    = rec.get("extraction_metadata", {}) or {}
    loc     = rec.get("location", {})             or {}
    funding = rec.get("funding", {})              or {}
    cont    = rec.get("containment", {})          or {}
    agents  = rec.get("agents", {})               or {}

    # translated: use the field set by Claude if present; fall back to
    # catalogue language (anything other than English means AI-translated).
    source_id = meta.get("source_id", "")
    if "translated" in rec:
        translated = rec["translated"]
    else:
        lang = meta.get("language") or cat_index.get(source_id, "en")
        translated = lang != "en"

    return {
        "country_iso3":        meta.get("country_iso3"),
        "year":                meta.get("year"),
        "facility_name":       rec.get("facility_name"),
        "responsible_org":     rec.get("responsible_organisation"),
        "city":                loc.get("city"),
        "address":             loc.get("address"),
        "funding_sources":     "; ".join(funding.get("sources") or []),
        "mod_funded":          funding.get("mod_funded"),
        "has_bsl4":            cont.get("has_bsl4"),
        "bsl4_area_m2":        cont.get("bsl4_area_m2"),
        "has_bsl3":            cont.get("has_bsl3"),
        "bsl3_area_m2":        cont.get("bsl3_area_m2"),
        "highest_containment": cont.get("highest_containment"),
        "agents_summary":      "; ".join(agents.get("listed") or []),
        "agents_redacted":     agents.get("redacted"),
        "confidence":          rec.get("confidence"),
        "translated":          translated,
        "canonical_facility_id": cid,
        "source_document":     meta.get("source_id"),
    }


def flatten_vaccine_record(rec: dict) -> dict:
    """Flatten one vaccine facility dict into a CSV-ready dict."""
    meta = rec.get("extraction_metadata", {}) or {}
    loc  = rec.get("location", {}) or {}

    if "translated" in rec:
        translated = rec["translated"]
    else:
        translated = meta.get("language", "en") != "en"

    return {
        "country_iso3":     meta.get("country_iso3"),
        "year":             meta.get("year"),
        "facility_name":    rec.get("facility_name"),
        "city":             loc.get("city"),
        "address":          loc.get("address"),
        "diseases_covered": rec.get("diseases_covered"),
        "vaccines_summary": "; ".join(rec.get("vaccines") or []),
        "confidence":       rec.get("confidence"),
        "translated":       translated,
        "source_document":  meta.get("source_id"),
    }


# ── Entity registry ───────────────────────────────────────────────────────────


def build_entity_registry(
    records: list[dict], id_map: dict[int, str]
) -> list[dict]:
    """
    Build a canonical entity list — one entry per unique facility.

    Uses the most recent year's record for containment and area data.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for i, rec in enumerate(records):
        groups[id_map[i]].append(rec)

    registry: list[dict] = []
    for cid in sorted(groups.keys()):
        members = sorted(
            groups[cid],
            key=lambda r: r.get("extraction_metadata", {}).get("year", 0),
        )
        latest = members[-1]
        latest_cont = latest.get("containment", {}) or {}

        years = sorted({
            r.get("extraction_metadata", {}).get("year")
            for r in members
            if r.get("extraction_metadata", {}).get("year")
        })

        # All distinct names in chronological order (deduped, preserving order)
        seen: set[str] = set()
        all_names: list[str] = []
        for r in members:
            n = r.get("facility_name") or ""
            if n and n not in seen:
                seen.add(n)
                all_names.append(n)

        # Prefer BSL-4 area; fall back to BSL-3 area
        latest_area = latest_cont.get("bsl4_area_m2") or latest_cont.get("bsl3_area_m2")

        registry.append({
            "canonical_facility_id": cid,
            "canonical_name":        all_names[-1] if all_names else None,
            "country_iso3":          (latest.get("extraction_metadata") or {}).get("country_iso3"),
            "all_names":             all_names,
            "years_declared":        years,
            "latest_containment":    latest_cont.get("highest_containment"),
            "latest_area_m2":        latest_area,
        })

    return registry


# ── Summary stats ─────────────────────────────────────────────────────────────


def build_summary_stats(
    flat: list[dict], registry: list[dict], vaccine_flat: list[dict] | None = None
) -> dict:
    """Compute dataset-level summary statistics."""
    confidences = [r["confidence"] for r in flat if r["confidence"] is not None]

    countries = sorted({r["country_iso3"] for r in flat if r["country_iso3"]})
    years     = sorted({r["year"]         for r in flat if r["year"]})

    # Count unique facilities with BSL-4 / BSL-3 per country (from registry)
    bsl4_by_country: dict[str, int] = defaultdict(int)
    bsl3_by_country: dict[str, int] = defaultdict(int)
    for entity in registry:
        country = entity.get("country_iso3") or "UNK"
        cont    = entity.get("latest_containment") or ""
        if cont == "BSL-4":
            bsl4_by_country[country] += 1
        if cont in ("BSL-4", "BSL-3"):
            bsl3_by_country[country] += 1

    result = {
        "total_facility_year_records": len(flat),
        "unique_facilities":           len(registry),
        "countries_covered":           countries,
        "years_covered":               years,
        "mean_extraction_confidence":  (
            round(statistics.mean(confidences), 3) if confidences else None
        ),
        "bsl4_facilities_by_country":  dict(sorted(bsl4_by_country.items())),
        "bsl3_facilities_by_country":  dict(sorted(bsl3_by_country.items())),
    }
    if vaccine_flat is not None:
        result["total_vaccine_facility_year_records"] = len(vaccine_flat)
        vc = sorted({r["country_iso3"] for r in vaccine_flat if r["country_iso3"]})
        result["vaccine_countries_covered"] = vc
    return result


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Loading structured records from %s …", STRUCTURED_DIR)
    records = load_all_facilities()
    log.info("Loaded %d facility-year records", len(records))

    cat_index = load_catalogue_index()

    log.info("Resolving entities (threshold=%d)…", SIMILARITY_THRESHOLD)
    id_map = resolve_entities(records)
    unique = len({v for v in id_map.values()})
    log.info("Entity resolution complete: %d records → %d unique facilities",
             len(records), unique)

    # Flatten and sort
    flat = [flatten_record(rec, id_map[i], cat_index) for i, rec in enumerate(records)]
    flat.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0, r["facility_name"] or ""))

    # ── all_facilities.csv ────────────────────────────────────────────────────
    csv_path = OUTPUT_DIR / "all_facilities.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(flat)
    log.info("Wrote %s (%d rows)", csv_path, len(flat))

    # ── all_facilities.json ───────────────────────────────────────────────────
    json_path = OUTPUT_DIR / "all_facilities.json"
    json_path.write_text(
        json.dumps(flat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("Wrote %s", json_path)

    # ── entity_registry.json ──────────────────────────────────────────────────
    registry = build_entity_registry(records, id_map)
    reg_path = OUTPUT_DIR / "entity_registry.json"
    reg_path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("Wrote %s (%d entities)", reg_path, len(registry))

    # ── vaccine facilities (Form G) ───────────────────────────────────────────
    vaccine_records = load_all_vaccine_facilities()
    vaccine_flat: list[dict] | None = None
    if vaccine_records:
        log.info("Loaded %d vaccine facility-year records (Form G)", len(vaccine_records))
        vaccine_flat = [flatten_vaccine_record(r) for r in vaccine_records]
        vaccine_flat.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0, r["facility_name"] or ""))

        vcsv_path = OUTPUT_DIR / "all_vaccine_facilities.csv"
        with vcsv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_G)
            writer.writeheader()
            writer.writerows(vaccine_flat)
        log.info("Wrote %s (%d rows)", vcsv_path, len(vaccine_flat))

        vjson_path = OUTPUT_DIR / "all_vaccine_facilities.json"
        vjson_path.write_text(
            json.dumps(vaccine_flat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log.info("Wrote %s", vjson_path)
    else:
        log.info("No Form G data found; skipping vaccine facility outputs")

    # ── summary_stats.json ────────────────────────────────────────────────────
    stats = build_summary_stats(flat, registry, vaccine_flat)
    stats_path = OUTPUT_DIR / "summary_stats.json"
    stats_path.write_text(
        json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("Wrote %s", stats_path)

    # ── stdout summary ────────────────────────────────────────────────────────
    bsl4 = stats["bsl4_facilities_by_country"]
    bsl3 = stats["bsl3_facilities_by_country"]
    years = stats["years_covered"]

    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Facility-year records:   {stats['total_facility_year_records']}")
    print(f"  Unique facilities:       {stats['unique_facilities']}")
    if stats.get("total_vaccine_facility_year_records"):
        print(f"  Vaccine facility recs:   {stats['total_vaccine_facility_year_records']}")
    print(f"  Countries covered:       {len(stats['countries_covered'])}")
    print(f"  Years covered:           {min(years)}–{max(years)}")
    print(f"  Mean extraction conf.:   {stats['mean_extraction_confidence']:.3f}")
    print()
    if bsl4:
        print(f"  BSL-4 facilities ({sum(bsl4.values())} unique across {len(bsl4)} countries):")
        for country, n in sorted(bsl4.items(), key=lambda x: -x[1]):
            print(f"    {country}: {n}")
    print()
    if bsl3:
        print(f"  BSL-3 or higher ({sum(bsl3.values())} unique across {len(bsl3)} countries):")
        for country, n in sorted(bsl3.items(), key=lambda x: -x[1]):
            print(f"    {country}: {n}")
    print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
