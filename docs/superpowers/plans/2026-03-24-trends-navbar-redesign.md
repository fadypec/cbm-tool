# Trends Navbar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Trends to the navbar, remove the AI search toggle (AI always on), and relocate the theme toggle into the About modal.

**Architecture:** Three self-contained tasks touch one file each. Task 1 cleans up HTML. Task 2 removes dead JS and wires new behaviour. Task 3 updates styles. Each task leaves the app in a working state.

**Tech Stack:** Plain HTML/JS/CSS (no build step). Bootstrap 5 modal events. Leaflet tile-layer swap preserved via `applyTheme()`.

**Spec:** `docs/superpowers/specs/2026-03-24-trends-navbar-redesign.md`

---

### Task 1: Update `dashboard/index.html`

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 1: Remove `#search-mode-btn` and hard-code AI mode on the input**

In `#search-wrap` (around lines 31–34), replace the `<input>` and the robot `<button>`:

```html
<!-- BEFORE (lines 31–34): -->
<input id="search-input" type="text" placeholder="Search facilities, organisms…" autocomplete="off"
       aria-label="Search facilities and organisms" role="combobox" aria-expanded="false" aria-controls="search-results">
<button id="search-mode-btn" data-action="toggle-search-mode" title="Switch to AI natural language search" aria-label="Toggle AI search mode">🤖</button>
<ul id="search-results" role="listbox"></ul>

<!-- AFTER: -->
<input id="search-input" type="text" placeholder="Ask anything, e.g. 'BSL-4 labs in Europe'" autocomplete="off"
       aria-label="Search facilities and organisms" role="combobox" aria-expanded="false" aria-controls="search-results"
       class="ai-mode">
<ul id="search-results" role="listbox"></ul>
```

- [ ] **Step 2: Remove `#theme-toggle` from navbar and add `#navbar-trends`**

Replace the two navbar buttons (around lines 40–41):

```html
<!-- BEFORE: -->
<button id="theme-toggle" data-action="toggle-theme" aria-label="Switch to light mode" aria-pressed="false" title="Switch to light mode">☀</button>
<button id="navbar-about" data-bs-toggle="modal" data-bs-target="#about-modal">About</button>

<!-- AFTER: -->
<button id="navbar-trends" data-action="show-trends">📈 Trends</button>
<button id="navbar-about" data-bs-toggle="modal" data-bs-target="#about-modal">About</button>
```

- [ ] **Step 3: Add theme segmented control to About modal footer**

The `#about-modal` `modal-content` div ends around line 332. After the `</div>` that closes `modal-body` (line 331) and before the `</div>` that closes `modal-content` (line 332), insert:

```html
            <div class="modal-footer about-theme-footer">
                <span class="about-theme-label">Theme</span>
                <div role="radiogroup" aria-label="Theme" class="theme-seg">
                    <button id="theme-btn-dark" role="radio" aria-checked="true" class="theme-seg-btn">🌙 Dark</button>
                    <button id="theme-btn-light" role="radio" aria-checked="false" class="theme-seg-btn">☀ Light</button>
                </div>
            </div>
```

- [ ] **Step 4: Verify visually**

```bash
source .venv/bin/activate
uvicorn api.main:app --port 8000 --reload
```

Open http://localhost:8000. Confirm:
- Navbar: `CBM Lens | [search bar] | 📈 Trends | About` — no robot, no sun/moon
- About modal footer has "Theme 🌙 Dark ☀ Light" (not yet functional — that's Task 2)

- [ ] **Step 5: Commit**

```bash
git add dashboard/index.html
git commit -m "feat(navbar): add Trends button, remove robot and theme toggles from navbar"
```

---

### Task 2: Update `dashboard/static/app.js`

**Files:**
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: Convert `_searchMode` to `const`; delete `_syncSearchModeVisuals` and `toggleSearchMode`**

Around line 62, change:
```js
// BEFORE:
let _searchMode   = 'ai'; // 'normal' | 'ai'
// AFTER:
const _searchMode = 'ai';
```

Delete the entire `_syncSearchModeVisuals()` function (lines 1727–1735):
```js
// DELETE this whole function:
function _syncSearchModeVisuals(btn, input) {
    if (_searchMode === 'ai') {
        if (btn) { btn.classList.add('ai-active'); btn.title = 'Switch back to facility search'; }
        if (input) { input.placeholder = 'Ask anything, e.g. "BSL-4 labs in Eastern Europe"…'; input.classList.add('ai-mode'); }
    } else {
        if (btn) { btn.classList.remove('ai-active'); btn.title = 'Switch to AI natural language search'; }
        if (input) { input.placeholder = 'Search facilities, organisms…'; input.classList.remove('ai-mode'); }
    }
}
```

Delete the entire `toggleSearchMode()` function (lines 1737–1748):
```js
// DELETE this whole function:
function toggleSearchMode() {
    _searchMode = _searchMode === 'normal' ? 'ai' : 'normal';
    const btn    = document.getElementById('search-mode-btn');
    const input  = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    _syncSearchModeVisuals(btn, input);
    if (_searchMode === 'ai') {
        results.classList.remove('open');
        input.classList.remove('searching');
    }
    input.focus();
}
```

- [ ] **Step 2: Clean up `initSearch()` — remove dead code**

**2a. Replace the `input` event listener** (lines 1669–1676). The entire body was only for normal-mode autocomplete — delete the whole listener:

```js
// DELETE this entire listener:
input.addEventListener('input', () => {
    if (_searchMode === 'ai') return;  // AI mode: wait for Enter
    clearTimeout(searchTimer);
    const q = input.value.trim();
    if (q.length < 2) { results.classList.remove('open'); input.classList.remove('searching'); return; }
    input.classList.add('searching');
    searchTimer = setTimeout(() => doSearch(q), 300);
});
```

**2b. Rewrite the `keydown` listener** (lines 1678–1709). Remove the outer `if (_searchMode === 'ai')` wrapper and its `return;`. **Keep** the arrow-key navigation block — it handles keyboard navigation of the results dropdown, which remains in the DOM. Add a `return;` after the Enter-AI block to prevent it falling through into the arrow-key `else if (e.key === 'Enter')` handler:

```js
// REPLACE the entire keydown listener with:
input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
        e.preventDefault();
        const q = input.value.trim();
        if (q.length >= 3) {
            results.classList.remove('open');
            showAIQuery(q);
        }
        return;  // prevent falling through to the dropdown Enter handler below
    }

    if (!results.classList.contains('open')) return;
    const items = [...results.querySelectorAll('li[data-idx]')];
    const active = results.querySelector('li.sr-active');
    const idx = active ? items.indexOf(active) : -1;

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        active?.classList.remove('sr-active');
        (items[idx + 1] || items[0])?.classList.add('sr-active');
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        active?.classList.remove('sr-active');
        (items[idx - 1] || items[items.length - 1])?.classList.add('sr-active');
    } else if (e.key === 'Enter') {
        e.preventDefault();
        (active || items[0])?.click();
    }
});
```

**2c.** Delete the last line of `initSearch()`:
```js
// DELETE this line:
_syncSearchModeVisuals(document.getElementById('search-mode-btn'), input);
```

- [ ] **Step 3: Replace theme functions with `applyTheme()`**

Delete `_syncThemeBtn()` entirely (lines 207–215):
```js
// DELETE this whole function:
function _syncThemeBtn(theme) {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const isLight = theme === 'light';
    btn.textContent = isLight ? '🌙' : '☀';
    btn.title = isLight ? 'Switch to dark mode' : 'Switch to light mode';
    btn.setAttribute('aria-pressed', String(isLight));
}
```

In `initTheme()`, delete the `_syncThemeBtn(theme)` call on line 204. Keep everything else — `initTheme()` correctly sets `dataset.theme` which `initMap()` then reads to initialise the tile layer. No replacement call is needed; `applyTheme()` is only for user-triggered theme changes after the map is already initialised:

```js
// BEFORE:
    document.documentElement.dataset.theme = theme;
    _syncThemeBtn(theme);
}
// AFTER:
    document.documentElement.dataset.theme = theme;
}
```

Delete `toggleTheme()` entirely (lines 217–224):
```js
// DELETE this whole function:
function toggleTheme() {
    const current = document.documentElement.dataset.theme || 'dark';
    const next    = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('cbm-theme', next); } catch (_) {}
    _syncThemeBtn(next);
    _swapTile(next);
}
```

Add `applyTheme()` immediately after `initTheme()`:
```js
function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('cbm-theme', theme); } catch (_) {}
    _swapTile(theme);
}
```

- [ ] **Step 4: Remove dead dispatch cases; wire theme segments**

In the event dispatch switch, delete the `toggle-search-mode` case:
```js
// DELETE:
case 'toggle-search-mode':
    toggleSearchMode();
    break;
```

Delete the `toggle-theme` case:
```js
// DELETE:
case 'toggle-theme':
    toggleTheme();
    break;
```

In the `DOMContentLoaded` async handler (around line 77), after the existing `initTheme()` and `initMap()` calls, add:

```js
// Theme segmented control in About modal
const themeDark  = document.getElementById('theme-btn-dark');
const themeLight = document.getElementById('theme-btn-light');
if (themeDark && themeLight) {
    themeDark.addEventListener('click', () => {
        applyTheme('dark');
        themeDark.setAttribute('aria-checked', 'true');
        themeLight.setAttribute('aria-checked', 'false');
    });
    themeLight.addEventListener('click', () => {
        applyTheme('light');
        themeLight.setAttribute('aria-checked', 'true');
        themeDark.setAttribute('aria-checked', 'false');
    });
    document.getElementById('about-modal').addEventListener('show.bs.modal', () => {
        const current = document.documentElement.dataset.theme || 'dark';
        themeDark.setAttribute('aria-checked', String(current === 'dark'));
        themeLight.setAttribute('aria-checked', String(current === 'light'));
    });
}
```

- [ ] **Step 5: Verify in browser**

Open http://localhost:8000:
- Search: placeholder reads `"Ask anything, e.g. 'BSL-4 labs in Europe'"`; no robot button; typing and pressing Enter triggers AI search
- Click 📈 Trends in the navbar → Trends modal opens
- Open About → footer shows theme buttons; clicking 🌙 Dark / ☀ Light switches theme and map tiles swap; reopening About shows the correct segment active

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_api.py -v
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat(navbar): hard-code AI mode, replace toggleTheme with applyTheme, wire theme segments"
```

---

### Task 3: Update `dashboard/static/style.css`

**Files:**
- Modify: `dashboard/static/style.css`

- [ ] **Step 1: Delete `#search-mode-btn` rules**

Find and delete these three rules (around lines 140–157):
```css
#search-mode-btn { ... }
#search-mode-btn:hover { color: #c0a0f0; border-color: #6040a0; }
#search-mode-btn.ai-active { background: #221840; border-color: #8060c0; color: #c0a0f0; }
```

- [ ] **Step 2: Delete `#theme-toggle` rules and their section comment**

Find and delete the section comment and three rules (around lines 1478–1494):
```css
/* ── Theme toggle button (lives in navbar — always dark-styled) ─────────── */
/* ══════════════════════════════════════════════════════════════════════════ */

#theme-toggle { ... }
#theme-toggle:hover { color: #c0ccf0; border-color: #4a4a6a; }
#theme-toggle:focus-visible { outline: 2px solid #4a8ad4; outline-offset: 2px; }
```

Note: `#theme-toggle:focus-visible` exists as a standalone rule here (not in the grouped selector), so deleting it here is sufficient.

- [ ] **Step 3: Add `#navbar-trends` to the grouped `focus-visible` selector**

The grouped selector around line 1449 already includes `button:focus-visible` at the top (which technically covers `#navbar-trends`), but the project convention adds specific navbar button IDs to the group for clarity. Add `#navbar-trends:focus-visible,` before `#navbar-about:focus-visible,`:

```css
/* BEFORE: */
#navbar-about:focus-visible,
#navbar-review:focus-visible,

/* AFTER: */
#navbar-trends:focus-visible,
#navbar-about:focus-visible,
#navbar-review:focus-visible,
```

- [ ] **Step 4: Add `#navbar-trends` accent styles**

After the `#navbar-about:hover` rule (around line 108), insert:

```css
/* ── Navbar Trends button ───────────────────────────────────────────────── */
#navbar-trends {
    flex-shrink: 0;
    background: #1e3a5f;
    border: 1px solid #2a5a8f;
    border-radius: 5px;
    color: #4a9de8;
    padding: 4px 10px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
}
#navbar-trends:hover { background: #254878; border-color: #4a8ad4; color: #7ab8f5; }
```

And in the light-theme section (where `[data-theme="light"] #navbar-about` appears), add:
```css
[data-theme="light"] #navbar-trends {
    background: #ddeeff;
    border-color: #5590cc;
    color: #1a5a9a;
}
[data-theme="light"] #navbar-trends:hover { background: #c8e0f8; border-color: #2470b0; color: #0a4a8a; }
```

- [ ] **Step 5: Add theme segmented control styles**

After the About modal styles (or at the end of the About section), add:

```css
/* ── About modal theme segmented control ───────────────────────────────── */
.about-theme-footer {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    border-top: 1px solid #252535;
    background: #0e1020;
}
.about-theme-label {
    font-size: 12px;
    color: #8090b8;
    font-weight: 600;
}
.theme-seg {
    display: flex;
    gap: 4px;
}
.theme-seg-btn {
    background: none;
    border: 1px solid #2a2a3a;
    border-radius: 4px;
    color: #8090b8;
    font-size: 12px;
    padding: 3px 10px;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.theme-seg-btn[aria-checked="true"] {
    background: #1e3a5f;
    border-color: #2a5a8f;
    color: #4a9de8;
}
.theme-seg-btn:hover { border-color: #4a4a6a; color: #c0ccf0; }

[data-theme="light"] .about-theme-footer {
    background: #f0f2f8;
    border-top-color: #d0d8ee;
}
[data-theme="light"] .about-theme-label { color: #6070a0; }
[data-theme="light"] .theme-seg-btn { border-color: #c8d2ee; color: #6070a0; }
[data-theme="light"] .theme-seg-btn[aria-checked="true"] {
    background: #ddeeff;
    border-color: #5590cc;
    color: #1a5a9a;
}
```

- [ ] **Step 6: Final visual check**

Hard-refresh http://localhost:8000:
- 📈 Trends button has blue accent appearance, visually distinct from About
- About modal footer shows styled theme buttons; active segment highlighted; light-theme works correctly

- [ ] **Step 7: Run tests and commit**

```bash
pytest tests/test_api.py -v
```

```bash
git add dashboard/static/style.css
git commit -m "feat(navbar): style Trends button and About modal theme control"
```

---

### Final verification

- [ ] Check all 8 acceptance criteria from the spec
- [ ] `git push` — Railway auto-deploys
