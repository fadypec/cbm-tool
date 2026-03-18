# CBM Facility Explorer — Completeness Assessment

**Date:** 2026-03-18
**Scope:** Independent audit of project completeness across all dimensions

---

## What IS done

The core mission — "extract structured data from BWC CBM PDF submissions and make it accessible" — is **complete and deployed**.

| Component | Status | Scale |
|---|---|---|
| PDF acquisition | Done | 517 documents from UN portal |
| OCR + text extraction | Done | 8 OCR docs w/ Claude correction |
| Form segmentation | Done | 98.4% regex, 1.6% LLM fallback |
| Form A1 (research facilities) | Done | 1,600 facility-years, 457 facilities, 45 countries |
| Form G (vaccine facilities) | Done | 599 facility-year records |
| Form A2 (defence programmes) | Done | 560 programmes, 969 facilities |
| Form F (past programmes) | Done | 354 records |
| Form E (legislation) | Done | 324 records, 42 countries |
| Form 0 (compliance matrix) | Done | All 517 docs categorised |
| Entity resolution | Done | Union-Find + manual merges |
| Geocoding | Done | 97.3% match rate |
| PostgreSQL + PostGIS | Done | 9 tables, 13 migrations |
| REST API | Done | 25 endpoints, rate-limited, auth-guarded |
| Interactive dashboard | Done | Map, choropleth, search, drill-down, compare, trends, export |
| Security hardening | Done | CSP, headers, rate limiting, SQL parameterisation |
| Accessibility audit | Done | ARIA, contrast, colorblind patterns |
| Mobile responsiveness | Done | Audited and fixed |
| Deployment | Done | Railway auto-deploy, Supabase DB, healthcheck |
| Monitoring | Done | UptimeRobot + Umami analytics |
| Dark/light theme | Done | With localStorage persistence |

~13,650 lines of code across API, dashboard, extraction pipeline, database, and tests. The tool is live at `https://cbm.fady.phd` and functional.

---

## What is NOT done

### A. Data Quality — Manual Verification (requires human, not code)

These require opening source PDFs and checking numbers. No code can do this.

| # | Item | Impact | Time |
|---|---|---|---|
| 1 | USA Plum Island 17,643 m² BSL-3 — likely 8x error | Inflates US totals | 10 min |
| 2 | CIV CEPRIS 1,000 m² BSL-4 — likely wrong BSL level | False BSL-4 claim | 10 min |
| 3 | AUS AAHL 11,000 m² BSL-4 — likely total site area | Inflates BSL-4 by 20x for 2006-2009 | 10 min |
| 4 | GBR Pirbright + Boehringer BSL-4 areas | Affects UK BSL-4 totals | 15 min |
| 5 | NLD Wageningen 4,500 m² BSL-4 | New entry, large number | 10 min |
| 6 | DEU FLI 264→1,251 m² BSL-4 jump in 2023 | Probably correct (new building) | 10 min |
| 7 | PRT_003 ghost record (zero confidence) | Noise in data | 5 min |
| 8 | AUT legislation 14 rows all-NULL booleans | Compliance status wrong | 15 min |
| 9 | JPN Murayama intermittent BSL-4 area | BSL-3/4 confusion | 10 min |
| 10 | UKR SSCIBSM 1,374 m² BSL-4 in 2023 | High-significance claim | 10 min |

**Estimated total: ~2 hours.** This is the single most important remaining work for data credibility. Items 1-3 are Tier 1 — if you present this tool to the ISU or any subject-matter expert, the AUS 11,000 m² BSL-4 and the CIV BSL-4 claim will immediately undermine confidence if they turn out to be wrong.

### B. Entity Resolution — Remaining Merges

Entity fragmentation remains significant for several countries:

| Country | Current | Should be | Problem |
|---|---|---|---|
| CHE | 14 entities | ~4 | German/French/English name variants |
| USA (CDC) | 5 entities | 1 | Administrative reorg names |
| UKR (Mechnikov) | 5 entities | 1 | Russian/Ukrainian transliteration |
| DEU (Marburg) | 2 entities | 1 | Language switch in 2020 |
| CHE defence | 40 entities | ~4 | Same as A1 but worse |
| CAN defence | 9 entities | ~3 | DRDC split |
| BEL defence | 12 entities | ~4 | CTMA/DLD-Bio split |
| USA defence | 63 entities | ~20? | Edgewood etc. |

**Estimated effort: 3-4 hours** (verify current IDs in DB, add merge groups to `dedup_entities.py`, run). The A1 merges (CHE/USA/UKR/DEU) are straightforward. The defence merges (CHE/CAN/BEL/USA with ~124 entities needing consolidation) are more work and the `DEFENCE_MERGES` handler doesn't exist yet — it would need to be written.

### C. Geocoding Gaps

| Country | Missing | Total | Gap |
|---|---|---|---|
| Estonia | 58 | 70 | 83% |
| Mexico | 77 | 129 | 60% |
| AUS QHFSS 2024 | 1 | 1 | Wrong location |

**Estonia** and **Mexico** are the two biggest holes on the map. Estonia is a prolific CBM submitter but nearly invisible. Mexico has more facility-years than most countries. Both are fixable — Estonia with ~5 manual institution lookups (~30 min), Mexico by investigating address formatting (~30 min diagnostic + possible re-geocode).

### D. Engineering Quality

| Gap | Severity | Effort |
|---|---|---|
| **No CI/CD pipeline** | Medium | 1-2 hrs (GitHub Actions for pytest + lint) |
| **Test coverage: 18%** (5/28 endpoints) | Medium | 4-6 hrs for reasonable coverage |
| **No extraction script tests** | Low-Medium | 3-4 hrs (unit tests for parsing logic) |
| **No `.env.example`** | Low | 5 min |
| **No structured logging** | Low | 30 min |
| **Dockerfile runs as root** | Low | 5 min (add USER directive) |
| **No readiness probe** (DB connectivity) | Low | 15 min |

The test gap is real but contextual. This is a single-maintainer research tool, not a team codebase. The 22 existing tests cover security-critical paths (headers, auth, health). What's missing are happy-path tests for data endpoints — these would catch regressions if someone modifies SQL queries. Whether it's worth 4-6 hours depends on how much future maintenance you expect.

The CI/CD gap is the one flagged most strongly. Right now, there's nothing stopping a broken commit from auto-deploying to production. Even a minimal GitHub Action (`pytest tests/`) would catch obvious breakage.

### E. Feature Completeness (Roadmap)

| Item | Status | Notes |
|---|---|---|
| Form A1 | Done | |
| Form A2 | Done | |
| Form E | Done | |
| Form F | Done | |
| Form G | Done | |
| **Form B** (outbreaks) | **Deferred** | Intentionally — free-text, no use case. Migration 012 created the table but no extraction logic exists |
| **Form C** (publications) | **Not started** | Listed as Phase 3, lower priority |
| **Longitudinal analysis** | **Partially done** | Timeline, BSL-4 trends, notable changes exist. Could go deeper |
| **ISU outreach** | **Not started** | External dependency, not a code task |
| **Restricted corpus** | **Not started** | Requires ISU partnership (CHN/FRA/RUS/IND) |

Form B and C are deliberate scope decisions, not oversights. Form B is genuinely low-value (free-text outbreak descriptions with no structured fields). Form C (publications) could be interesting but is a different kind of problem (matching to open-access databases).

### F. Minor/Cosmetic Items

- **Non-standard containment values** (BSL-2+, BSL-3+, Enhanced BSL-2) map to "unknown" grey on the dashboard. Could add explicit colour handling.
- **40 legislation records (12%) with all-NULL booleans** — noise in the dataset beyond the AUT issue.
- **37 records with has_bsl4=True but bsl4_area_m2=NULL** — not wrong, but limits area analysis.
- **Year 2026 data** (BLZ, TUV) — cosmetically confusing in year filters but technically correct.
- **Review badge doesn't auto-refresh** — acceptable for a low-traffic admin feature.

---

## Verdict

**As a functional research tool: Yes, it's done.** The extraction pipeline processes all 517 public CBM documents across 5 form types, structures the data in a relational database, and serves it through a polished interactive dashboard with maps, search, drill-down, comparison, trends, and export. It's deployed, monitored, and publicly accessible. This is a substantial piece of work.

**As a tool to present to the ISU: Almost.** The ~2 hours of manual PDF verification (Section A) is the gap. If the AUS 11,000 m² BSL-4 or the CIV BSL-4 claim is wrong and an ISU analyst spots it, it undermines the credibility of everything else. The remaining entity merges (Section B) are less critical for a demo but would make country profiles look cleaner.

**As production-grade software: No, but it doesn't need to be.** The missing CI/CD and thin test coverage are real engineering gaps, but this is a single-maintainer research tool with one deployment target, not a team codebase with multiple contributors. The code quality itself is excellent — no dead code, no TODOs, no hacks, proper error handling, good security posture. The gaps are in *process* (automated testing, CI/CD), not in *substance*.

**In terms of BWC coverage: ~80%.** Forms A1, A2, E, F, and G are done. Form B is deferred (reasonable). Form C is not started. The restricted corpus (4 major countries) requires an external partnership. These are scope decisions, not bugs.

### Recommended priority order

1. **Manual PDF verification** (~2 hrs) — Tier 1 items from VALIDATION_CHECKLIST. This is the highest-leverage work remaining. It protects your credibility.

2. **Remaining entity merges** (~3-4 hrs) — CHE, USA CDC, UKR, DEU for A1; CHE/CAN/BEL/USA for defence. Makes country profiles accurate.

3. **CI/CD pipeline** (~1-2 hrs) — A GitHub Action that runs `pytest tests/` on push to main. Prevents broken deployments.

4. **Estonia/Mexico geocoding** (~1 hr) — Two countries with 60-83% missing map coverage.

Everything else is marginal improvement on a tool that already works well.
