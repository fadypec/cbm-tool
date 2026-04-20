# ISU Feedback Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address 6 items of feedback from the BWC ISU meeting: dark mode contrast, light mode 0% tag, pathogen bar alignment, About modal additions, comparison mode enhancements, and "compliance" → "submission" terminology rename.

**Architecture:** All changes are frontend (dashboard HTML/CSS/JS) and one API query change (`/api/countries` switching from document count to year count). No database migrations needed. No new files.

**Tech Stack:** Plain CSS, vanilla JS, FastAPI (Python), PostgreSQL

---

### Task 1: Dark mode contrast — compliance grid text

The compliance grid in the country detail panel has low-contrast text on dark backgrounds. Form names (A1, A2, etc.), year labels, and legend text use muted blue-gray colors (#7080a0, #8090b0, #8090b8) that become illegible on some Windows displays with different font rendering.

**Files:**
- Modify: `dashboard/static/style.css:355-365` (compliance grid base styles)

- [ ] **Step 1: Brighten compliance grid text colors**

In `dashboard/static/style.css`, update these dark-mode base styles to use higher-contrast values:

```css
/* Line 355 — legend */
.compliance-legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px; color: #b0bee0; margin-bottom: 8px; align-items: center; }

/* Line 362 — form header cells (A1, A2, etc.) */
.compliance-grid th { color: #b0bee0; font-size: 10px; font-weight: 700; padding: 2px 0 4px; text-align: center; letter-spacing: 0.02em; }

/* Line 363 — "Year" header */
.compliance-grid th.yr-col { text-align: left; color: #a0b0d0; }

/* Line 365 — year number cells */
.compliance-grid td.yr-col { background: none !important; color: #a0b0d0; font-size: 11px; font-weight: 500; text-align: left; padding-right: 4px; vertical-align: middle; }
```

Changes: `#8899c8` → `#b0bee0`, `#8090b0` → `#a0b0d0`, `#7080a0` → `#a0b0d0`. These are brighter versions of the same hue family, improving contrast from ~3.5:1 to ~6:1 against the dark backgrounds.

- [ ] **Step 2: Verify visually**

Run: `source .venv/bin/activate && uvicorn api.main:app --port 8000 --reload`

Open http://localhost:8000, select any country, check the compliance grid tab. Verify form names and year labels are clearly readable in dark mode. Switch to light mode and verify the light overrides still look correct (light mode has separate selectors that override these values).

- [ ] **Step 3: Commit**

```bash
git add dashboard/static/style.css
git commit -m "fix: improve dark mode contrast for compliance grid text

Brighten form header, year label, and legend colors in the compliance
grid for better readability on Windows displays with different font
rendering. Addresses ISU feedback about illegible text on Edge/Windows.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Light mode 0% tag contrast in global table

In the "All Countries — Submission Overview" table, the A1 rate tag for 0% countries is nearly invisible in light mode. The `.gt-rate` class uses `color: #fff` (white text), but `choroColor(0)` returns `#f5f5f5` (near-white background). White on near-white = invisible.

**Files:**
- Modify: `dashboard/static/app.js:1829-1831` (global table rate badge rendering)

- [ ] **Step 1: Add dark text for low-rate badges**

In `dashboard/static/app.js`, find the rate badge rendering inside `renderGlobalTable()` at line 1829:

```javascript
        const rate = c.a1_rate != null
            ? `<span class="gt-rate" style="background:${choroColor(c.a1_rate)}">${Math.round(c.a1_rate * 100)}%</span>`
            : `<span class="gt-rate gt-rate-none">—</span>`;
```

Replace with:

```javascript
        const rate = c.a1_rate != null
            ? `<span class="gt-rate" style="background:${choroColor(c.a1_rate)};${c.a1_rate <= 0.2 ? 'color:#555' : ''}">${Math.round(c.a1_rate * 100)}%</span>`
            : `<span class="gt-rate gt-rate-none">—</span>`;
```

This adds `color:#555` (dark gray text) when the A1 rate is 0.2 or below, where the choropleth colors are too light for white text to be readable.

- [ ] **Step 2: Verify visually**

Open http://localhost:8000, click "All countries" button, find a country with 0% A1 rate. Verify the "0%" tag text is clearly visible in both light and dark mode. In dark mode, `#555` on `#f5f5f5` is fine because the badge background is still light.

- [ ] **Step 3: Commit**

```bash
git add dashboard/static/app.js
git commit -m "fix: use dark text for low-rate A1 badges in global table

When A1 rate <= 20%, the choropleth background color is too light for
white text. Switch to dark gray (#555) text for these badges.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Pathogen bar alignment — uniform bar start position

The pathogen frequency bars in the Trends modal start at different horizontal positions because the pathogen label width varies. The label element has `width: 190px` but the bars start immediately after the text content rather than after the fixed-width box. The issue is actually a **missing closing `>` on the `.pathogen-row` div** — the `<span>` becomes part of the div's tag rather than content, breaking the flex layout.

**Files:**
- Modify: `dashboard/static/app.js:1932` (pathogen row HTML template)

- [ ] **Step 1: Fix the missing closing angle bracket**

In `dashboard/static/app.js`, find line 1932 in `renderPathogenChart()`:

```javascript
            return `<div class="pathogen-row" data-action="apply-organism-filter" data-term="${esc(d.term)}"
                <span class="pathogen-label">${esc(d.label)}</span>
```

The `<div>` tag is missing its closing `>` before `<span>`. Replace with:

```javascript
            return `<div class="pathogen-row" data-action="apply-organism-filter" data-term="${esc(d.term)}">
                <span class="pathogen-label">${esc(d.label)}</span>
```

Adding the `>` after the closing quote of `data-term` ensures the div is properly closed and the flex layout works as intended. Each `.pathogen-label` has `width: 190px; flex-shrink: 0` which should give uniform bar starting positions — but only when the HTML structure is valid.

- [ ] **Step 2: Verify visually**

Open http://localhost:8000, click "Trends", switch to "Pathogens" tab. Verify all bars now start at the same horizontal position regardless of label length. "Rickettsia" and "Foot-and-mouth" with count=7 should have identical bar widths.

- [ ] **Step 3: Commit**

```bash
git add dashboard/static/app.js
git commit -m "fix: add missing closing bracket on pathogen row div

The pathogen-row div was missing its closing '>' which broke the flex
layout, causing bars to start at different horizontal positions.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Change global table from document count to year count

Canada shows "30 submissions" because it submits in English and French (2 documents per year × 15 years). Change the API to count distinct years instead of distinct documents.

**Files:**
- Modify: `api/main.py:347-358` (`/api/countries` SQL query)
- Modify: `api/main.py:830-843` (`/api/map/compliance` SQL query)

- [ ] **Step 1: Update `/api/countries` to count distinct years**

In `api/main.py`, find the `/api/countries` query at line 347:

```python
        cur.execute("""
            SELECT
                d.country_iso3,
                MAX(d.country_name)                                                          AS country_name,
                COUNT(DISTINCT d.id)                                                         AS submission_count,
                MAX(d.year)                                                                  AS latest_year,
                COUNT(DISTINCT fy.canonical_facility_id)                                     AS facility_count,
                COUNT(DISTINCT CASE WHEN fy.has_bsl4 THEN fy.canonical_facility_id END)     AS bsl4_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.id END)::numeric
                    / NULLIF(COUNT(DISTINCT d.id), 0), 3
                )                                                                            AS a1_rate
            FROM documents d
            LEFT JOIN facility_years fy  ON fy.document_id = d.id
            LEFT JOIN form_compliance fc ON fc.document_id = d.id AND fc.form = 'A1'
            WHERE NOT d.is_amendment
            GROUP BY d.country_iso3
            ORDER BY MAX(d.country_name)
        """)
```

Replace with:

```python
        cur.execute("""
            SELECT
                d.country_iso3,
                MAX(d.country_name)                                                          AS country_name,
                COUNT(DISTINCT d.year)                                                       AS submission_count,
                MAX(d.year)                                                                  AS latest_year,
                COUNT(DISTINCT fy.canonical_facility_id)                                     AS facility_count,
                COUNT(DISTINCT CASE WHEN fy.has_bsl4 THEN fy.canonical_facility_id END)     AS bsl4_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.year END)::numeric
                    / NULLIF(COUNT(DISTINCT d.year), 0), 3
                )                                                                            AS a1_rate
            FROM documents d
            LEFT JOIN facility_years fy  ON fy.document_id = d.id
            LEFT JOIN form_compliance fc ON fc.document_id = d.id AND fc.form = 'A1'
            WHERE NOT d.is_amendment
            GROUP BY d.country_iso3
            ORDER BY MAX(d.country_name)
        """)
```

Three changes: `COUNT(DISTINCT d.id)` → `COUNT(DISTINCT d.year)` in all three places (submission_count, a1_rate numerator, a1_rate denominator). This counts unique years instead of unique documents, so bilingual countries like Canada show 15 instead of 30.

- [ ] **Step 2: Update `/api/map/compliance` to count distinct years**

In `api/main.py`, find the `/api/map/compliance` query at line 830:

```python
        cur.execute("""
            SELECT
                d.country_iso3,
                MAX(d.country_name)  AS country_name,
                COUNT(DISTINCT d.id) AS submission_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.id END)::numeric
                    / NULLIF(COUNT(DISTINCT d.id), 0), 3
                )                    AS a1_rate
            FROM documents d
            LEFT JOIN form_compliance fc ON fc.document_id = d.id AND fc.form = 'A1'
            WHERE NOT d.is_amendment
            GROUP BY d.country_iso3
        """)
```

Replace with:

```python
        cur.execute("""
            SELECT
                d.country_iso3,
                MAX(d.country_name)  AS country_name,
                COUNT(DISTINCT d.year) AS submission_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.year END)::numeric
                    / NULLIF(COUNT(DISTINCT d.year), 0), 3
                )                    AS a1_rate
            FROM documents d
            LEFT JOIN form_compliance fc ON fc.document_id = d.id AND fc.form = 'A1'
            WHERE NOT d.is_amendment
            GROUP BY d.country_iso3
        """)
```

Same pattern: `d.id` → `d.year` in all three COUNT expressions.

- [ ] **Step 3: Also update `/api/map/compliance/{form}`**

In `api/main.py`, find the `/api/map/compliance/{form}` query at line 1015:

```python
            SELECT
                d.country_iso3,
                MAX(d.country_name)  AS country_name,
                COUNT(DISTINCT d.id) AS submission_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.id END)::numeric
                    / NULLIF(COUNT(DISTINCT d.id), 0), 3
                )                    AS rate
```

Replace with:

```python
            SELECT
                d.country_iso3,
                MAX(d.country_name)  AS country_name,
                COUNT(DISTINCT d.year) AS submission_count,
                ROUND(
                    COUNT(DISTINCT CASE WHEN fc.status = 'substantive' THEN d.year END)::numeric
                    / NULLIF(COUNT(DISTINCT d.year), 0), 3
                )                    AS rate
```

- [ ] **Step 4: Run tests**

Run: `source .venv/bin/activate && pytest tests/test_api.py -v -k "countries or compliance" --no-header`

Verify all tests pass. If any assertion checks for specific submission counts, they may need updating to reflect year-based counting.

- [ ] **Step 5: Commit**

```bash
git add api/main.py
git commit -m "fix: count distinct years instead of documents for submission counts

Bilingual countries (e.g. Canada submits in EN+FR) had inflated counts
because each language version was a separate document. Now counts
distinct years, matching the comparison modal's existing behavior.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: About modal — add transparency index explanation and submission count clarification

Add explanations for: (1) how the transparency index is calculated, (2) what "submissions" means (distinct years, not documents), and why some countries have surprisingly high counts.

**Files:**
- Modify: `dashboard/index.html:296-328` (About modal body)

- [ ] **Step 1: Add transparency index and submission count sections**

In `dashboard/index.html`, find line 303 (just before the "Data extraction" heading). Insert these two new sections between the "Compliance grid" section and the "Data extraction" section:

After line 302 (`</ul>` closing the compliance grid bullet list), insert:

```html

                <h6 class="about-heading">Submission counts</h6>
                <p>The <strong>"Submissions"</strong> column in the All Countries table and the comparison view counts
                the number of <strong>distinct years</strong> in which a country filed at least one CBM document.
                Countries that submit in multiple languages (e.g. Canada submits in both English and French)
                are counted once per year, not once per document.</p>
                <p>The UN portal holds public CBM records from 1988 onwards, but coverage varies: most countries'
                earliest available submissions date from 2011 (when the revised template was adopted).
                Historical records before 2011 are available for some countries.</p>

                <h6 class="about-heading">Transparency index</h6>
                <p>The transparency score (0–100) shown per country is a weighted composite of three factors:</p>
                <ul>
                    <li><strong>Regularity (40%)</strong> — ratio of years with submissions to total years since first submission.
                    A country that has submitted every year since its first CBM scores 1.0; one that skips years scores lower.</li>
                    <li><strong>Substantive A1 rate (40%)</strong> — share of submissions that contain substantive Form A1
                    research facility declarations (as opposed to "nothing to declare" or absent).</li>
                    <li><strong>Recency (20%)</strong> — 1.0 if the country submitted within the last 3 years, 0.5 if 4–6 years ago,
                    0.1 otherwise.</li>
                </ul>
                <p>This distinguishes procedural participation (submitting "nothing to declare" every year) from
                substantive transparency (detailed facility declarations with consistent reporting).</p>
```

- [ ] **Step 2: Verify visually**

Open http://localhost:8000, click "About". Scroll down and verify the new sections appear between the compliance grid explanation and the data extraction section.

- [ ] **Step 3: Commit**

```bash
git add dashboard/index.html
git commit -m "docs: add transparency index and submission count explanations to About modal

Explains weighted formula (regularity 40%, A1 rate 40%, recency 20%)
and clarifies that submissions count distinct years not documents.
Addresses ISU feedback requesting these clarifications.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Comparison mode — tooltips on empty dots, info icon, and A2/G counts

Three sub-changes:
1. Add tooltip on empty (absent) dots explaining what they mean
2. Add info tooltip on "Total CBMs filed" label clarifying what it counts
3. Add Defence (A2) and Vaccine (G) facility counts to the comparison

**Files:**
- Modify: `dashboard/static/app.js:2860-2946` (comparison rendering)

- [ ] **Step 1: Enhance mini grid dot tooltips**

In `dashboard/static/app.js`, find the `miniGrid` function at line 2906:

```javascript
    const miniGrid = (byYear) => {
        const sortedYears = Object.keys(byYear).map(Number).sort((a, b) => a - b);
        return `<div class="cmp-compliance-mini">${sortedYears.map(yr => {
            const a1 = byYear[yr]['A1'];
            const cls = a1 === 'substantive' ? 'td-sub' : a1 === 'nothing_to_declare' ? 'td-ntd' : 'td-abs';
            return `<span class="cmp-yr-dot ${cls}" title="${yr}: ${a1 || 'absent'}"></span>`;
        }).join('')}</div>`;
    };
```

Replace with:

```javascript
    const miniGrid = (byYear, countryName) => {
        const sortedYears = Object.keys(byYear).map(Number).sort((a, b) => a - b);
        return `<div class="cmp-compliance-mini">${sortedYears.map(yr => {
            const a1 = byYear[yr]['A1'];
            const cls = a1 === 'substantive' ? 'td-sub' : a1 === 'nothing_to_declare' ? 'td-ntd' : 'td-abs';
            const tip = a1 === 'substantive' ? `${countryName} submitted Form A1 in ${yr}`
                      : a1 === 'nothing_to_declare' ? `${countryName} declared nothing for Form A1 in ${yr}`
                      : `${countryName} did not submit Form A1 in ${yr}`;
            return `<span class="cmp-yr-dot ${cls}" title="${tip}"></span>`;
        }).join('')}</div>`;
    };
```

- [ ] **Step 2: Update miniGrid calls to pass country name**

In the same `renderComparison` function, find line 2928:

```javascript
            ${miniGrid(years(d))}
```

Replace both occurrences (inside the `col` template) with:

```javascript
            ${miniGrid(years(d), d.country_name)}
```

- [ ] **Step 3: Add info icon to "Total CBMs filed" label and fetch A2/G counts**

Replace the `renderComparison` function body from the `onCompareSelect` call through the comparison rendering. In `dashboard/static/app.js`, find the `onCompareSelect` function at line 2860:

```javascript
async function onCompareSelect() {
    const iso3a = document.getElementById('cmp-country-a')?.value;
    const iso3b = document.getElementById('cmp-country-b')?.value;
    if (!iso3a || !iso3b) return;
    const body = document.getElementById('cmp-body');
    body.innerHTML = '<div class="text-center text-muted py-4">Loading…</div>';
    try {
        const [dataA, dataB] = await Promise.all([
            api(`/api/country/${iso3a}`),
            api(`/api/country/${iso3b}`),
        ]);
        body.innerHTML = renderComparison(dataA, dataB);
    } catch (e) {
        body.innerHTML = `<div class="text-danger">Failed to load: ${esc(e.message)}</div>`;
    }
}
```

Replace with:

```javascript
async function onCompareSelect() {
    const iso3a = document.getElementById('cmp-country-a')?.value;
    const iso3b = document.getElementById('cmp-country-b')?.value;
    if (!iso3a || !iso3b) return;
    const body = document.getElementById('cmp-body');
    body.innerHTML = '<div class="text-center text-muted py-4">Loading…</div>';
    try {
        const [dataA, dataB, defA, defB, vacA, vacB] = await Promise.all([
            api(`/api/country/${iso3a}`),
            api(`/api/country/${iso3b}`),
            api(`/api/country/${iso3a}/defence`).catch(() => ({ entities: [] })),
            api(`/api/country/${iso3b}/defence`).catch(() => ({ entities: [] })),
            api(`/api/country/${iso3a}/vaccine`).catch(() => ({ entities: [] })),
            api(`/api/country/${iso3b}/vaccine`).catch(() => ({ entities: [] })),
        ]);
        dataA._defCount = defA.entities.length;
        dataA._vacCount = vacA.entities.length;
        dataB._defCount = defB.entities.length;
        dataB._vacCount = vacB.entities.length;
        body.innerHTML = renderComparison(dataA, dataB);
    } catch (e) {
        body.innerHTML = `<div class="text-danger">Failed to load: ${esc(e.message)}</div>`;
    }
}
```

- [ ] **Step 4: Update the `col` template in renderComparison to show A2/G and info icon**

In the `renderComparison` function, find the `col` template at line 2920:

```javascript
    const col = (d, ts, subCount) => `
        <div>
            <div class="cmp-col-head">${esc(d.country_name)}
                ${ts != null ? `<span style="font-size:12px;font-weight:400;margin-left:6px;color:#8090b0">${transparencyBadge(ts)}</span>` : ''}
            </div>

            <div class="cmp-section-label">SUBMISSIONS</div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">Total CBMs filed</span><span class="cmp-stat-val">${subCount}</span></div>
            ${miniGrid(years(d))}

            <div class="cmp-section-label">RESEARCH FACILITIES (A1)</div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">Unique facilities</span>
                <span class="cmp-stat-val ${d.facilities.length > 0 ? 'highlight' : ''}">${d.facilities.length}</span></div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">BSL-4</span>
                <span class="cmp-stat-val" style="color:#c0392b">${bsl4Count(d) || '—'}</span></div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">BSL-3</span>
                <span class="cmp-stat-val" style="color:#e67e22">${bsl3Count(d) || '—'}</span></div>

            ${organisms(d) ? `<div class="cmp-section-label">DECLARED ORGANISMS (SAMPLE)</div>
                <div class="cmp-organisms">${esc(organisms(d))}</div>` : ''}
        </div>`;
```

Replace with:

```javascript
    const col = (d, ts, subCount) => `
        <div>
            <div class="cmp-col-head">${esc(d.country_name)}
                ${ts != null ? `<span style="font-size:12px;font-weight:400;margin-left:6px;color:#8090b0">${transparencyBadge(ts)}</span>` : ''}
            </div>

            <div class="cmp-section-label">SUBMISSIONS</div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">Years with submissions <span title="Number of distinct years in which this country filed at least one CBM document (2011 revised-template era onwards for most countries)" style="cursor:help;opacity:0.6">&#9432;</span></span><span class="cmp-stat-val">${subCount}</span></div>
            ${miniGrid(years(d), d.country_name)}

            <div class="cmp-section-label">DECLARED FACILITIES</div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">Research (A1)</span>
                <span class="cmp-stat-val ${d.facilities.length > 0 ? 'highlight' : ''}">${d.facilities.length}</span></div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">Defence (A2)</span>
                <span class="cmp-stat-val">${d._defCount || '—'}</span></div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">Vaccine (G)</span>
                <span class="cmp-stat-val">${d._vacCount || '—'}</span></div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">BSL-4</span>
                <span class="cmp-stat-val" style="color:#c0392b">${bsl4Count(d) || '—'}</span></div>
            <div class="cmp-stat-row"><span class="cmp-stat-key">BSL-3</span>
                <span class="cmp-stat-val" style="color:#e67e22">${bsl3Count(d) || '—'}</span></div>

            ${organisms(d) ? `<div class="cmp-section-label">DECLARED ORGANISMS (SAMPLE)</div>
                <div class="cmp-organisms">${esc(organisms(d))}</div>` : ''}
        </div>`;
```

Key changes:
- "Total CBMs filed" → "Years with submissions" with `ⓘ` hover tooltip
- "RESEARCH FACILITIES (A1)" section renamed to "DECLARED FACILITIES"
- Added Defence (A2) and Vaccine (G) facility counts
- miniGrid calls now pass `d.country_name`

- [ ] **Step 5: Update export CSV to include A2/G**

In the `exportComparison` function at line 2948, find:

```javascript
        ['Research facilities', dataA.facility_count, dataB.facility_count],
        ['BSL-4 facilities', dataA.bsl4_count || 0, dataB.bsl4_count || 0],
```

This function uses `_countriesData` (which doesn't have A2/G counts), so we cannot easily add those here without extra API calls. Leave the CSV export as-is for now — it already exports A1 facility counts.

- [ ] **Step 6: Verify visually**

Open http://localhost:8000, click "Compare", select Australia and Belgium. Verify:
1. Hover over the dark dots in the mini grid — tooltip should say "Australia did not submit Form A1 in 2014" (or similar)
2. The submissions label says "Years with submissions" with an ⓘ icon
3. Hovering ⓘ shows explanatory tooltip
4. Defence (A2) and Vaccine (G) counts appear in the comparison

- [ ] **Step 7: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat: enhance comparison mode with tooltips, info icon, and A2/G counts

- Mini grid dots now show descriptive tooltips explaining submission status
- 'Total CBMs filed' renamed to 'Years with submissions' with info tooltip
- Added Defence (A2) and Vaccine (G) facility counts to comparison
- Section renamed from 'RESEARCH FACILITIES (A1)' to 'DECLARED FACILITIES'

Addresses ISU feedback on comparison clarity and completeness.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Rename "Compliance" to "Submission" in all user-facing text

Replace all user-visible instances of "compliance" with appropriate alternatives. Internal variable names, DB table names, and API endpoints remain unchanged.

**Files:**
- Modify: `dashboard/index.html:68,79-89,296,379-384` (tab labels, legend, About modal, global table modal)
- Modify: `dashboard/static/app.js:1206-1244,1228,1782,2926` (JS-generated text)

- [ ] **Step 1: Rename the sidebar tab**

In `dashboard/index.html`, find line 68:

```html
                <button class="dtab active" id="tab-compliance-btn" data-action="switch-tab" data-tab="compliance" role="tab" aria-selected="true" aria-controls="tab-compliance">Compliance</button>
```

Replace with:

```html
                <button class="dtab active" id="tab-compliance-btn" data-action="switch-tab" data-tab="compliance" role="tab" aria-selected="true" aria-controls="tab-compliance">Submissions</button>
```

Only change the visible text "Compliance" → "Submissions". Keep the `data-tab="compliance"` and `id="tab-compliance-btn"` internal identifiers unchanged.

- [ ] **Step 2: Rename the About modal heading**

In `dashboard/index.html`, find line 296:

```html
                <h6 class="about-heading">Compliance grid</h6>
```

Replace with:

```html
                <h6 class="about-heading">Submission grid</h6>
```

- [ ] **Step 3: Rename the global table modal title**

In `dashboard/index.html`, find line 384:

```html
                <h5 class="modal-title" id="global-table-modal-title">All Countries — Submission Overview</h5>
```

This already says "Submission Overview" — no change needed here.

- [ ] **Step 4: Rename JS-generated compliance text**

In `dashboard/static/app.js`, find the empty compliance message at line 1211:

```javascript
        el.innerHTML = '<div style="color:#8090b8;font-size:12px">No compliance data</div>';
```

Replace with:

```javascript
        el.innerHTML = '<div style="color:#8090b8;font-size:12px">No submission data</div>';
```

Find the table aria-label at line 1228:

```javascript
        `<table role="grid" aria-label="Compliance grid: form submission status by year">` +
```

Replace with:

```javascript
        `<table role="grid" aria-label="Submission grid: form submission status by year">` +
```

- [ ] **Step 5: Rename the global table section comment (cosmetic)**

In `dashboard/static/app.js`, find the section comment at line 1782:

```javascript
// ── Global compliance table ─────────────────────────────────────────────────
```

Replace with:

```javascript
// ── Global submission table ─────────────────────────────────────────────────
```

- [ ] **Step 6: Rename API endpoint summaries (user-visible in docs mode)**

In `api/main.py`, find line 827:

```python
@app.get("/api/map/compliance", summary="Per-country Form A1 submission rates (for choropleth)")
```

Replace with:

```python
@app.get("/api/map/compliance", summary="Per-country Form A1 submission rates for choropleth")
```

Find line 1003:

```python
@app.get("/api/map/compliance/{form}", summary="Per-country compliance rate for a given form")
```

Replace with:

```python
@app.get("/api/map/compliance/{form}", summary="Per-country submission rate for a given form")
```

Find line 372:

```python
@app.get("/api/country/{iso3}", summary="Compliance history and facility list for one country")
```

Replace with:

```python
@app.get("/api/country/{iso3}", summary="Submission history and facility list for one country")
```

- [ ] **Step 7: Run tests**

Run: `source .venv/bin/activate && pytest tests/test_api.py -v --no-header`

All tests should pass — no API behavior was changed, only summary text and UI labels.

- [ ] **Step 8: Commit**

```bash
git add dashboard/index.html dashboard/static/app.js api/main.py
git commit -m "refactor: rename user-facing 'compliance' to 'submission'

'Compliance' is a sensitive term for NAM members. All user-visible
labels changed to 'submission(s)' while keeping internal identifiers
(DB table, API paths, CSS classes, JS variables) unchanged.

Addresses ISU feedback on terminology sensitivity.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/test_api.py -v --no-header`

All tests must pass.

- [ ] **Step 2: Visual spot-check**

Start the server and verify:
1. Dark mode: compliance grid text is clearly readable
2. Light mode: 0% A1 rate badge text is visible
3. Pathogen bars all start at the same horizontal position
4. About modal has new transparency index and submission count sections
5. Comparison mode shows A2/G counts, descriptive dot tooltips, info icon
6. "Compliance" tab now says "Submissions"
7. Canada shows ~15 submissions, not 30

- [ ] **Step 3: Commit if any final adjustments were needed**
