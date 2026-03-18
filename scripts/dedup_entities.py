#!/usr/bin/env python3
"""
dedup_entities.py — Merge known fragmented canonical facility entities.

Some physical facilities were split into multiple canonical entities during
entity resolution (e.g. due to multilingual name variants). This script
applies known manual merge groups to consolidate them.

FEATURE 1: Entity deduplication script

Default behaviour is dry-run (print what would change, no DB writes).
Pass --apply to actually execute the merges.

Usage:
    python scripts/dedup_entities.py            # dry-run (no flags needed)
    python scripts/dedup_entities.py --apply    # execute merges
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
DB_URL = os.getenv("DATABASE_URL", "postgresql://cbm:cbm@localhost:5432/cbm")

# ── Known merge groups ────────────────────────────────────────────────────────
# Each entry: (keeper_id, [deprecated_ids_to_merge_in], new_canonical_name)
# The keeper_id is the entity that will survive; all deprecated IDs are merged
# into it. The new_canonical_name replaces the keeper's existing canonical_name.

# Hardcoded merge groups based on manual review of entity fragmentation.
# Each entry: (keeper_id, [deprecated_ids], new_canonical_name)
# None as new_canonical_name preserves a null name (for genuinely unnamed facilities).
MERGES = [
    # Switzerland — multilingual name variants
    ("CHE_001", ["CHE_004", "CHE_007"], "Spiez Laboratory"),
    ("CHE_003", ["CHE_006", "CHE_008"], "National Reference Center for Emerging Viral Infections (HUG Geneva)"),
    ("CHE_002", ["CHE_005", "CHE_010", "CHE_011", "CHE_013"], "Institut für Viruskrankheiten und Immunprophylaxe / IVI"),
    ("CHE_009", ["CHE_012", "CHE_014"], "Institute of Medical Virology, Universität Zürich"),
    # USA — CDC administrative reorganisation names
    ("USA_007", ["USA_009", "USA_013", "USA_014", "USA_018"], "Centers for Disease Control and Prevention (CDC)"),
    # Ukraine — transliteration variants
    ("UKR_001", ["UKR_009", "UKR_011", "UKR_012", "UKR_018"], "I.I. Mechnikov Ukrainian Research Anti-Plague Institute"),
    # Germany — bilingual name switch
    ("DEU_013", ["DEU_015"], "Institut für Virologie, Philipps-Universität Marburg"),

    # UK — serial agency renames across HPA → PHE → UKHSA (AUDIT_DATA §1A)
    ("GBR_001", ["GBR_006", "GBR_019", "GBR_026", "GBR_030"],
        "UK Health Security Agency – Porton Down"),
    ("GBR_002", ["GBR_005", "GBR_018", "GBR_027", "GBR_028"],
        "UK Health Security Agency – Colindale"),
    ("GBR_004", ["GBR_013"],
        "Defence Science and Technology Laboratory (Dstl), Porton Down"),
    ("GBR_008", ["GBR_015", "GBR_020"],
        "Animal and Plant Health Agency (APHA)"),
    ("GBR_009", ["GBR_014", "GBR_017"],
        "The Pirbright Institute"),
    ("GBR_010", ["GBR_016", "GBR_023", "GBR_024"],
        "Boehringer Ingelheim Animal Health UK (Pirbright site)"),
    ("GBR_007", ["GBR_021", "GBR_022"],
        "The Francis Crick Institute (formerly NIMR)"),
    ("GBR_003", ["GBR_029", "GBR_031"],
        "Medicines and Healthcare Products Regulatory Agency / NIBSC"),
    ("GBR_011", ["GBR_012"],
        "Intervet Schering-Plough Animal Health"),

    # Australia — AAHL renamed to ACDP in 2020 (AUDIT_DATA §1B)
    ("AUS_001", ["AUS_005"],
        "Australian Centre for Disease Preparedness (formerly AAHL)"),

    # Denmark — 14 null-named single-year entities are the same unnamed facility (AUDIT_DATA §1C)
    ("DNK_008", ["DNK_009", "DNK_010", "DNK_011", "DNK_012", "DNK_013", "DNK_014",
                 "DNK_015", "DNK_016", "DNK_017", "DNK_018", "DNK_019", "DNK_020", "DNK_021"],
        None),

    # Cyprus — 7 null-named single-year entities (AUDIT_DATA §1D)
    ("CYP_001", ["CYP_002", "CYP_003", "CYP_004", "CYP_005", "CYP_006", "CYP_007"],
        None),

    # Slovakia — 5 null-named single-year entities (AUDIT_DATA §1E)
    ("SVK_001", ["SVK_002", "SVK_003", "SVK_004", "SVK_005"],
        None),
]

# ── Vaccine entity merge groups ───────────────────────────────────────────────
# Same structure as MERGES but operates on vaccine_facilities / vaccine_facility_years.

VACCINE_MERGES = [
    # Bulgaria — BulBio-NCIPD: 6 spelling variants of the same facility (AUDIT_DATA §1G)
    ("BGR_V001", ["BGR_V002", "BGR_V003", "BGR_V004", "BGR_V005", "BGR_V006"],
        "BulBio-NCIPD Ltd (National Center of Infectious and Parasitic Diseases)"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_entity(cur, entity_id: str) -> dict | None:
    """Fetch a single facility entity row as a dict."""
    cur.execute("""
        SELECT canonical_facility_id, canonical_name, country_iso3,
               all_names, years_declared, latest_containment, latest_area_m2
        FROM   facilities
        WHERE  canonical_facility_id = %s
    """, (entity_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def count_facility_years(cur, entity_id: str) -> int:
    """Count how many facility_year rows point to this entity."""
    cur.execute(
        "SELECT count(*) FROM facility_years WHERE canonical_facility_id = %s",
        (entity_id,)
    )
    return cur.fetchone()["count"]


def print_merge_plan(cur, keeper_id: str, deprecated_ids: list[str], new_name: str) -> None:
    """Print a human-readable summary of what this merge would do."""
    keeper = fetch_entity(cur, keeper_id)
    if not keeper:
        print(f"  [WARN] Keeper '{keeper_id}' not found in facilities table — skipping")
        return

    keeper_fy = count_facility_years(cur, keeper_id)
    print(f"\n  MERGE → keeper: {keeper_id}  ({keeper['canonical_name'] or '[unnamed]'})")
    print(f"    new canonical_name : {new_name}")
    print(f"    keeper facility_years: {keeper_fy}")

    total_deprecated_fy = 0
    missing = []
    for dep_id in deprecated_ids:
        dep = fetch_entity(cur, dep_id)
        if not dep:
            missing.append(dep_id)
            print(f"    [WARN] Deprecated '{dep_id}' not found — will be skipped")
            continue
        dep_fy = count_facility_years(cur, dep_id)
        total_deprecated_fy += dep_fy
        print(f"    merge in:  {dep_id}  ({dep['canonical_name'] or '[unnamed]'})  → {dep_fy} facility_years")

    print(f"    total facility_years after merge: {keeper_fy + total_deprecated_fy}")
    if missing:
        print(f"    [WARN] {len(missing)} deprecated IDs not found in DB: {missing}")


def apply_merge(cur, keeper_id: str, deprecated_ids: list[str], new_name: str) -> None:
    """Execute one merge group: re-point facility_years, update keeper, delete deprecated."""
    keeper = fetch_entity(cur, keeper_id)
    if not keeper:
        print(f"  [SKIP] Keeper '{keeper_id}' not found — skipping entire group")
        return

    # Collect all_names from deprecated entities before we delete them
    combined_names: set[str] = set(keeper["all_names"] or [])
    found_deprecated: list[str] = []

    for dep_id in deprecated_ids:
        dep = fetch_entity(cur, dep_id)
        if not dep:
            print(f"  [SKIP] Deprecated '{dep_id}' not found — skipping this ID")
            continue
        found_deprecated.append(dep_id)
        combined_names.update(dep["all_names"] or [])

    if not found_deprecated:
        print(f"  [SKIP] No deprecated entities found for keeper {keeper_id}")
        return

    # FEATURE 1: Step 1 — re-point facility_years to the keeper ID
    cur.execute("""
        UPDATE facility_years
        SET    canonical_facility_id = %s
        WHERE  canonical_facility_id = ANY(%s)
    """, (keeper_id, found_deprecated))
    moved_rows = cur.rowcount
    print(f"  [OK] Re-pointed {moved_rows} facility_years → {keeper_id}")

    # FEATURE 1: Step 2 — compute updated years_declared for the keeper
    cur.execute("""
        SELECT ARRAY_AGG(DISTINCT year ORDER BY year) AS years
        FROM   facility_years
        WHERE  canonical_facility_id = %s
    """, (keeper_id,))
    new_years = cur.fetchone()["years"] or []

    # FEATURE 1: Step 3 — update the keeper entity with merged names + years
    all_names_list = sorted(combined_names)
    cur.execute("""
        UPDATE facilities
        SET    canonical_name  = %s,
               all_names       = %s,
               years_declared  = %s
        WHERE  canonical_facility_id = %s
    """, (new_name, all_names_list, new_years, keeper_id))
    print(f"  [OK] Updated keeper {keeper_id}: name='{new_name}', {len(all_names_list)} names, {len(new_years)} years")

    # FEATURE 1: Step 4 — delete deprecated facility rows
    cur.execute("""
        DELETE FROM facilities
        WHERE  canonical_facility_id = ANY(%s)
    """, (found_deprecated,))
    deleted = cur.rowcount
    print(f"  [OK] Deleted {deleted} deprecated facility rows: {found_deprecated}")


# ── Vaccine entity helpers ────────────────────────────────────────────────────

def fetch_vaccine_entity(cur, entity_id: str) -> dict | None:
    cur.execute(
        "SELECT id, canonical_name, country_iso3 FROM vaccine_facilities WHERE id = %s",
        (entity_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def print_vaccine_merge_plan(cur, keeper_id: str, deprecated_ids: list[str], new_name: str) -> None:
    keeper = fetch_vaccine_entity(cur, keeper_id)
    if not keeper:
        print(f"  [WARN] Vaccine keeper '{keeper_id}' not found — skipping")
        return
    cur.execute(
        "SELECT COUNT(*) FROM vaccine_facility_years WHERE canonical_vaccine_facility_id = %s",
        (keeper_id,),
    )
    keeper_fy = cur.fetchone()["count"]
    print(f"\n  VACCINE MERGE → keeper: {keeper_id}  ({keeper['canonical_name'] or '[unnamed]'})")
    print(f"    new canonical_name : {new_name}")
    print(f"    keeper vaccine_facility_years: {keeper_fy}")
    for dep_id in deprecated_ids:
        dep = fetch_vaccine_entity(cur, dep_id)
        if not dep:
            print(f"    [WARN] Deprecated '{dep_id}' not found")
            continue
        cur.execute(
            "SELECT COUNT(*) FROM vaccine_facility_years WHERE canonical_vaccine_facility_id = %s",
            (dep_id,),
        )
        dep_fy = cur.fetchone()["count"]
        print(f"    merge in:  {dep_id}  ({dep['canonical_name'] or '[unnamed]'})  → {dep_fy} vaccine_facility_years")


def apply_vaccine_merge(cur, keeper_id: str, deprecated_ids: list[str], new_name: str) -> None:
    keeper = fetch_vaccine_entity(cur, keeper_id)
    if not keeper:
        print(f"  [SKIP] Vaccine keeper '{keeper_id}' not found")
        return

    found_deprecated = [d for d in deprecated_ids if fetch_vaccine_entity(cur, d)]
    if not found_deprecated:
        print(f"  [SKIP] No deprecated vaccine entities found for keeper {keeper_id}")
        return

    cur.execute("""
        UPDATE vaccine_facility_years
        SET    canonical_vaccine_facility_id = %s
        WHERE  canonical_vaccine_facility_id = ANY(%s)
    """, (keeper_id, found_deprecated))
    print(f"  [OK] Re-pointed {cur.rowcount} vaccine_facility_years → {keeper_id}")

    cur.execute("""
        SELECT MIN(year) AS first_year, MAX(year) AS last_year
        FROM   vaccine_facility_years
        WHERE  canonical_vaccine_facility_id = %s
    """, (keeper_id,))
    row = cur.fetchone()

    cur.execute("""
        UPDATE vaccine_facilities
        SET    canonical_name = %s,
               first_year     = %s,
               last_year      = %s
        WHERE  id = %s
    """, (new_name, row["first_year"], row["last_year"], keeper_id))
    print(f"  [OK] Updated vaccine keeper {keeper_id}: name='{new_name}'")

    cur.execute("DELETE FROM vaccine_facilities WHERE id = ANY(%s)", (found_deprecated,))
    print(f"  [OK] Deleted {cur.rowcount} deprecated vaccine_facility rows: {found_deprecated}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually apply merges (default is dry-run — print only, no DB writes).",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== dedup_entities.py — {mode} ===")
    print(f"Research facility merge groups: {len(MERGES)}")
    print(f"Vaccine facility merge groups:  {len(VACCINE_MERGES)}")

    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Cannot connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Always print the merge plan first
            print("\n--- RESEARCH FACILITY MERGE PLAN ---")
            for keeper_id, deprecated_ids, new_name in MERGES:
                print_merge_plan(cur, keeper_id, deprecated_ids, new_name)

            print("\n--- VACCINE FACILITY MERGE PLAN ---")
            for keeper_id, deprecated_ids, new_name in VACCINE_MERGES:
                print_vaccine_merge_plan(cur, keeper_id, deprecated_ids, new_name)

            if not args.apply:
                print("\n[DRY-RUN] No changes made. Pass --apply to execute.")
                return

            # Apply merges
            print("\n--- APPLYING RESEARCH FACILITY MERGES ---")
            for keeper_id, deprecated_ids, new_name in MERGES:
                print(f"\nGroup: {keeper_id} ← {deprecated_ids}")
                apply_merge(cur, keeper_id, deprecated_ids, new_name)

            print("\n--- APPLYING VACCINE FACILITY MERGES ---")
            for keeper_id, deprecated_ids, new_name in VACCINE_MERGES:
                print(f"\nGroup: {keeper_id} ← {deprecated_ids}")
                apply_vaccine_merge(cur, keeper_id, deprecated_ids, new_name)

        conn.commit()
        print("\n[OK] All merges committed successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"\n[ERROR] Merge failed, rolling back: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
