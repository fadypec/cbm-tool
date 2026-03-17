# Next Steps — CBM Facility Explorer

## Features not yet implemented from original brainstorm

- **Lapsed declarations view** — design agreed, awaiting implementation go-ahead
- **Facility biography timeline** — mentioned in brainstorm, not yet revisited

---

## High impact — no comparable tool does this

**1. Lapsed declarations view**
Facilities declared for years then dropped from submissions are analytically the most interesting records in the dataset. A muted marker + "last declared 2018" label would be immediately legible to any BWC researcher. Implementation design already agreed: filter toggle with year threshold, relative to each country's most recent submission year.

**2. Transparency index per country**
A composite score beyond raw A1 rate — weighting regularity of submission, number of forms filled substantively, recency, and year-on-year consistency. The current choropleth rewards countries with nothing to declare; a transparency score distinguishing "compliant because nothing to declare" from "compliant and detailed" would be cited in research.

**3. Country comparison pane**
Select two countries side-by-side: compliance grid, BSL-4 facility count, declared organisms overlap, submission history. Researchers routinely make this comparison in papers; one-click access would be genuinely novel.

**4. Global BSL-4 capacity chart**
Total declared BSL-4 area (m²) globally over time, broken down by country. The data is all in the database. The kind of chart that gets reproduced in think-tank reports and news articles.

---

## Medium impact — significant UX improvement

**5. Interactive trends chart**
The current SVG chart has no hover interaction. Tooltips showing exact values per year (and contributing countries) would make the chart usable for data extraction, not just browsing.

**6. Facility biography timeline view**
The entity modal currently shows a vertical stack of year cards. A horizontal timeline bar with notable events (BSL-4 gained, personnel spike, organisms changed) would be far more legible for facilities with 10+ years on record — a more visual version of the existing Changes tab.

**7. Full-text search of extracted PDFs**
Current search only hits `agents_summary` (the 200-char extracted field). A full-text search endpoint over the raw extracted text would surface details the structured extraction missed — responsible org changes, specific lab descriptions, funding notes.

**8. Quick-filter bar on the map**
A thin persistent bar below the map showing active filters as dismissible chips (organism, year, BSL level). Currently you have to look inside the filter panel to know what filters are active.

---

## Lower impact — polish

**9. Country report card export**
A single-page HTML or PDF summary for a country: compliance history, facility list, notable changes, key organisms declared. Something a journalist or briefing author could paste straight into a report.

**10. Hover tooltips on map markers**
On desktop, hovering a marker should show a brief tooltip (name, year, BSL level) before committing to a click — standard behaviour for this type of map.

**11. Consistent dark/light theme**
The navbar and sidebar are dark; the map, filter panel, and modals are light. A full dark theme would remove this inconsistency and give the tool a more finished feel.

---

## Recommended priority order

1. Lapsed declarations — design agreed, highest analytical value
2. Interactive chart tooltips — low effort, high payoff
3. Country comparison pane — makes the tool genuinely research-grade
4. Transparency index — needs methodological discussion on formula, but would be the headline feature for any publication
