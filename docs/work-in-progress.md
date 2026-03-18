# Work in Progress — 2026-03-18

## Security (AUDIT_SECURITY.md)
ALL items confirmed fixed. Document is outdated.
- H1 esc() in exportCountryReport: DONE
- H2 single-quote escaping in esc(): DONE
- M1 unsafe-inline scripts removed: DONE (style-src retains it for Bootstrap — unavoidable)
- M2 rightmost XFF entry trusted: DONE
- M4 docker-compose credentials: DONE (requires POSTGRES_PASSWORD env var)

---

## Data quality (AUDIT_DATA.md) — in progress this session

### Implementing now (no PDF verification needed)

**A. dedup_entities.py — new merge groups**

GBR (31 → ~13 entities):
- Porton Down HPA/PHE/UKHSA: keep GBR_001, merge GBR_006, GBR_019, GBR_026, GBR_030
- Colindale HPA/PHE/UKHSA: keep GBR_002, merge GBR_005, GBR_018, GBR_027, GBR_028
- Dstl Porton Down: keep GBR_004, merge GBR_013
- VLA/AHVLA/APHA: keep GBR_008, merge GBR_015, GBR_020
- IAH/Pirbright: keep GBR_009, merge GBR_014, GBR_017
- Merial/Boehringer: keep GBR_010, merge GBR_016, GBR_023, GBR_024
- NIMR/Francis Crick: keep GBR_007, merge GBR_021, GBR_022
- NIBSC/MHRA: keep GBR_003, merge GBR_029, GBR_031
- Schering-Plough/Intervet: keep GBR_011, merge GBR_012

AUS:
- AUS_001 (AAHL 2006-2020) + AUS_005 (ACDP 2021-2025) → keep AUS_001

DNK unnamed (DNK_008–DNK_021, 14 null entities → 1):
- keep DNK_008, merge DNK_009–DNK_021

CYP unnamed (CYP_001–CYP_007, 7 null entities → 1):
- keep CYP_001, merge CYP_002–CYP_007

SVK unnamed (SVK_001–SVK_005, 5 null entities → 1):
- keep SVK_001, merge SVK_002–SVK_005

**B. dedup_entities.py — extend to handle vaccine entities**
- BGR BulBio: keep BGR_V001, merge BGR_V002–BGR_V006

### Deferred (requires manual PDF verification)
- Fix AUS AAHL 11,000 m² BSL-4 area (VALIDATION_CHECKLIST §C / AUDIT_DATA §2A)
- Delete PRT_003 ghost record (must verify PDF first — VALIDATION_CHECKLIST §H)
- Fix AUT legislation compliance status (14 rows where Form E "substantive" but all NULL booleans)
- Correct AUS QHFSS 2024 geocoordinates (VALIDATION_CHECKLIST §P)
- Manual geocoding: Estonia 5-10 institutions, Mexico address check
- Defence entity merges CHE/CAN/BEL/USA (AUDIT_DATA §1F) — need entity ID verification
- BSL area corrections: USA Plum Island, CIV CEPRIS, GBR Pirbright/Boehringer, NLD Wageningen
