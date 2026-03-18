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

Also reads data/structured/*_form_f.json (if present) and writes:
  data/output/past_programmes.csv

Also reads data/structured/*_form_e.json (if present) and writes:
  data/output/legislation.csv

Reads data/segmented/*/manifest.json and writes:
  data/output/compliance_matrix.csv   (one row per document, wide format)
  data/output/form_compliance.csv     (one row per document × form, long format)

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
SEGMENTED_DIR  = PROJECT_ROOT / "data" / "segmented"
OUTPUT_DIR     = PROJECT_ROOT / "data" / "output"

# ── Constants ────────────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD      = 85   # token_sort_ratio to merge facilities by name (cross-year)
SAME_YEAR_THRESHOLD       = 95   # higher bar when both records are from the same year
                                 # (same-year records are in the same CBM submission and
                                 # are therefore distinct facilities unless names are nearly
                                 # identical — prevents merging e.g. MEX state labs)

# Anchor-merge overrides: (country_iso3, anchor_substring) pairs.
# All records whose facility_name contains the anchor string (case-insensitive) are
# forced into one canonical entity, regardless of their token_sort_ratio score.
# Needed when a lab's formal name grows or shrinks with institutional context across
# years and the string similarity drops below the threshold, e.g.:
#   CAN: "National Microbiology Laboratory" (short form, 2013–2022)
#     vs "National Microbiology Laboratory, Public Health Agency of Canada,
#         Canadian Science Centre for Human and Animal Health" (full form, 2011–2019)
#     vs "National Microbiology Laboratory, Canadian Science Centre for Human
#         and Animal Health" (medium form, 2023–2025)
# All three refer to the same physical lab at 1015 Arlington Avenue, Winnipeg.
ANCHOR_MERGES: list[tuple[str, str]] = [
    ("CAN", "National Microbiology Laboratory"),
    ("CAN", "National Centre for Foreign Animal Disease"),
]

# Forms tracked in the compliance matrix (Form 0 is the cover page, not tracked)
COMPLIANCE_FORMS = ["A1", "A2", "B", "C", "E", "F", "G"]

CSV_FIELDS_COMPLIANCE_MATRIX = (
    ["country_iso3", "year", "source_document"]
    + [f"form_{f.lower()}" for f in COMPLIANCE_FORMS]
    + ["forms_substantive_count", "forms_ntd_count", "forms_absent_count"]
)

CSV_FIELDS_FORM_COMPLIANCE = [
    "country_iso3", "year", "source_document", "form", "status",
]

CSV_FIELDS_DEFENCE_PROGRAMMES = [
    "country_iso3", "year", "programme_name", "responsible_org",
    "objectives_summary", "research_areas", "total_funding_amount",
    "total_funding_currency", "uses_contractors", "contractor_proportion_pct",
    "confidence", "translated", "source_document",
]

CSV_FIELDS_DEFENCE_FACILITIES = [
    "country_iso3", "year", "canonical_defence_facility_id",
    "facility_name", "city", "address",
    "bsl2_area_m2", "bsl3_area_m2", "bsl4_area_m2", "total_lab_area_m2",
    "personnel_total", "personnel_military", "personnel_civilian",
    "personnel_scientists", "personnel_engineers", "personnel_technicians",
    "personnel_admin", "mod_funded", "funding_source",
    "funding_research", "funding_development", "funding_te", "funding_currency",
    "work_description", "confidence", "translated", "source_document",
]

CSV_FIELDS_PAST_PROGRAMMES = [
    "country_iso3", "year", "convention_entry_date",
    "has_offensive_programme", "offensive_period", "offensive_summary",
    "has_defensive_programme", "defensive_period", "defensive_summary",
    "translated", "confidence", "notes", "source_document",
]

_CATEGORIES = ["prohibitions", "exports", "imports", "biosafety"]
_CAT_FIELDS  = ["legislation", "regulations", "other_measures", "amended"]

CSV_FIELDS_LEGISLATION = (
    ["country_iso3", "year"]
    + [f"{cat}_{fld}" for cat in _CATEGORIES for fld in _CAT_FIELDS]
    + ["key_laws", "translated", "confidence", "notes",
       "input_truncated", "source_document"]
)

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
    "confidence", "translated", "canonical_vaccine_facility_id", "source_document",
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


def load_catalogue() -> list[dict]:
    """Return the full catalogue list from catalogue.json."""
    cat_path = PROJECT_ROOT / "data" / "catalogue.json"
    if not cat_path.exists():
        return []
    return json.loads(cat_path.read_text(encoding="utf-8"))


def load_catalogue_index() -> dict[str, str]:
    """Return {source_id: language} from catalogue.json."""
    return {e["id"]: e.get("language", "en") for e in load_catalogue()}


def get_secondary_source_ids(catalogue: list[dict]) -> set[str]:
    """
    Return the set of source IDs that are secondary (non-preferred) language
    versions of a submission for which a preferred-language version exists.

    Some countries (Canada, Switzerland) submit the same CBM year in multiple
    languages (e.g. English + French, or German + French + English).  Script 04
    processes every downloaded document independently, so the same facilities
    are extracted once per language version.  When these near-identical records
    are fed into entity resolution, the slightly different translated names
    produce spurious separate canonical entities (e.g. CAN_003/004 for the NML
    alongside CAN_001; CHE_007 "Labor Spiez" alongside CHE_001 "Spiez
    Laboratory").

    Strategy: for each (country_iso3, year) pair that has > 1 document, elect
    one as primary — preferring the English document; if none is English, the
    lexicographically first ID wins.  All others are marked secondary and
    excluded from facility loading, so entity resolution sees only one set of
    names per submission.
    """
    from collections import defaultdict

    by_country_year: dict[tuple, list[dict]] = defaultdict(list)
    for entry in catalogue:
        if entry.get("is_amendment") or not entry.get("downloaded"):
            continue
        key = (entry.get("country_iso3"), entry.get("year"))
        by_country_year[key].append(entry)

    secondary: set[str] = set()
    for entries in by_country_year.values():
        if len(entries) <= 1:
            continue
        english = [e for e in entries if e.get("language") == "en"]
        # Prefer English; fall back to alphabetically first ID
        primary_id = sorted(english or entries, key=lambda e: e["id"])[0]["id"]
        for entry in entries:
            if entry["id"] != primary_id:
                secondary.add(entry["id"])

    if secondary:
        log.info(
            "Bilingual deduplication: skipping %d secondary-language source IDs: %s",
            len(secondary), sorted(secondary),
        )
    return secondary


def load_all_facilities(secondary_ids: set[str] | None = None) -> list[dict]:
    """Return a flat list of all facility dicts from every structured JSON.

    secondary_ids: if provided, records whose source_id is in this set are
    skipped.  Used to avoid loading the same facilities twice when a country
    submitted the same CBM year in multiple languages (see
    get_secondary_source_ids).
    """
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_a1.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for fac in data.get("facilities", []):
            src = (fac.get("extraction_metadata") or {}).get("source_id", "")
            if secondary_ids and src in secondary_ids:
                continue  # duplicate language version — prefer the English doc
            records.append(fac)
    return records


def load_all_vaccine_facilities(secondary_ids: set[str] | None = None) -> list[dict]:
    """Return a flat list of all vaccine facility dicts from every Form G JSON.

    secondary_ids: same bilingual deduplication as load_all_facilities.
    """
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_g.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for fac in data.get("vaccine_facilities", []):
            src = (fac.get("extraction_metadata") or {}).get("source_id", "")
            if secondary_ids and src in secondary_ids:
                continue  # duplicate language version — prefer the English doc
            records.append(fac)
    return records


# ── Form A Part 2 loaders ────────────────────────────────────────────────────


def load_all_defence_programmes(secondary_ids: set[str] | None = None) -> list[dict]:
    """Return a flat list of all defence programme dicts from Form A Part 2 JSONs.

    secondary_ids: same bilingual deduplication as load_all_facilities.
    """
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_a2.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for prog in data.get("defence_programmes", []):
            src = (prog.get("extraction_metadata") or {}).get("source_id", "")
            if secondary_ids and src in secondary_ids:
                continue  # duplicate language version — prefer the English doc
            records.append(prog)
    return records


def load_all_defence_facilities(secondary_ids: set[str] | None = None) -> list[dict]:
    """Return a flat list of all defence facility dicts from Form A Part 2 JSONs.

    secondary_ids: same bilingual deduplication as load_all_facilities.
    """
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_a2.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for fac in data.get("defence_facilities", []):
            src = (fac.get("extraction_metadata") or {}).get("source_id", "")
            if secondary_ids and src in secondary_ids:
                continue  # duplicate language version — prefer the English doc
            records.append(fac)
    return records


def flatten_defence_programme(rec: dict) -> dict:
    """Flatten one defence programme dict into a CSV-ready dict."""
    meta = rec.get("extraction_metadata", {}) or {}
    return {
        "country_iso3":            meta.get("country_iso3"),
        "year":                    meta.get("year"),
        "programme_name":          rec.get("programme_name"),
        "responsible_org":         rec.get("responsible_org"),
        "objectives_summary":      rec.get("objectives_summary"),
        "research_areas":          "; ".join(rec.get("research_areas") or []),
        "total_funding_amount":    rec.get("total_funding_amount"),
        "total_funding_currency":  rec.get("total_funding_currency"),
        "uses_contractors":        rec.get("uses_contractors"),
        "contractor_proportion_pct": rec.get("contractor_proportion_pct"),
        "confidence":              rec.get("confidence"),
        "translated":              rec.get("translated"),
        "source_document":         meta.get("source_id"),
    }


def flatten_defence_facility(rec: dict) -> dict:
    """Flatten one defence facility dict into a CSV-ready dict."""
    meta = rec.get("extraction_metadata", {}) or {}
    loc  = rec.get("location", {}) or {}
    return {
        "country_iso3":       meta.get("country_iso3"),
        "year":               meta.get("year"),
        "facility_name":      rec.get("facility_name"),
        "city":               loc.get("city"),
        "address":            loc.get("address"),
        "bsl2_area_m2":       rec.get("bsl2_area_m2"),
        "bsl3_area_m2":       rec.get("bsl3_area_m2"),
        "bsl4_area_m2":       rec.get("bsl4_area_m2"),
        "total_lab_area_m2":  rec.get("total_lab_area_m2"),
        "personnel_total":    rec.get("personnel_total"),
        "personnel_military": rec.get("personnel_military"),
        "personnel_civilian": rec.get("personnel_civilian"),
        "personnel_scientists": rec.get("personnel_scientists"),
        "personnel_engineers":  rec.get("personnel_engineers"),
        "personnel_technicians": rec.get("personnel_technicians"),
        "personnel_admin":    rec.get("personnel_admin"),
        "mod_funded":         rec.get("mod_funded"),
        "funding_source":     rec.get("funding_source"),
        "funding_research":   rec.get("funding_research"),
        "funding_development": rec.get("funding_development"),
        "funding_te":         rec.get("funding_te"),
        "funding_currency":   rec.get("funding_currency"),
        "work_description":   rec.get("work_description"),
        "confidence":         rec.get("confidence"),
        "translated":         rec.get("translated"),
        "source_document":    meta.get("source_id"),
    }


# ── Form F loaders ───────────────────────────────────────────────────────────


def load_all_past_programmes() -> list[dict]:
    """Return a flat list of all past-programme dicts from Form F JSONs."""
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_f.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        # Skip NTD / no_form stubs (no has_offensive_programme key)
        if data.get("has_offensive_programme") is None and data.get("has_defensive_programme") is None:
            continue
        records.append(data)
    return records


def flatten_past_programme(rec: dict) -> dict:
    """Flatten one Form F record into a CSV-ready dict."""
    meta = rec.get("extraction_metadata", {}) or {}
    return {
        "country_iso3":            meta.get("country_iso3") or rec.get("country_iso3"),
        "year":                    meta.get("year") or rec.get("year"),
        "convention_entry_date":   rec.get("convention_entry_date"),
        "has_offensive_programme": rec.get("has_offensive_programme"),
        "offensive_period":        rec.get("offensive_period"),
        "offensive_summary":       rec.get("offensive_summary"),
        "has_defensive_programme": rec.get("has_defensive_programme"),
        "defensive_period":        rec.get("defensive_period"),
        "defensive_summary":       rec.get("defensive_summary"),
        "translated":              rec.get("translated"),
        "confidence":              rec.get("confidence"),
        "notes":                   rec.get("notes"),
        "source_document":         meta.get("source_id") or rec.get("id"),
    }


# ── Form E loaders ───────────────────────────────────────────────────────────


def load_all_legislation() -> list[dict]:
    """Return all Form E records from *_form_e.json files."""
    records: list[dict] = []
    for path in sorted(STRUCTURED_DIR.glob("*_form_e.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("categories") is not None:
            records.append(data)
    return records


def flatten_legislation(rec: dict) -> dict:
    """Flatten one Form E record into a CSV-ready dict."""
    meta = rec.get("extraction_metadata", {}) or {}
    cats = rec.get("categories") or {}
    row: dict = {
        "country_iso3": meta.get("country_iso3") or rec.get("country_iso3"),
        "year":         meta.get("year") or rec.get("year"),
    }
    for cat in _CATEGORIES:
        c = cats.get(cat) or {}
        for fld in _CAT_FIELDS:
            row[f"{cat}_{fld}"] = c.get(fld)
    row["key_laws"]        = "; ".join(rec.get("key_laws") or [])
    row["translated"]      = rec.get("translated")
    row["confidence"]      = rec.get("confidence")
    row["notes"]           = rec.get("notes")
    row["input_truncated"] = rec.get("input_truncated", False)
    row["source_document"] = meta.get("source_id") or rec.get("id")
    return row


# ── Compliance data ───────────────────────────────────────────────────────────


def load_compliance_data(catalogue: list[dict]) -> list[dict]:
    """
    Read segmentation manifests and compute per-form compliance status.

    Returns a list of dicts, one per document, with keys:
        country_iso3, year, source_document,
        form_<f>  (for each f in COMPLIANCE_FORMS): "substantive" | "nothing_to_declare" | "absent"
        forms_substantive_count, forms_ntd_count, forms_absent_count
    """
    # Build lookup: id → catalogue entry (for country, year, amendment flag)
    cat_map = {e["id"]: e for e in catalogue}

    rows: list[dict] = []
    for manifest_path in sorted(SEGMENTED_DIR.glob("*/manifest.json")):
        doc_id = manifest_path.parent.name
        entry = cat_map.get(doc_id, {})

        # Skip amendments and docs not in catalogue
        if entry.get("is_amendment"):
            continue
        if not entry.get("downloaded"):
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        present = set(manifest.get("forms_present", []))
        ntd     = set(manifest.get("forms_nothing_to_declare", []))

        row: dict = {
            "country_iso3":    entry.get("country_iso3"),
            "year":            entry.get("year"),
            "source_document": doc_id,
        }
        substantive_count = 0
        ntd_count         = 0
        absent_count      = 0

        for form in COMPLIANCE_FORMS:
            if form not in present:
                status = "absent"
                absent_count += 1
            elif form in ntd:
                status = "nothing_to_declare"
                ntd_count += 1
            else:
                # Check for 'limited': form is present & not NTD, but for A1/G
                # the extracted JSON has zero facilities — indicates a BSL-level-only
                # declaration, redacted public version, or blank-template submission.
                status = "substantive"
                if form == "A1":
                    a1_json = STRUCTURED_DIR / f"{doc_id}_form_a1.json"
                    if a1_json.exists():
                        try:
                            a1 = json.loads(a1_json.read_text(encoding="utf-8"))
                            if a1.get("facility_count", -1) == 0:
                                status = "limited"
                        except Exception:
                            pass
                if status == "substantive":
                    substantive_count += 1
                elif status == "limited":
                    ntd_count += 1   # counted alongside NTD for summary stats
            row[f"form_{form.lower()}"] = status

        row["forms_substantive_count"] = substantive_count
        row["forms_ntd_count"]         = ntd_count
        row["forms_absent_count"]      = absent_count
        rows.append(row)

    rows.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0))
    return rows


# ── Entity resolution ─────────────────────────────────────────────────────────


def _resolve_entities_core(
    records: list[dict],
    id_format: str = "{country}_{n:03d}",
    anchor_merges: list[tuple[str, str]] | None = None,
) -> dict[int, str]:
    """Unified entity resolution via Union-Find on facility names.

    Groups records by country, then merges record indices whose facility names
    score >= SIMILARITY_THRESHOLD on rapidfuzz token_sort_ratio.  Records from
    the same year use the stricter SAME_YEAR_THRESHOLD to avoid false merges
    within a single CBM submission.

    Args:
        records: list of extracted facility dicts.
        id_format: Python format string with {country} and {n} placeholders.
        anchor_merges: optional (country_iso3, anchor_string) tuples for
                       force-merging records whose names contain the anchor.
    """
    by_country: dict[str, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        country = (rec.get("extraction_metadata") or {}).get("country_iso3", "UNK")
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
                # Records from the same year are from the same CBM submission and are
                # therefore guaranteed to be different facilities.  Use a much higher
                # threshold so that only near-identical names (abbreviation vs full form)
                # are merged within a single year, preventing false merges like the five
                # "State Public Health Laboratory of X State, Ministry of Health" entries
                # from Mexico 2021, or four Universiti Malaysia Sabah sub-labs in 2011.
                ya = (records[ia].get("extraction_metadata") or {}).get("year")
                yb = (records[ib].get("extraction_metadata") or {}).get("year")
                threshold = SAME_YEAR_THRESHOLD if (ya and yb and ya == yb) else SIMILARITY_THRESHOLD
                if fuzz.token_sort_ratio(na, nb) >= threshold:
                    uf.union(ia, ib)

    # Anchor-merge pass: force-merge records whose names contain a given anchor
    # string.  Handles labs whose formal name changes across years.
    if anchor_merges:
        for anchor_country, anchor in anchor_merges:
            anchor_lower = anchor.lower()
            indices = by_country.get(anchor_country, [])
            matched = [
                i for i in indices
                if anchor_lower in (records[i].get("facility_name") or "").lower()
            ]
            for i in matched[1:]:
                uf.union(matched[0], i)

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
        groups = sorted(country_groups[country].values(), key=min)
        for n, members in enumerate(groups, 1):
            cid = id_format.format(country=country, n=n)
            for i in members:
                id_map[i] = cid

    return id_map


def resolve_entities(records: list[dict]) -> dict[int, str]:
    """Assign a canonical_facility_id to each A1 research facility record."""
    return _resolve_entities_core(
        records, id_format="{country}_{n:03d}", anchor_merges=ANCHOR_MERGES,
    )


# ── Vaccine entity resolution ─────────────────────────────────────────────────


def resolve_vaccine_entities(records: list[dict]) -> dict[int, str]:
    """Assign a canonical_vaccine_facility_id to each Form G vaccine record."""
    return _resolve_entities_core(records, id_format="{country}_V{n:03d}")


def build_vaccine_entity_registry(
    records: list[dict], id_map: dict[int, str]
) -> list[dict]:
    """Build a canonical vaccine facility list — one entry per unique facility."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for i, rec in enumerate(records):
        groups[id_map[i]].append(rec)

    registry: list[dict] = []
    for vid in sorted(groups.keys()):
        members = sorted(
            groups[vid],
            key=lambda r: (r.get("extraction_metadata") or {}).get("year", 0),
        )
        latest = members[-1]
        years = sorted({
            (r.get("extraction_metadata") or {}).get("year")
            for r in members
            if (r.get("extraction_metadata") or {}).get("year")
        })
        seen: set[str] = set()
        all_names: list[str] = []
        for r in members:
            n = r.get("facility_name") or ""
            if n and n not in seen:
                seen.add(n)
                all_names.append(n)
        loc = (latest.get("location") or {})
        registry.append({
            "canonical_vaccine_facility_id": vid,
            "canonical_name":  all_names[-1] if all_names else None,
            "country_iso3":    (latest.get("extraction_metadata") or {}).get("country_iso3"),
            "all_names":       all_names,
            "years_declared":  years,
            "city":            loc.get("city"),
        })

    return registry


# ── Defence entity resolution ─────────────────────────────────────────────────


def resolve_defence_facility_entities(records: list[dict]) -> dict[int, str]:
    """Assign a canonical_defence_facility_id to each A2 defence facility record."""
    return _resolve_entities_core(records, id_format="{country}_D{n:03d}")


def build_defence_entity_registry(
    records: list[dict], id_map: dict[int, str]
) -> list[dict]:
    """Build a canonical defence facility list — one entry per unique facility."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for i, rec in enumerate(records):
        groups[id_map[i]].append(rec)

    registry: list[dict] = []
    for did in sorted(groups.keys()):
        members = sorted(
            groups[did],
            key=lambda r: (r.get("extraction_metadata") or {}).get("year", 0),
        )
        latest = members[-1]
        years = sorted({
            (r.get("extraction_metadata") or {}).get("year")
            for r in members
            if (r.get("extraction_metadata") or {}).get("year")
        })
        seen: set[str] = set()
        all_names: list[str] = []
        for r in members:
            n = r.get("facility_name") or ""
            if n and n not in seen:
                seen.add(n)
                all_names.append(n)
        registry.append({
            "canonical_defence_facility_id": did,
            "canonical_name":  all_names[-1] if all_names else None,
            "country_iso3":    (latest.get("extraction_metadata") or {}).get("country_iso3"),
            "all_names":       all_names,
            "years_declared":  years,
            "has_bsl4":        any(r.get("bsl4_area_m2") for r in members),
            "has_bsl3":        any(r.get("bsl3_area_m2") for r in members),
        })

    return registry


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


def flatten_vaccine_record(rec: dict, vid: str) -> dict:
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
        "canonical_vaccine_facility_id": vid,
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
    flat: list[dict],
    registry: list[dict],
    vaccine_flat: list[dict] | None = None,
    compliance: list[dict] | None = None,
    defence_programmes_flat: list[dict] | None = None,
    defence_facilities_flat: list[dict] | None = None,
    past_programmes_flat: list[dict] | None = None,
    legislation_flat: list[dict] | None = None,
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
    if compliance is not None:
        total_docs = len(compliance)
        result["compliance_documents_tracked"] = total_docs
        form_rates: dict[str, float] = {}
        for form in COMPLIANCE_FORMS:
            col = f"form_{form.lower()}"
            sub = sum(1 for r in compliance if r.get(col) == "substantive")
            form_rates[form] = round(sub / total_docs, 3) if total_docs else 0.0
        result["form_substantive_rate"] = form_rates
    if defence_programmes_flat is not None:
        result["total_defence_programme_records"] = len(defence_programmes_flat)
        dc = sorted({r["country_iso3"] for r in defence_programmes_flat if r["country_iso3"]})
        result["defence_programme_countries"] = dc
    if defence_facilities_flat is not None:
        result["total_defence_facility_records"] = len(defence_facilities_flat)
    if past_programmes_flat is not None:
        result["total_past_programme_records"] = len(past_programmes_flat)
        off = sum(1 for r in past_programmes_flat if r.get("has_offensive_programme"))
        defn = sum(1 for r in past_programmes_flat if r.get("has_defensive_programme"))
        result["past_programmes_with_offensive"] = off
        result["past_programmes_with_defensive"] = defn
    if legislation_flat is not None:
        result["total_legislation_records"] = len(legislation_flat)
        leg_c = sorted({r["country_iso3"] for r in legislation_flat if r["country_iso3"]})
        result["legislation_countries"] = leg_c
    return result


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalogue = load_catalogue()
    cat_index = {e["id"]: e.get("language", "en") for e in catalogue}

    # Skip secondary-language versions of bilingual submissions (Canada EN+FR,
    # Switzerland DE/FR/EN) so entity resolution does not create spurious duplicates
    # from translated facility names (see get_secondary_source_ids for details).
    secondary_ids = get_secondary_source_ids(catalogue)

    log.info("Loading structured records from %s …", STRUCTURED_DIR)
    records = load_all_facilities(secondary_ids=secondary_ids)
    log.info("Loaded %d facility-year records", len(records))

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
    vaccine_records = load_all_vaccine_facilities(secondary_ids=secondary_ids)
    vaccine_flat: list[dict] | None = None
    if vaccine_records:
        log.info("Loaded %d vaccine facility-year records (Form G)", len(vaccine_records))

        log.info("Resolving vaccine entities (threshold=%d)…", SIMILARITY_THRESHOLD)
        v_id_map = resolve_vaccine_entities(vaccine_records)
        v_unique = len({v for v in v_id_map.values()})
        log.info("Vaccine entity resolution: %d records → %d unique facilities",
                 len(vaccine_records), v_unique)

        vaccine_flat = [flatten_vaccine_record(r, v_id_map[i])
                        for i, r in enumerate(vaccine_records)]
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

        # ── vaccine_entity_registry.json ─────────────────────────────────────
        v_registry = build_vaccine_entity_registry(vaccine_records, v_id_map)
        vreg_path = OUTPUT_DIR / "vaccine_entity_registry.json"
        vreg_path.write_text(
            json.dumps(v_registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log.info("Wrote %s (%d vaccine entities)", vreg_path, len(v_registry))
    else:
        log.info("No Form G data found; skipping vaccine facility outputs")
        v_registry = []

    # ── Form A Part 2: defence programmes and facilities ──────────────────────
    defence_programmes = load_all_defence_programmes(secondary_ids=secondary_ids)
    defence_facilities = load_all_defence_facilities(secondary_ids=secondary_ids)
    if defence_programmes or defence_facilities:
        log.info("Loaded %d defence programmes, %d defence facilities (Form A Part 2)",
                 len(defence_programmes), len(defence_facilities))

        dp_flat = [flatten_defence_programme(r) for r in defence_programmes]
        dp_flat.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0, r["programme_name"] or ""))
        dp_csv = OUTPUT_DIR / "defence_programmes.csv"
        with dp_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_DEFENCE_PROGRAMMES)
            writer.writeheader()
            writer.writerows(dp_flat)
        log.info("Wrote %s (%d rows)", dp_csv, len(dp_flat))

        df_id_map = resolve_defence_facility_entities(defence_facilities)
        df_flat = []
        for i, rec in enumerate(defence_facilities):
            d = flatten_defence_facility(rec)
            d["canonical_defence_facility_id"] = df_id_map.get(i)
            df_flat.append(d)
        df_flat.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0, r["facility_name"] or ""))
        df_csv = OUTPUT_DIR / "defence_facilities.csv"
        with df_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_DEFENCE_FACILITIES)
            writer.writeheader()
            writer.writerows(df_flat)
        log.info("Wrote %s (%d rows)", df_csv, len(df_flat))

        d_registry = build_defence_entity_registry(defence_facilities, df_id_map)
        dreg_path = OUTPUT_DIR / "defence_entity_registry.json"
        dreg_path.write_text(
            json.dumps(d_registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log.info("Wrote %s (%d defence entities)", dreg_path, len(d_registry))
    else:
        log.info("No Form A Part 2 data found; skipping defence programme outputs")
        dp_flat = df_flat = []

    # ── Form F: past offensive/defensive programmes ───────────────────────────
    past_programmes = load_all_past_programmes()
    pp_flat: list[dict] = []
    if past_programmes:
        log.info("Loaded %d past-programme records (Form F)", len(past_programmes))
        pp_flat = [flatten_past_programme(r) for r in past_programmes]
        pp_flat.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0))
        pp_csv = OUTPUT_DIR / "past_programmes.csv"
        with pp_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_PAST_PROGRAMMES)
            writer.writeheader()
            writer.writerows(pp_flat)
        log.info("Wrote %s (%d rows)", pp_csv, len(pp_flat))
    else:
        log.info("No Form F data found; skipping past programme outputs")

    # ── Form E: national biosafety/biosecurity legislation ────────────────────
    legislation = load_all_legislation()
    leg_flat: list[dict] = []
    if legislation:
        log.info("Loaded %d legislation records (Form E)", len(legislation))
        leg_flat = [flatten_legislation(r) for r in legislation]
        leg_flat.sort(key=lambda r: (r["country_iso3"] or "", r["year"] or 0))
        leg_csv = OUTPUT_DIR / "legislation.csv"
        with leg_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_LEGISLATION)
            writer.writeheader()
            writer.writerows(leg_flat)
        log.info("Wrote %s (%d rows)", leg_csv, len(leg_flat))
    else:
        log.info("No Form E data found; skipping legislation output")

    # ── compliance matrix (Form 0 / segmentation manifests) ──────────────────
    log.info("Loading compliance data from segmentation manifests …")
    compliance = load_compliance_data(catalogue)
    log.info("Loaded compliance data for %d documents", len(compliance))

    comp_matrix_path = OUTPUT_DIR / "compliance_matrix.csv"
    with comp_matrix_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_COMPLIANCE_MATRIX)
        writer.writeheader()
        writer.writerows(compliance)
    log.info("Wrote %s (%d rows)", comp_matrix_path, len(compliance))

    # Long-format: one row per (document, form)
    long_rows: list[dict] = []
    for row in compliance:
        for form in COMPLIANCE_FORMS:
            long_rows.append({
                "country_iso3":    row["country_iso3"],
                "year":            row["year"],
                "source_document": row["source_document"],
                "form":            form,
                "status":          row[f"form_{form.lower()}"],
            })

    form_comp_path = OUTPUT_DIR / "form_compliance.csv"
    with form_comp_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS_FORM_COMPLIANCE)
        writer.writeheader()
        writer.writerows(long_rows)
    log.info("Wrote %s (%d rows)", form_comp_path, len(long_rows))

    # ── summary_stats.json ────────────────────────────────────────────────────
    stats = build_summary_stats(flat, registry, vaccine_flat, compliance,
                               dp_flat or None, df_flat or None,
                               pp_flat or None, leg_flat or None)
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
    if stats.get("total_defence_programme_records"):
        print(f"  Defence programme recs:  {stats['total_defence_programme_records']}")
    if stats.get("total_defence_facility_records"):
        print(f"  Defence facility recs:   {stats['total_defence_facility_records']}")
    if stats.get("total_past_programme_records"):
        off = stats.get("past_programmes_with_offensive", 0)
        defn = stats.get("past_programmes_with_defensive", 0)
        print(f"  Past programme recs:     {stats['total_past_programme_records']} "
              f"({off} offensive / {defn} defensive)")
    if stats.get("total_legislation_records"):
        print(f"  Legislation recs (E):    {stats['total_legislation_records']} "
              f"({len(stats.get('legislation_countries', []))} countries)")
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

    if stats.get("form_substantive_rate"):
        n = stats["compliance_documents_tracked"]
        print(f"── Compliance (% of {n} docs with substantive submission) ──")
        for form, rate in stats["form_substantive_rate"].items():
            bar = "█" * round(rate * 20)
            print(f"  Form {form:2s}  {rate*100:5.1f}%  {bar}")
        print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
