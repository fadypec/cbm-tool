# Trends Navbar Redesign

**Date:** 2026-03-24
**Status:** Approved

## Problem

The Trends feature — which shows global submission patterns, pathogen data, year-on-year changes, and BSL-4 capacity — is currently buried as a small button inside the filter panel. New users have no reason to open the filter panel just to find it. Meanwhile, the navbar is cluttered with two low-value icons: the robot toggle (AI search mode) and the sun/moon theme toggle.

## Goals

1. Make Trends discoverable from any state without opening any panel.
2. Remove the AI search toggle — AI mode is always on.
3. Demote the theme toggle to a less prominent location.
4. Keep the navbar clean and uncluttered.

## Out of scope

- Changes to the Trends modal itself (tabs, charts, data).
- Changes to the filter panel, sidebar, or map.
- Mobile-specific layout changes beyond those that follow naturally from the navbar change.

## Design

### Navbar (before → after)

| Position | Before | After |
|----------|--------|-------|
| Left | CBM Lens title | CBM Lens title (unchanged) |
| Centre | Search bar + 🤖 robot toggle inside it | Search bar only (AI always on) |
| Right (left-to-right) | 🚩 Review (conditional) · ☀ Theme · About | 🚩 Review (conditional) · 📈 Trends · About |

The Review queue button (`#navbar-review`) is conditionally visible — hidden when the flagged-record count is zero, shown otherwise. This behaviour is unchanged.

The Trends button uses a visually distinct accent style (blue-tint background, blue border, blue text) to draw the eye relative to the plain About button.

### AI search toggle removal

**In `index.html`:** Remove `#search-mode-btn` (the robot icon button inside the search bar). Hard-code the `ai-mode` class and placeholder `"Ask anything, e.g. 'BSL-4 labs in Europe'"` directly on `#search-input` in the HTML.

**In `app.js`:**
- Change `let _searchMode = 'ai'` to `const _searchMode = 'ai'`.
- Remove `toggleSearchMode()` entirely.
- Remove `_syncSearchModeVisuals()` entirely.
- In the `input` event listener on `#search-input`: remove the `if (_searchMode === 'ai') return;` guard and the entire normal-mode autocomplete block that follows it (dead code).
- In the `keydown` event listener on `#search-input`: remove the `if (_searchMode === 'ai')` wrapper — keep the Enter-handler body unchanged, just unindent it.
- Remove the `toggle-search-mode` case from the event dispatch switch.

No other parts of the code reference `_searchMode`.

### Theme toggle relocation

**In `index.html`:**
- Remove `#theme-toggle` (the `☀` button in the navbar).
- The `#about-modal` has `modal-header` and `modal-body` but no `modal-footer`. Add a `<div class="modal-footer about-theme-footer">` after `modal-body` containing:
  - A label: `<span class="about-theme-label">Theme</span>`
  - A `<div role="radiogroup" aria-label="Theme" class="theme-seg">` containing:
    - `<button id="theme-btn-dark" role="radio" aria-checked="true" class="theme-seg-btn">🌙 Dark</button>`
    - `<button id="theme-btn-light" role="radio" aria-checked="false" class="theme-seg-btn">☀ Light</button>`
  - Note: `modal-dialog-scrollable` makes only `modal-body` scroll; `modal-footer` stays pinned — this is intentional and desirable.

**In `app.js`:**
- Remove `_syncThemeBtn()` entirely.
- In `initTheme()`: remove the `_syncThemeBtn(theme)` call (keep the rest of `initTheme()` — it reads `localStorage` and applies the theme on load).
- Extract a new `applyTheme(theme)` function from `toggleTheme()`. It takes `'dark'|'light'` and: sets `document.documentElement.dataset.theme`, saves to `localStorage`, and calls `_swapTile(theme)`. This preserves the Leaflet tile-layer swap that `toggleTheme` currently performs.
- Remove `toggleTheme()` (replaced by `applyTheme`) and the `toggle-theme` dispatch case.
- Add click event listeners on `#theme-btn-dark` and `#theme-btn-light` (wired at `DOMContentLoaded`). Each listener calls `applyTheme('dark'|'light')` and sets `aria-checked="true"` on the clicked button and `"false"` on the other.
- Add a one-time `show.bs.modal` listener on `#about-modal` that syncs `aria-checked` on both buttons to match `document.documentElement.dataset.theme` (defaulting to `'dark'` if unset). This is the only sync point.

**In `style.css`:**
- Delete the `#theme-toggle`, `#theme-toggle:hover`, and `#theme-toggle:focus-visible` rules (three rules; there is no `[data-theme="light"] #theme-toggle` variant).
- Delete `#search-mode-btn` rules and variants.
- Add `#navbar-trends` to the existing grouped `focus-visible` selector block (where `#navbar-about` and `#navbar-review` already appear) rather than writing a standalone rule.
- Add standalone styles for `#navbar-trends`: blue-tint background (`#1e3a5f`), blue border (`#2a5a8f`), blue text (`#4a9de8`), same border-radius and padding as `#navbar-about`. Add hover state. Light-theme variant follows the same pattern as the existing `[data-theme="light"] #navbar-about`.
- Add styles for the theme segmented control: `.theme-seg` as a flex row with a small gap; `.theme-seg-btn` as small pill buttons with muted border and grey text when `aria-checked="false"`, and blue border + blue text + dark-tint background when `aria-checked="true"`.

### Trends button

**In `index.html`:** Add `<button id="navbar-trends" data-action="show-trends">📈 Trends</button>` in the right-hand group, between `#navbar-review` and `#navbar-about`.

The `show-trends` dispatch case already exists and calls `showTrends()` — no change to `app.js` needed.

### Filter panel Trends button

The existing `📈 Trends` button in the filter panel action buttons is **retained**. The navbar button is an additional entry point, not a replacement. Users who have the filter panel open still have Trends one click away from there.

### Tests

`tests/test_api.py` contains only API-level tests and does not reference any DOM elements. No changes required.

## Files changed

| File | Change |
|------|--------|
| `dashboard/index.html` | Remove `#search-mode-btn`; remove `#theme-toggle`; add `#navbar-trends`; add `modal-footer` with theme segmented control to `#about-modal`; hard-code `ai-mode` class and placeholder on `#search-input` |
| `dashboard/static/app.js` | `_searchMode` → `const`; remove `toggleSearchMode`, `_syncSearchModeVisuals`, `toggleTheme`, `_syncThemeBtn`; remove call in `initTheme`; remove dead autocomplete path; remove dispatch cases; add theme segment listeners and modal-open sync |
| `dashboard/static/style.css` | Delete `#theme-toggle` and `#search-mode-btn` rule sets; add `#navbar-trends` to focus-visible group and add its own styles; add theme segmented control styles |

## Acceptance criteria

1. Navbar contains exactly: title, search-form (single unit), (conditional) review button, Trends button, About button — in that left-to-right order.
2. No robot icon / `#search-mode-btn` anywhere in the UI.
3. No theme button in the navbar; theme segmented control present and functional in the About modal footer; active segment matches current theme when modal opens.
4. Clicking Trends (navbar or filter panel) opens the existing Trends modal.
5. Search input always has class `ai-mode` and placeholder `"Ask anything, e.g. 'BSL-4 labs in Europe'"` and never changes at runtime.
6. `_searchMode` is `const 'ai'`; `toggleSearchMode`, `_syncSearchModeVisuals`, `toggleTheme`, and `_syncThemeBtn` do not exist; `applyTheme(theme)` exists and correctly sets `dataset.theme`, `localStorage`, and calls `_swapTile`.
7. Style rules targeting `#theme-toggle` and `#search-mode-btn` no longer appear in `style.css`.
8. `tests/test_api.py` passes without modification.
