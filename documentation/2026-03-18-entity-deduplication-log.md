# Entity Deduplication — Session Log (2026-03-18)

## What was done

Comprehensive entity deduplication was performed across all three entity types
(research facilities, vaccine facilities, defence facilities) in a single session.

Merge groups were identified by scanning for pairs that satisfied all of:
- Same country
- No overlapping `years_declared` (so they cannot be different active entities)
- High name similarity (rapidfuzz `token_sort_ratio ≥ 85`, visually verified)
- Same city / BSL / containment level where available

Groups were then added to `scripts/dedup_entities.py` and applied via:

```bash
python3 scripts/dedup_entities.py --apply
```

## Prompt used to trigger the systematic scan

> Then, generalise this logic to search across all the existing entities (research,
> defence, vaccines) to identify likely duplicate entries. Use the pattern you've
> detected here (no year overlap, very similar naming with minor typographical
> differences or appended locations, same agents, same BSL classification) and apply
> it across the full dataset. When you identify high-likelihood candidates, amend
> `dedup_entities.py` to consolidate them.

## Results

| Entity type       | Before | After | Groups merged |
|-------------------|--------|-------|---------------|
| Research (A1)     | ~380   | 318   | 65            |
| Vaccine (G)       | ~150   | 128   | 21            |
| Defence (A2)      | ~150   | 107   | 45            |
| **Total**         |        |       | **131**       |

## Key individual merges

- **IRL_002 / IRL_008 / IRL_023** — Public Health Laboratory (PHL), Cherry Orchard
  Hospital. IRL_002 declared in 2012–2013, IRL_008 in 2014/2018, IRL_023 in 2019.
  All three are the same Galway-Road HSE microbiology lab; name varied year to year.
  Merged into IRL_002 with canonical name
  `"Public Health Laboratory (PHL), Cherry Orchard Hospital"`.

- **USA_D010** — Dahlgren Naval Surface Warfare Center expanded to absorb USA_D061
  (same base, concepts lab listed separately in some years).

## Commit reference

`e987f77` — "feat: comprehensive entity deduplication across all three entity types"

## Outstanding

- `.github/workflows/test.yml` was committed locally but not pushed — GitHub PAT
  requires `workflow` scope. Re-push once PAT is updated.
- Containment-color handling (BSL-2+/BSL-3+/Enhanced BSL-2): options A/B/C were
  proposed to the user; awaiting decision. Options were:
  - **A** No change — existing `bslColor()` already maps these correctly via `.includes()`
  - **B** Normalise at DB level (store `2`, `3`, etc.) and keep raw value in a new column
  - **C** Add tooltip clarification in the dashboard popup showing the raw value
