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

# FEATURE 1: Hardcoded merge groups based on manual review of entity fragmentation
MERGES = [
    ("CHE_001", ["CHE_004", "CHE_007"], "Spiez Laboratory"),
    ("CHE_003", ["CHE_006", "CHE_008"], "National Reference Center for Emerging Viral Infections (HUG Geneva)"),
    ("CHE_002", ["CHE_005", "CHE_010", "CHE_011", "CHE_013"], "Institut für Viruskrankheiten und Immunprophylaxe / IVI"),
    ("CHE_009", ["CHE_012", "CHE_014"], "Institute of Medical Virology, Universität Zürich"),
    ("USA_007", ["USA_009", "USA_013", "USA_014", "USA_018"], "Centers for Disease Control and Prevention (CDC)"),
    ("UKR_001", ["UKR_009", "UKR_011", "UKR_012", "UKR_018"], "I.I. Mechnikov Ukrainian Research Anti-Plague Institute"),
    ("DEU_013", ["DEU_015"], "Institut für Virologie, Philipps-Universität Marburg"),
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
    print(f"Merge groups: {len(MERGES)}")

    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Cannot connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Always print the merge plan first
            print("\n--- MERGE PLAN ---")
            for keeper_id, deprecated_ids, new_name in MERGES:
                print_merge_plan(cur, keeper_id, deprecated_ids, new_name)

            if not args.apply:
                print("\n[DRY-RUN] No changes made. Pass --apply to execute.")
                return

            # Apply merges
            print("\n--- APPLYING MERGES ---")
            for keeper_id, deprecated_ids, new_name in MERGES:
                print(f"\nGroup: {keeper_id} ← {deprecated_ids}")
                apply_merge(cur, keeper_id, deprecated_ids, new_name)

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
