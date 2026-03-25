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

    # Ireland — typographic variation + appended hospital name (same BSL-3, same agents, no year overlap)
    ("IRL_002", ["IRL_008", "IRL_023", "IRL_027"],
        "Public Health Laboratory (PHL), Cherry Orchard Hospital"),

    # ── Ireland — systematic scan (2026-03-18) ──────────────────────────────
    # Same city/address, same BSL, same agents, no overlapping years throughout.
    ("IRL_001", ["IRL_005"],
        "National Viral Reference Laboratory (NVRL), University College Dublin"),
    ("IRL_006", ["IRL_024"],
        "Department of Agriculture, Food and the Marine (DAFM) Laboratories"),
    ("IRL_007", ["IRL_010"],
        "Veterinary Sciences Centre, University College Dublin"),
    ("IRL_009", ["IRL_013", "IRL_017", "IRL_020"],
        "Galway University Hospital"),
    ("IRL_011", ["IRL_015"],
        "Institute for Molecular Medicine, Trinity College Dublin"),
    ("IRL_012", ["IRL_018"],
        "School of Medicine, Centre for Research in Infectious Diseases, UCD"),
    ("IRL_014", ["IRL_021", "IRL_026"],
        "Sample Processing Laboratory, HRB Clinical Research Facility"),
    ("IRL_019", ["IRL_022", "IRL_025", "IRL_029"],
        "Research Pharmacy, HRB Clinical Research Facility"),
    ("IRL_003", ["IRL_030"],
        "National Salmonella, Shigella, Listeria Reference Laboratory (NSSLRL)"),
    ("IRL_004", ["IRL_031"],
        "Marine Institute"),

    # ── Austria ──────────────────────────────────────────────────────────────
    ("AUT_001", ["AUT_002"],
        "Federal Ministry of Defence — NBC & Environmental Protection Technology"),

    # ── Germany — additional merges ──────────────────────────────────────────
    ("DEU_014", ["DEU_016"],
        "Centre for Biological Threats and Special Pathogens (ZBS), Robert Koch Institute"),
    ("DEU_003", ["DEU_005", "DEU_012"],
        "Friedrich-Loeffler-Institut"),
    ("DEU_001", ["DEU_007"],
        "Institute of Virology, Charité Berlin"),
    ("DEU_002", ["DEU_006"],
        "Central Institute of Cancer Research, Berlin-Buch"),

    # ── Finland ──────────────────────────────────────────────────────────────
    # KTL → THL → Finnish Institute for Health and Welfare (same Helsinki address)
    ("FIN_002", ["FIN_009", "FIN_010", "FIN_011"],
        "Finnish Institute for Health and Welfare (THL)"),
    # PVTT → FDRA (same Lakiala address)
    ("FIN_006", ["FIN_015"],
        "Finnish Defence Research Agency (FDRA)"),
    ("FIN_018", ["FIN_019"],
        "Finnish Defence Forces, Centre for Military Medicine"),
    # Wellbeing services county added to name (same Turku address)
    ("FIN_020", ["FIN_021", "FIN_023"],
        "University of Turku, Institute of Biomedicine / Tyks Laboratories"),
    # Department of Virology parent entity → Viral Zoonoses continuation
    ("FIN_004", ["FIN_017"],
        "Department of Virology, University of Helsinki"),

    # ── Romania ──────────────────────────────────────────────────────────────
    # Pasteur Institute SA chain — same 333 Giulesti address throughout
    ("ROU_007", ["ROU_011", "ROU_014", "ROU_016", "ROU_017", "ROU_020", "ROU_021", "ROU_022"],
        "National Society Pasteur Institute SA"),
    # Cantacuzino: civilian → military takeover (same Bucharest address)
    ("ROU_008", ["ROU_015", "ROU_018"],
        "Cantacuzino National Medico-Military Institute"),

    # ── Latvia ───────────────────────────────────────────────────────────────
    # Hospital org-name evolution (same 3 Linezera Street, Riga)
    ("LVA_001", ["LVA_003", "LVA_004", "LVA_005", "LVA_007"],
        "Riga East University Hospital, National Microbiology Reference Laboratory"),

    # ── Sweden ───────────────────────────────────────────────────────────────
    # SMI → Public Health Agency → PHAS (same Solna BSL-4 lab)
    ("SWE_002", ["SWE_006", "SWE_007", "SWE_008", "SWE_009", "SWE_010"],
        "High Containment Laboratory, Public Health Agency of Sweden"),
    # FOI division name variants (same Umeå address)
    ("SWE_001", ["SWE_004", "SWE_005"],
        "Swedish Defence Research Agency (FOI)"),

    # ── USA — additional merges ──────────────────────────────────────────────
    ("USA_001", ["USA_008"],
        "Viral Immunology Center, National B Virus Resource Laboratory"),
    ("USA_002", ["USA_015"],
        "Betty Slick and Lewis J. Moorman Jr. Laboratory Complex"),

    # ── Hungary ──────────────────────────────────────────────────────────────
    ("HUN_002", ["HUN_004"],
        "National Biosafety Laboratory (NBL)"),

    # ── Czech Republic ───────────────────────────────────────────────────────
    ("CZE_006", ["CZE_016"],
        "Laboratory for Biological Monitoring and Protection"),
    ("CZE_005", ["CZE_017"],
        "Military Health Institute, Těchonín"),
    ("CZE_004", ["CZE_013"],
        "Institute of Molecular Pathology (IMP)"),

    # ── CZE_005 post-merge split fix ──────────────────────────────────────
    # CZE_005 incorrectly contains a Praha row (2011, department Prague)
    # alongside Těchonín rows.  After merge, split the Praha row into CZE_018.
    # Handled by SPLITS below — not a merge.

    # ── Slovenia ─────────────────────────────────────────────────────────────
    ("SVN_003", ["SVN_005"],
        "Mobile Laboratory"),

    # ── Lithuania ────────────────────────────────────────────────────────────
    ("LTU_003", ["LTU_005"],
        "National Veterinary Laboratory"),
    ("LTU_002", ["LTU_006"],
        "Centre for Communicable Diseases and AIDS"),

    # ── Moldova ──────────────────────────────────────────────────────────────
    ("MDA_001", ["MDA_004"],
        "National Agency of Public Health (NAPH)"),

    # ── Portugal ─────────────────────────────────────────────────────────────
    ("PRT_001", ["PRT_013"],
        "Laboratório de Bromatologia e Defesa Biológica"),
    ("PRT_006", ["PRT_010", "PRT_012"],
        "Instituto Nacional de Investigação Agrária e Veterinária (INIAV)"),

    # ── Bulgaria ─────────────────────────────────────────────────────────────
    ("BGR_001", ["BGR_005", "BGR_006"],
        "National Centre of Infectious and Parasitic Diseases"),

    # ── Estonia ──────────────────────────────────────────────────────────────
    # Joint Laboratories umbrella — different sub-labs appended in different years
    ("EST_001", ["EST_005", "EST_008", "EST_016"],
        "Joint Laboratories of Tartu University Clinics"),
    ("EST_007", ["EST_014"],
        "Synlab Eesti"),
    ("EST_004", ["EST_012"],
        "Laboratory for Mycobacteriosis, University of Life Sciences"),
    ("EST_006", ["EST_010"],
        "Laboratory of Communicable Diseases, Estonian Health Board"),
    # Veterinary & Food Laboratory merged into LABRIS in 2023
    ("EST_003", ["EST_017"],
        "National Centre for Laboratory Research and Risk Assessment (LABRIS)"),

    # ── Norway ───────────────────────────────────────────────────────────────
    ("NOR_001", ["NOR_002"],
        "Institute of Microbiology, Armed Forces Medical Services"),
]

# ── Vaccine entity merge groups ───────────────────────────────────────────────
# Same structure as MERGES but operates on vaccine_facilities / vaccine_facility_years.

VACCINE_MERGES = [
    # Bulgaria — BulBio-NCIPD: 6 spelling variants of the same facility (AUDIT_DATA §1G)
    ("BGR_V001", ["BGR_V002", "BGR_V003", "BGR_V004", "BGR_V005", "BGR_V006"],
        "BulBio-NCIPD Ltd (National Center of Infectious and Parasitic Diseases)"),

    # ── Systematic vaccine entity scan (2026-03-18) ─────────────────────────

    # Czech Republic
    ("CZE_V002", ["CZE_V006"],
        "Sevapharma a.s."),
    ("CZE_V004", ["CZE_V007", "CZE_V008"],
        "Dyntec spol. s r.o."),

    # Romania — corporate name evolution, same addresses throughout
    ("ROU_V001", ["ROU_V003", "ROU_V007", "ROU_V010"],
        "Pasteur Filiala Filipesti (Bucharest)"),
    ("ROU_V002", ["ROU_V006", "ROU_V009"],
        "Cantacuzino National Institute (Bucharest)"),
    ("ROU_V004", ["ROU_V008"],
        "Romvac Company S.A."),

    # Germany — corporate restructuring / acquisitions
    ("DEU_V008", ["DEU_V014"],
        "BioNTech IMFS GmbH (Idar-Oberstein)"),
    ("DEU_V007", ["DEU_V009", "DEU_V016", "DEU_V022"],
        "CureVac (Tübingen)"),
    ("DEU_V005", ["DEU_V019"],
        "GlaxoSmithKline Biologicals (Dresden)"),
    ("DEU_V020", ["DEU_V023"],
        "Takeda GmbH (Singen)"),

    # UK — agency renames + corporate acquisitions
    ("GBR_V001", ["GBR_V002", "GBR_V009", "GBR_V017"],
        "Porton Biopharma Limited (Porton Down)"),
    ("GBR_V003", ["GBR_V005", "GBR_V012"],
        "AstraZeneca Liverpool (MedImmune UK Limited)"),
    ("GBR_V008", ["GBR_V014"],
        "Merck BioReliance (Glasgow)"),
    ("GBR_V011", ["GBR_V015", "GBR_V016"],
        "Charles River (Keele Science Park)"),

    # Netherlands
    ("NLD_V001", ["NLD_V003"],
        "Patheon Biologics BV (Groningen)"),
    ("NLD_V002", ["NLD_V009"],
        "Wacker Biotech B.V. (Amsterdam)"),

    # Sweden — Crucell acquired by Valneva
    ("SWE_V003", ["SWE_V005", "SWE_V006"],
        "Valneva Sweden AB (Stockholm)"),

    # USA
    ("USA_V001", ["USA_V014"],
        "Emergent Biosolutions (Lansing)"),
    ("USA_V016", ["USA_V021"],
        "Pfizer Inc / BioNTech Manufacturing GmbH"),

    # Switzerland — successive corporate owners of same Thorishaus facility
    ("CHE_V002", ["CHE_V003", "CHE_V005"],
        "Bavarian Nordic Berna GmbH (Thorishaus)"),
    ("CHE_V004", ["CHE_V006"],
        "Lonza AG (Visp)"),
]

# ── Defence entity merge groups ──────────────────────────────────────────────
# Same structure as MERGES but operates on defence_entities / defence_facilities.
# Merge groups verified against defence_facilities.city in local DB 2026-03-18.

DEFENCE_MERGES = [
    # ── CHE (40 → 14) — multilingual name variants, verified by city ────────
    ("CHE_D001", ["CHE_D002", "CHE_D003", "CHE_D004", "CHE_D005"],
        "Spiez Laboratory"),
    ("CHE_D015", ["CHE_D006", "CHE_D032"],
        "Institute of Virology and Immunology (IVI)"),
    ("CHE_D007", ["CHE_D016", "CHE_D025"],
        "National Reference Center for Emerging Viral Infections (HUG Geneva)"),
    ("CHE_D008", ["CHE_D017"],
        "National Reference Center for Anthrax"),
    ("CHE_D009", ["CHE_D018", "CHE_D029", "CHE_D033"],
        "Bacteriological Laboratory (HUG Geneva)"),
    ("CHE_D010", ["CHE_D019"],
        "Virological Laboratory (HUG Geneva)"),
    ("CHE_D011", ["CHE_D020", "CHE_D030", "CHE_D035"],
        "Diagnostic Laboratories of the Institute of Microbiology (CHUV Lausanne)"),
    ("CHE_D013", ["CHE_D023", "CHE_D027"],
        "Cantonal Laboratory of Basel-Stadt"),
    ("CHE_D014", ["CHE_D024", "CHE_D028", "CHE_D040"],
        "Cantonal Institute of Microbiology (Bellinzona)"),
    ("CHE_D021", ["CHE_D031", "CHE_D036", "CHE_D038", "CHE_D039"],
        "Department of Medical Microbiology (Luzerner Kantonsspital)"),
    ("CHE_D034", ["CHE_D037"],
        "Laboratory of Applied Microbiology (Bellinzona)"),
    # NOTE: CHE_D012 (Zürich+Luzern mixed), CHE_D022 (Zürich+Bellinzona mixed),
    # and CHE_D026 (unique Zürich) are left unmerged — their facility_years rows
    # span multiple cities, requiring row-level correction rather than entity merge.

    # ── CAN (9 → 2) — DRDC name variants ────────────────────────────────────
    ("CAN_D001", ["CAN_D004", "CAN_D005", "CAN_D008"],
        "DRDC Suffield Research Centre"),
    ("CAN_D002", ["CAN_D003", "CAN_D006", "CAN_D007", "CAN_D009"],
        "DRDC Valcartier Research Centre"),

    # ── BEL (12 → 6) — CTMA/DLD-Bio name variants ──────────────────────────
    ("BEL_D001", ["BEL_D004", "BEL_D005", "BEL_D007", "BEL_D008", "BEL_D010", "BEL_D011"],
        "Belgian Defence Biological Laboratory (CTMA/DLD-Bio)"),

    # ── USA (63 → 42) — renames, reorgs, duplicate entries ──────────────────
    # Edgewood → DEVCOM CBC (Aberdeen Proving Ground)
    ("USA_D004", ["USA_D008", "USA_D055"],
        "U.S. Army DEVCOM Chemical Biological Center (CBC)"),
    # Lothar Salomon (Dugway)
    ("USA_D003", ["USA_D030"],
        "Lothar Salomon Life Sciences Test Facility (LSTF)"),
    # Tyndall AFB (all test areas, Tyndall AFB FL)
    ("USA_D002", ["USA_D031", "USA_D032", "USA_D038"],
        "Tyndall Air Force Base"),
    # IRF-RML (Hamilton, Montana)
    ("USA_D021", ["USA_D036"],
        "Integrated Research Facility at Rocky Mountain Laboratories (IRF-RML)"),
    # IRF-Frederick (Frederick, Maryland)
    ("USA_D046", ["USA_D050"],
        "Integrated Research Facility at Fort Detrick (IRF-Frederick)"),
    # NSWC Dahlgren (Dahlgren, Virginia) — CBR Defense Lab + Concepts & Experimentation Lab
    ("USA_D010", ["USA_D037", "USA_D061"],
        "Naval Surface Warfare Center (NSWC) Dahlgren Division"),
    # NIH CW Bill Young Center (Bethesda)
    ("USA_D020", ["USA_D042"],
        "C.W. Bill Young Center for Biodefense and Emerging Infectious Diseases (NIH)"),
    # NIH VRC (Bethesda)
    ("USA_D022", ["USA_D043"],
        "Dale and Betty Bumpers Vaccine Research Center (NIH)"),
    # AFRL 711th HPW (Wright-Patterson AFB / Dayton)
    ("USA_D051", ["USA_D059"],
        "Air Force Research Laboratory (AFRL), 711th Human Performance Wing"),
    # CDC main infectious disease division (Atlanta) — CCID → OID → DDID → CDC
    ("USA_D023", ["USA_D035", "USA_D048", "USA_D053", "USA_D063"],
        "Centers for Disease Control and Prevention (CDC) — Atlanta"),
    # CDC DVBD (Fort Collins, CO — different city from Atlanta, kept separate)
    ("USA_D024", ["USA_D034", "USA_D041", "USA_D049"],
        "CDC Division of Vector Borne Diseases (DVBD) — Fort Collins"),
    # CDC Mass Spec Toxin Lab (Atlanta)
    ("USA_D025", ["USA_D033"],
        "CDC Mass Spectrometry Toxin Laboratory"),
    # CDC NCEH (Atlanta)
    ("USA_D040", ["USA_D047"],
        "CDC National Center for Environmental Health (NCEH)"),
    # FDA White Oak / CBER (Silver Spring, MD)
    ("USA_D054", ["USA_D057"],
        "FDA White Oak Campus"),

    # ── DEU — bilingual name variants (EN/DE alternating submissions) ──────
    ("DEU_D002", ["DEU_D006", "DEU_D007"],
        "Bundeswehr Institute of Microbiology"),
    ("DEU_D001", ["DEU_D005", "DEU_D009", "DEU_D012", "DEU_D017"],
        "CBRN Defence School (Sonthofen)"),
    ("DEU_D003", ["DEU_D008", "DEU_D014"],
        "Bundeswehr Research Institute for Protective Technologies — NBC Protection"),
    ("DEU_D010", ["DEU_D011", "DEU_D013"],
        "Centre for Biological Threats and Special Pathogens (ZBS), Robert Koch Institute"),
    ("DEU_D004", ["DEU_D015", "DEU_D016", "DEU_D018"],
        "Central Institute of the Bundeswehr Medical Service Kiel"),

    # ── AUS — DSTO → DST Group rename chain (same Fishermans Bend address) ─
    ("AUS_D001", ["AUS_D002", "AUS_D003", "AUS_D004", "AUS_D005"],
        "Biological Defence Research, Defence Science and Technology Group"),

    # ── NOR — abbreviated name variants (Oslo → Kjeller relocation) ────────
    ("NOR_D001", ["NOR_D002", "NOR_D005"],
        "Institute of Microbiology (FML)"),

    # ── SWE ─────────────────────────────────────────────────────────────────
    ("SWE_D001", ["SWE_D004"],
        "Swedish Defence Research Agency (FOI), CBRN Defence"),
    # National Veterinary Institute → Swedish Veterinary Agency
    ("SWE_D002", ["SWE_D003", "SWE_D005"],
        "Swedish Veterinary Agency (SVA)"),

    # ── CZE ─────────────────────────────────────────────────────────────────
    ("CZE_D001", ["CZE_D004", "CZE_D006"],
        "Institute of Molecular Pathology (IMP), University of Defence"),
    ("CZE_D005", ["CZE_D007"],
        "Laboratory for Biological Monitoring and Protection"),

    # ── ROU — same Bucharest military lab ───────────────────────────────────
    ("ROU_D001", ["ROU_D004", "ROU_D005"],
        "Military Medical Research Center (Bucharest)"),

    # ── PRT — EN/PT language variants of same army lab ─────────────────────
    ("PRT_D001", ["PRT_D002", "PRT_D003", "PRT_D004"],
        "Laboratório de Bromatologia e Defesa Biológica (LBDB)"),

    # ── DNK — Biological Defence → Biosecurity rename (same CBB abbreviation)
    ("DNK_D001", ["DNK_D002"],
        "Centre for Biosecurity and Biopreparedness (CBB)"),

    # ── LVA — hospital org-name evolution (same Riga address) ──────────────
    ("LVA_D001", ["LVA_D002", "LVA_D003", "LVA_D004"],
        "Riga East University Hospital, National Microbiology Reference Laboratory"),

    # ── BGR ─────────────────────────────────────────────────────────────────
    ("BGR_D001", ["BGR_D002"],
        "National Center of Infectious and Parasitic Diseases"),

    # ── FIN — added parent org prefix ──────────────────────────────────────
    ("FIN_D001", ["FIN_D004"],
        "Centre for Biothreat Preparedness"),
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


# ── Defence entity helpers ───────────────────────────────────────────────────

def fetch_defence_entity(cur, entity_id: str) -> dict | None:
    cur.execute(
        "SELECT canonical_defence_facility_id, canonical_name, country_iso3, "
        "       all_names, first_year, last_year "
        "FROM defence_entities WHERE canonical_defence_facility_id = %s",
        (entity_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def count_defence_facilities(cur, entity_id: str) -> int:
    cur.execute(
        "SELECT count(*) FROM defence_facilities "
        "WHERE canonical_defence_facility_id = %s",
        (entity_id,),
    )
    return cur.fetchone()["count"]


def print_defence_merge_plan(
    cur, keeper_id: str, deprecated_ids: list[str], new_name: str,
) -> None:
    keeper = fetch_defence_entity(cur, keeper_id)
    if not keeper:
        print(f"  [WARN] Defence keeper '{keeper_id}' not found — skipping")
        return
    keeper_df = count_defence_facilities(cur, keeper_id)
    print(f"\n  DEFENCE MERGE → keeper: {keeper_id}  ({keeper['canonical_name'] or '[unnamed]'})")
    print(f"    new canonical_name : {new_name}")
    print(f"    keeper defence_facilities: {keeper_df}")
    for dep_id in deprecated_ids:
        dep = fetch_defence_entity(cur, dep_id)
        if not dep:
            print(f"    [WARN] Deprecated '{dep_id}' not found")
            continue
        dep_df = count_defence_facilities(cur, dep_id)
        print(f"    merge in:  {dep_id}  ({dep['canonical_name'] or '[unnamed]'})  → {dep_df} defence_facilities")


def apply_defence_merge(
    cur, keeper_id: str, deprecated_ids: list[str], new_name: str,
) -> None:
    keeper = fetch_defence_entity(cur, keeper_id)
    if not keeper:
        print(f"  [SKIP] Defence keeper '{keeper_id}' not found")
        return

    # Collect all_names from deprecated entities
    combined_names: set[str] = set(keeper["all_names"] or [])
    found_deprecated: list[str] = []
    for dep_id in deprecated_ids:
        dep = fetch_defence_entity(cur, dep_id)
        if not dep:
            print(f"  [SKIP] Deprecated defence '{dep_id}' not found — skipping this ID")
            continue
        found_deprecated.append(dep_id)
        combined_names.update(dep["all_names"] or [])

    if not found_deprecated:
        print(f"  [SKIP] No deprecated defence entities found for keeper {keeper_id}")
        return

    # Step 1 — re-point defence_facilities to the keeper
    cur.execute("""
        UPDATE defence_facilities
        SET    canonical_defence_facility_id = %s
        WHERE  canonical_defence_facility_id = ANY(%s)
    """, (keeper_id, found_deprecated))
    print(f"  [OK] Re-pointed {cur.rowcount} defence_facilities → {keeper_id}")

    # Step 2 — compute year range for the keeper
    cur.execute("""
        SELECT MIN(year) AS first_year, MAX(year) AS last_year
        FROM   defence_facilities
        WHERE  canonical_defence_facility_id = %s
    """, (keeper_id,))
    row = cur.fetchone()

    # Step 3 — update the keeper entity
    all_names_list = sorted(combined_names)
    cur.execute("""
        UPDATE defence_entities
        SET    canonical_name = %s,
               all_names      = %s,
               first_year     = %s,
               last_year      = %s
        WHERE  canonical_defence_facility_id = %s
    """, (new_name, all_names_list, row["first_year"], row["last_year"], keeper_id))
    print(f"  [OK] Updated defence keeper {keeper_id}: name='{new_name}', "
          f"{len(all_names_list)} names, {row['first_year']}–{row['last_year']}")

    # Step 4 — delete deprecated defence entity rows
    cur.execute("""
        DELETE FROM defence_entities
        WHERE  canonical_defence_facility_id = ANY(%s)
    """, (found_deprecated,))
    print(f"  [OK] Deleted {cur.rowcount} deprecated defence_entity rows: {found_deprecated}")


# ── Row-level splits ────────────────────────────────────────────────────────
# Each entry: (source_entity, new_entity_id, new_canonical_name, sql_filter)
# The sql_filter is a WHERE clause fragment applied to facility_years to select
# which rows to move from source_entity to the new entity.

SPLITS = [
    # CZE_005 contains a Praha row (2011) that belongs to a separate facility.
    ("CZE_005", "CZE_018", "Central Military Health Institute, department Prague",
     "city = 'Praha' AND year = 2011"),
]


def apply_split(cur, source_id: str, new_id: str, new_name: str, row_filter: str) -> None:
    """Split rows matching row_filter out of source_id into a new entity."""
    # Check source exists
    source = fetch_entity(cur, source_id)
    if not source:
        print(f"  [SKIP] Source '{source_id}' not found — skipping split")
        return

    # Check if new entity already exists (idempotent)
    existing = fetch_entity(cur, new_id)
    if existing:
        print(f"  [SKIP] Target '{new_id}' already exists — split already applied")
        return

    # Count rows to move
    cur.execute(
        f"SELECT count(*) FROM facility_years "
        f"WHERE canonical_facility_id = %s AND {row_filter}",
        (source_id,),
    )
    n = cur.fetchone()["count"]
    if n == 0:
        print(f"  [SKIP] No rows match filter for split {source_id} → {new_id}")
        return

    # Create the new facility entity
    cur.execute("""
        INSERT INTO facilities (canonical_facility_id, canonical_name, country_iso3,
                                all_names, years_declared)
        VALUES (%s, %s, %s, %s, ARRAY[]::int[])
    """, (new_id, new_name, source["country_iso3"], [new_name]))

    # Move matching rows
    cur.execute(
        f"UPDATE facility_years SET canonical_facility_id = %s "
        f"WHERE canonical_facility_id = %s AND {row_filter}",
        (new_id, source_id),
    )
    print(f"  [OK] Split {n} row(s) from {source_id} → {new_id} ({new_name})")

    # Recompute years_declared for both entities
    for eid in (source_id, new_id):
        cur.execute("""
            UPDATE facilities
            SET years_declared = (
                SELECT ARRAY_AGG(DISTINCT year ORDER BY year)
                FROM facility_years WHERE canonical_facility_id = %s
            )
            WHERE canonical_facility_id = %s
        """, (eid, eid))


def print_split_plan(cur, source_id: str, new_id: str, new_name: str, row_filter: str) -> None:
    """Print what this split would do."""
    source = fetch_entity(cur, source_id)
    if not source:
        print(f"  [WARN] Source '{source_id}' not found — will be skipped")
        return
    existing = fetch_entity(cur, new_id)
    if existing:
        print(f"  [INFO] Target '{new_id}' already exists — split already applied")
        return
    cur.execute(
        f"SELECT count(*) FROM facility_years "
        f"WHERE canonical_facility_id = %s AND {row_filter}",
        (source_id,),
    )
    n = cur.fetchone()["count"]
    print(f"\n  SPLIT: {source_id} → {new_id}")
    print(f"    filter: {row_filter}")
    print(f"    rows to move: {n}")
    print(f"    new name: {new_name}")


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
    print(f"Row-level splits:               {len(SPLITS)}")
    print(f"Research facility merge groups: {len(MERGES)}")
    print(f"Vaccine facility merge groups:  {len(VACCINE_MERGES)}")
    print(f"Defence facility merge groups:  {len(DEFENCE_MERGES)}")

    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Cannot connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Always print plans first
            print("\n--- ROW-LEVEL SPLIT PLAN ---")
            for source_id, new_id, new_name, row_filter in SPLITS:
                print_split_plan(cur, source_id, new_id, new_name, row_filter)

            print("\n--- RESEARCH FACILITY MERGE PLAN ---")
            for keeper_id, deprecated_ids, new_name in MERGES:
                print_merge_plan(cur, keeper_id, deprecated_ids, new_name)

            print("\n--- VACCINE FACILITY MERGE PLAN ---")
            for keeper_id, deprecated_ids, new_name in VACCINE_MERGES:
                print_vaccine_merge_plan(cur, keeper_id, deprecated_ids, new_name)

            print("\n--- DEFENCE FACILITY MERGE PLAN ---")
            for keeper_id, deprecated_ids, new_name in DEFENCE_MERGES:
                print_defence_merge_plan(cur, keeper_id, deprecated_ids, new_name)

            if not args.apply:
                print("\n[DRY-RUN] No changes made. Pass --apply to execute.")
                return

            # Apply splits first (before merges, so source entities are intact)
            print("\n--- APPLYING ROW-LEVEL SPLITS ---")
            for source_id, new_id, new_name, row_filter in SPLITS:
                print(f"\nSplit: {source_id} → {new_id}")
                apply_split(cur, source_id, new_id, new_name, row_filter)

            # Apply merges
            print("\n--- APPLYING RESEARCH FACILITY MERGES ---")
            for keeper_id, deprecated_ids, new_name in MERGES:
                print(f"\nGroup: {keeper_id} ← {deprecated_ids}")
                apply_merge(cur, keeper_id, deprecated_ids, new_name)

            print("\n--- APPLYING VACCINE FACILITY MERGES ---")
            for keeper_id, deprecated_ids, new_name in VACCINE_MERGES:
                print(f"\nGroup: {keeper_id} ← {deprecated_ids}")
                apply_vaccine_merge(cur, keeper_id, deprecated_ids, new_name)

            print("\n--- APPLYING DEFENCE FACILITY MERGES ---")
            for keeper_id, deprecated_ids, new_name in DEFENCE_MERGES:
                print(f"\nGroup: {keeper_id} ← {deprecated_ids}")
                apply_defence_merge(cur, keeper_id, deprecated_ids, new_name)

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
