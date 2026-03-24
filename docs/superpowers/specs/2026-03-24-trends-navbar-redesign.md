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
| Centre | Search bar + 🤖 robot toggle | Search bar only (AI always on) |
| Right | 🚩 Review · ☀ Theme · About | 🚩 Review · **📈 Trends** · About |

The Trends button uses a visually distinct accent style (blue tint background, blue border, blue text) to draw the eye relative to the plain About button.

### AI search toggle removal

The `#navbar-search-mode` button (robot icon) is removed from the HTML. The search mode is hard-coded to AI (`naturalLanguageMode = true`). The `toggle-search-mode` action and all mode-switching logic in `app.js` are removed. The search input placeholder is updated to always read `"Ask anything, e.g. 'BSL-4 labs in Europe'"`.

### Theme toggle relocation

The `#navbar-theme` button (sun/moon icon) is removed from the navbar. A Dark / Light segmented control is added to the footer of the existing About modal (`#about-modal`). Clicking either segment calls the existing `setTheme()` function and updates active styling on the segment buttons. The active segment reflects the current theme on open.

### Trends button

A new `<button id="navbar-trends">📈 Trends</button>` is added to the navbar in the position previously occupied by the theme toggle (between the Review queue button and About). It fires `data-action="show-trends"`, which calls the existing `showTrends()` function — no change to the modal itself.

## Files changed

| File | Change |
|------|--------|
| `dashboard/index.html` | Remove robot button; remove theme button; add Trends button; add theme control to About modal |
| `dashboard/static/app.js` | Remove `naturalLanguageMode` toggle logic; hard-code AI mode; update placeholder; wire Trends button action |
| `dashboard/static/style.css` | Add `#navbar-trends` accent style; remove any styles scoped to removed elements |

## Acceptance criteria

1. Navbar contains exactly: title, search bar, (conditional) review badge, Trends button, About button.
2. No robot icon anywhere in the UI.
3. No theme button in the navbar; theme control present and functional in About modal.
4. Clicking Trends opens the existing Trends modal.
5. AI search works as before; mode is permanently on.
6. Existing tests pass.
