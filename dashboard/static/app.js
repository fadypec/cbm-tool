'use strict';

// ── Constants ──────────────────────────────────────────────────────────────

// Countries whose CBM data is submitted to the ISU but not publicly available
const RESTRICTED = new Set(['CHN', 'FRA', 'RUS', 'IND']);
// This GeoJSON dataset gives France ISO code '-99'; match by name as fallback
const RESTRICTED_NAMES = new Set(['France']);

// BWC membership status — fetched from /api/bwc-membership on startup.
// Maps ISO3 → 'restricted' | 'signatory' | 'non_party'; absence = full BWC state party.
let bwcMembership = {};

// Choropleth layer styles — shared between loadChoropleth() and updateChoropleth()
const CHORO_STYLE_RESTRICTED  = { fillColor: '#5c3370', fillOpacity: 0.65, weight: 0.5, color: '#999',    opacity: 0.6 };
const CHORO_STYLE_CBM         = (fillColor) => ({ fillColor, fillOpacity: 0.7,  weight: 0.5, color: '#aaa',    opacity: 0.6 });
const CHORO_STYLE_NON_PARTY   = { fillColor: 'url(#country-hatch)', fillOpacity: 1,    weight: 0.5, color: '#bbb',    opacity: 0.5 };
const CHORO_STYLE_SIGNATORY   = { fillColor: '#d4b870', fillOpacity: 0.55, weight: 0.6, color: '#b09040', opacity: 0.6 };
const CHORO_STYLE_NO_DATA     = { fillColor: '#b8bdd0', fillOpacity: 0.55, weight: 0.5, color: '#8890a0', opacity: 0.6 };

// Map tile URLs for light / dark themes
const TILE_URLS = {
    light: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    dark:  'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
};
const TILE_ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' +
    ' contributors &copy; <a href="https://carto.com/">CARTO</a>';

let _tileLayer = null;

// ── State ──────────────────────────────────────────────────────────────────

const STATE = {
    layers:     { A1: true, A2: true, G: true },
    bsl:        { 'BSL-4': true, 'BSL-3': true, 'BSL-2': true, 'BSL-1': true, unknown: true },
    year:       null,   // null = all years
    hideLow:    false,
    aiFilterIds: null,  // Set of canonical_facility_ids from AI query (null = no filter)
    // Lapsed declarations: show facilities that haven't appeared in recent CBMs
    showLapsed:       false,
    lapsedThreshold:  3,   // years before country's latest submission counts as lapsed
};

// Raw GeoJSON data keyed by layer
const DATA = { A1: null, A2: null, G: null };

// Latest submission year per country per layer (for default deduplication)
const LATEST_YEAR = {};

// Leaflet MarkerClusterGroup per layer
const CLUSTERS = {};

let map;
let choroLayer    = null;
let complianceRates = {};   // iso3 → {a1_rate, submission_count}
let entityModal   = null;
// FEATURE 4: which form the choropleth is currently showing
let choroForm     = 'A1';
let filterCollapsed = false;
let searchTimer   = null;
let _searchMode   = 'ai'; // 'normal' | 'ai'
let _countriesData  = [];   // full /api/countries response, for global table
let _playInterval   = null; // year animation interval
let _hashTimer      = null; // debounce for history.replaceState
let _tableSort      = { col: 'submission_count', dir: 'desc' };

// Lapsed declarations: max year seen per facility id (across all DATA.A1 records)
const LATEST_FACILITY_YEAR = {};  // canonical_facility_id → max year in dataset
// Set of facility ids currently classified as "lapsed" — recomputed in applyFilters()
let _lapsedIds = new Set();
// Transparency scores fetched from /api/countries/transparency
let _transparencyMap = {};  // iso3 → transparency_score

// ── Bootstrap ──────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    initTheme();
    initMap();
    initClusters();
    entityModal = new bootstrap.Modal(document.getElementById('entity-modal'));
    initSearch();
    initStaticListeners();
    initEventDelegation();
    restoreFromHash();

    try {
        const [stats, countries, a1, a2, vaccines, compliance, transparency, membershipResp] = await Promise.all([
            api('/api/stats'),
            api('/api/countries'),
            api('/api/map/facilities'),
            api('/api/map/defence'),
            api('/api/map/vaccines'),
            api('/api/map/compliance'),
            // Transparency index loaded in parallel; non-critical so errors are swallowed below
            api('/api/countries/transparency').catch(() => []),
            // BWC membership — must resolve before loadChoropleth(); errors degrade gracefully
            api('/api/bwc-membership').catch(() => ({})),
        ]);
        if (membershipResp && membershipResp.membership) {
            bwcMembership = membershipResp.membership;
        }

        renderStats(stats);
        initYearSlider(stats.year_min, stats.year_max);

        compliance.forEach(c => { complianceRates[c.country_iso3] = c; });

        // Build transparency lookup for use in country list and global table
        transparency.forEach(t => { _transparencyMap[t.country_iso3] = t.transparency_score; });

        _countriesData = countries;
        renderCountryList(countries);

        DATA.A1 = a1;
        DATA.A2 = a2;
        DATA.G  = vaccines;

        computeLatestYears();
        computeLatestFacilityYears();  // for lapsed declarations feature
        applyFilters();
        addLegend();
        loadChoropleth();

        // Review queue badge: non-critical, fire-and-forget
        refreshReviewBadge().catch(() => {});
    } catch (e) {
        console.error('Init failed:', e);
        document.getElementById('country-list').innerHTML =
            '<div class="side-placeholder" style="color:#c0392b">Failed to load — is the API running?</div>';
    }

    // Prevent map interaction through the filter panel
    const fp = document.getElementById('filter-panel');
    L.DomEvent.disableClickPropagation(fp);
    L.DomEvent.disableScrollPropagation(fp);

    // Mobile: start with sidebar and filter panel collapsed
    if (window.innerWidth < 768) {
        const sb  = document.getElementById('sidebar');
        const tab = document.getElementById('sidebar-tab');
        sb.classList.add('collapsed');
        tab.textContent = '▶';
        tab.title = 'Expand sidebar';
        if (!filterCollapsed) toggleFilterPanel();
    }
});

// ── API ────────────────────────────────────────────────────────────────────

async function api(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
    return r.json();
}

// ── Stats bar ──────────────────────────────────────────────────────────────

let _stats = null; // kept so updateStatsBar() can re-render after layer toggles

function renderStats(s) {
    _stats = s;
    document.getElementById('stats-bar').innerHTML =
        `<span id="stat-a1">${s.total_unique_facilities.toLocaleString()} research</span>` +
        `<span id="stat-a1-sep">&nbsp;·&nbsp;</span>` +
        `<span id="stat-a2">${(s.total_unique_defence || 0).toLocaleString()} defence</span>` +
        `<span id="stat-a2-sep">&nbsp;·&nbsp;</span>` +
        `<span id="stat-g">${(s.total_unique_vaccine || 0).toLocaleString()} vaccine</span>` +
        `<span id="stat-g-sep">&nbsp;·&nbsp;</span>` +
        `${s.total_countries} countries`;
    // populate About modal dynamic counts
    const subEl = document.getElementById('about-sub-count');
    const cntEl = document.getElementById('about-country-count');
    if (subEl) subEl.textContent = s.total_submissions;
    if (cntEl) cntEl.textContent = s.total_countries;
    updateStatsBar();
}

// Show/hide per-layer counts in the stats bar to match the active layer checkboxes.
function updateStatsBar() {
    if (!_stats) return;
    [['A1','a1'], ['A2','a2'], ['G','g']].forEach(([layer, key]) => {
        const el  = document.getElementById(`stat-${key}`);
        const sep = document.getElementById(`stat-${key}-sep`);
        const show = STATE.layers[layer];
        if (el)  el.style.display  = show ? '' : 'none';
        if (sep) sep.style.display = show ? '' : 'none';
    });
}

// ── Map ────────────────────────────────────────────────────────────────────

// ── Theme ──────────────────────────────────────────────────────────────────

function initTheme() {
    // theme-init.js already applied the saved theme before CSS rendered.
    // Read localStorage again as a fallback in case that script was blocked.
    let theme = document.documentElement.dataset.theme || 'dark';
    try {
        const saved = localStorage.getItem('cbm-theme');
        if (saved === 'light' || saved === 'dark') theme = saved;
    } catch (_) {}
    document.documentElement.dataset.theme = theme;
    _syncThemeBtn(theme);
}

function _syncThemeBtn(theme) {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const isLight = theme === 'light';
    btn.textContent = isLight ? '☾' : '☀';
    btn.title       = isLight ? 'Switch to dark mode'  : 'Switch to light mode';
    btn.setAttribute('aria-label',   isLight ? 'Switch to dark mode'  : 'Switch to light mode');
    btn.setAttribute('aria-pressed', String(isLight));
}

function toggleTheme() {
    const current = document.documentElement.dataset.theme || 'dark';
    const next    = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('cbm-theme', next); } catch (_) {}
    _syncThemeBtn(next);
    _swapTile(next);
}

function _swapTile(theme) {
    if (!map || !_tileLayer) return;
    map.removeLayer(_tileLayer);
    _tileLayer = L.tileLayer(TILE_URLS[theme], {
        attribution: TILE_ATTRIBUTION,
        maxZoom: 19,
        noWrap:  true,
    }).addTo(map);
    _tileLayer.bringToBack();
}

// ── Map ────────────────────────────────────────────────────────────────────

function initMap() {
    map = L.map('map', {
        zoomControl: false,
        minZoom: 2,
        maxBounds: [[-90, -180], [90, 180]],
        maxBoundsViscosity: 1.0,
    }).setView([20, 0], 2);
    const theme = document.documentElement.dataset.theme || 'dark';
    _tileLayer = L.tileLayer(TILE_URLS[theme], {
        attribution: TILE_ATTRIBUTION,
        maxZoom: 19,
        noWrap: true,
    }).addTo(map);
    L.control.zoom({ position: 'topright' }).addTo(map);
}

function initClusters() {
    for (const layer of ['A1', 'A2', 'G']) {
        CLUSTERS[layer] = L.markerClusterGroup({
            showCoverageOnHover: false,
            maxClusterRadius: 40,
            chunkedLoading: true,
        });
        map.addLayer(CLUSTERS[layer]);
    }
}

// ── Colour helpers ─────────────────────────────────────────────────────────

function bslColor(containment) {
    if (!containment) return '#95a5a6';
    const u = containment.toUpperCase();
    if (u.includes('4')) return '#c0392b';
    if (u.includes('3')) return '#e67e22';
    if (u.includes('2')) return '#f39c12';
    if (u.includes('1')) return '#27ae60';
    return '#95a5a6';
}

function normalizeBsl(containment) {
    if (!containment) return 'unknown';
    const u = containment.toUpperCase();
    if (u.includes('4')) return 'BSL-4';
    if (u.includes('3')) return 'BSL-3';
    if (u.includes('2')) return 'BSL-2';
    if (u.includes('1')) return 'BSL-1';
    return 'unknown';
}

function markerOptions(layer, feature) {
    const p = feature.properties;
    // bubblingMouseEvents: false ensures a marker click never falls through to the
    // choropleth country layer underneath, preventing accidental country navigation.
    const base = { color: '#fff', weight: 1.5, opacity: 1, fillOpacity: 0.85, bubblingMouseEvents: false };

    // Lapsed facility: muted, dashed border, distinct from active markers
    if (layer === 'A1' && STATE.showLapsed && _lapsedIds.has(p.id)) {
        return { ...base, radius: 7, fillColor: '#999999', fillOpacity: 0.6,
                 color: '#bbb', weight: 1.5, dashArray: '3,3' };
    }

    if (layer === 'A1') return { ...base, radius: 8, fillColor: bslColor(p.containment) };
    if (layer === 'A2') return { ...base, radius: 7, fillColor: '#8b1a1a' };
    return                       { ...base, radius: 7, fillColor: '#0a7a6a' };
}

// ── Filter logic ───────────────────────────────────────────────────────────

function computeLatestYears() {
    for (const layer of ['A1', 'A2', 'G']) {
        if (!DATA[layer]) continue;
        const m = {};
        for (const f of DATA[layer].features) {
            const { country_iso3, year } = f.properties;
            if (!m[country_iso3] || year > m[country_iso3]) m[country_iso3] = year;
        }
        LATEST_YEAR[layer] = m;
    }
}

// Pre-compute latest year seen per canonical_facility_id across all A1 records.
// Used by the lapsed declarations view to find a facility's last known appearance.
function computeLatestFacilityYears() {
    if (!DATA.A1) return;
    for (const f of DATA.A1.features) {
        const id   = f.properties.id;
        const year = f.properties.year;
        if (!LATEST_FACILITY_YEAR[id] || year > LATEST_FACILITY_YEAR[id]) {
            LATEST_FACILITY_YEAR[id] = year;
        }
    }
}

// Return a Set of facility ids that are "lapsed": their last known declaration was
// STATE.lapsedThreshold or more years before the country's most recent submission.
function computeLapsedIds() {
    const lapsed = new Set();
    if (!DATA.A1) return lapsed;
    const facilityIds = new Set(DATA.A1.features.map(f => f.properties.id));
    facilityIds.forEach(fid => {
        const facilityLastYear = LATEST_FACILITY_YEAR[fid];
        if (!facilityLastYear) return;
        // Find the country for this facility (any feature with this id)
        const sample = DATA.A1.features.find(f => f.properties.id === fid);
        if (!sample) return;
        const countryIso3     = sample.properties.country_iso3;
        const countryLastYear = LATEST_YEAR.A1?.[countryIso3] ?? facilityLastYear;
        if ((countryLastYear - facilityLastYear) >= STATE.lapsedThreshold) {
            lapsed.add(fid);
        }
    });
    return lapsed;
}

// ── Lapsed filter UI handlers ───────────────────────────────────────────────

function onLapsedToggle() {
    STATE.showLapsed = document.getElementById('lapsed-toggle').checked;
    const row = document.getElementById('lapsed-threshold-row');
    if (row) row.style.display = STATE.showLapsed ? 'block' : 'none';
    // Lapsed mode ignores the year filter (always shows latest per facility)
    if (STATE.showLapsed && STATE.year !== null) {
        // Force to "all years" mode
        const allYears = document.getElementById('all-years');
        if (allYears) allYears.checked = true;
        onAllYearsToggle();
        return; // onAllYearsToggle already calls applyFilters
    }
    applyFilters();
}

function onLapsedThresholdChange(val) {
    const n = parseInt(val);
    if (!isNaN(n) && n >= 1) {
        STATE.lapsedThreshold = n;
        applyFilters();
    }
}

function matchesFilter(layer, feature) {
    const p = feature.properties;

    if (layer === 'A1' && STATE.showLapsed && STATE.year === null) {
        // Lapsed mode: show only the latest record per facility (not per country)
        // so each facility gets exactly one marker, styled lapsed if it went dark
        const facilityLatest = LATEST_FACILITY_YEAR[p.id];
        if (facilityLatest && p.year !== facilityLatest) return false;
    } else if (STATE.year !== null) {
        if (p.year !== STATE.year) return false;
    } else {
        // Default: show only each country's latest submission year
        const latest = LATEST_YEAR[layer]?.[p.country_iso3];
        if (latest && p.year !== latest) return false;
    }

    if (STATE.hideLow && p.geocode_conf === 'low') return false;
    if (layer === 'A1') {
        if (!STATE.bsl[normalizeBsl(p.containment)]) return false;
        if (STATE.aiFilterIds && !STATE.aiFilterIds.has(p.id)) return false;
    }
    return true;
}

function applyFilters() {
    // Recompute lapsed set before any layer is processed
    if (STATE.showLapsed && DATA.A1) {
        _lapsedIds = computeLapsedIds();
    } else {
        _lapsedIds = new Set();
    }

    for (const layer of ['A1', 'A2', 'G']) {
        CLUSTERS[layer].clearLayers();
        if (!STATE.layers[layer] || !DATA[layer]) continue;

        const visible = DATA[layer].features.filter(f => matchesFilter(layer, f));
        if (visible.length === 0) continue;

        const leafletLayers = L.geoJSON(
            { type: 'FeatureCollection', features: visible },
            {
                pointToLayer: (f, ll) => L.circleMarker(ll, markerOptions(layer, f)),
                onEachFeature: (f, lyr) => {
                    lyr.bindPopup(buildPopup(layer, f), { maxWidth: 280 });
                    // Hover tooltip: brief summary before committing to a click
                    lyr.bindTooltip(buildHoverTooltip(layer, f), {
                        permanent:   false,
                        direction:   'top',
                        sticky:      false,
                        offset:      [0, -6],
                        opacity:     1,
                        className:   'marker-tooltip',
                    });
                },
            }
        ).getLayers();

        CLUSTERS[layer].addLayers(leafletLayers);
    }

    updateActiveFiltersBar();
    updateHash();
}

// Build a compact hover tooltip (shown on desktop mouse-over before click)
function buildHoverTooltip(layer, feature) {
    const p = feature.properties;
    const name = p.name || '[Unnamed facility]';
    const yearStr = p.year ? ` · ${p.year}` : '';
    if (layer === 'A1') {
        const bsl = p.containment || 'Unknown containment';
        const color = bslColor(p.containment);
        return `<strong>${esc(name)}</strong>` +
               `<div style="color:${color};font-size:11px">${esc(bsl)}${yearStr}</div>`;
    }
    if (layer === 'A2') {
        return `<strong>${esc(name)}</strong>` +
               `<div style="color:#c06060;font-size:11px">Defence facility${yearStr}</div>`;
    }
    return `<strong>${esc(name)}</strong>` +
           `<div style="color:#60c0b0;font-size:11px">Vaccine facility${yearStr}</div>`;
}

function buildPopup(layer, feature) {
    const p = feature.properties;
    const loc = [p.city, p.country_name].filter(Boolean).join(', ');
    const layerLabel = layer === 'A1' ? 'Research' : layer === 'A2' ? 'Defence' : 'Vaccine';
    const color = layer === 'A1' ? bslColor(p.containment) : layer === 'A2' ? '#8b1a1a' : '#0a7a6a';
    const badge = layer === 'A1'
        ? `<span style="display:inline-block;padding:1px 7px;border-radius:4px;background:${color};color:#fff;font-size:11px">${esc(p.containment || 'Unknown')}</span>`
        : `<span style="display:inline-block;padding:1px 7px;border-radius:4px;background:${color};color:#fff;font-size:11px">${layerLabel} facility</span>`;

    const historyLink = layer === 'A1'
        ? `<br><a class="popup-link" href="#" data-action="show-entity" data-entity-id="${esc(p.id)}">Full history →</a>`
        : layer === 'G'
        ? `<br><a class="popup-link" href="#" data-action="show-vaccine-entity" data-entity-id="${esc(p.id)}">Full history →</a>`
        : '';

    // FEATURE 2: agents preview line in popup (A1 only)
    const agentsLine = (layer === 'A1' && p.agents_summary)
        ? `<div style="color:#6a8070;font-size:11px;margin-top:3px">${esc(p.agents_summary.slice(0, 80))}${p.agents_summary.length > 80 ? '…' : ''}</div>`
        : '';

    return `<div class="fac-popup">
        <strong>${esc(p.name || 'Unnamed facility')}</strong>
        <div class="popup-loc">${esc(loc)}</div>
        ${badge} <small style="color:#888;margin-left:4px">declared ${p.year}</small>
        ${agentsLine}
        ${historyLink}
    </div>`;
}

// ── Filter panel UI ────────────────────────────────────────────────────────

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const tab     = document.getElementById('sidebar-tab');
    const overlay = document.getElementById('mobile-overlay');
    const collapsed = sidebar.classList.toggle('collapsed');
    tab.textContent = collapsed ? '▶' : '◀';
    tab.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    if (overlay) overlay.classList.toggle('show', !collapsed);
    setTimeout(() => map && map.invalidateSize(), 260);
}

// On mobile, open the sidebar if it is currently collapsed (e.g. after tapping a map marker).
function ensureSidebarOpen() {
    if (window.innerWidth < 768 && document.getElementById('sidebar').classList.contains('collapsed')) {
        toggleSidebar();
    }
}

function toggleFilterPanel() {
    filterCollapsed = !filterCollapsed;
    document.getElementById('fp-body').style.display = filterCollapsed ? 'none' : '';
    document.getElementById('fp-collapse').textContent = filterCollapsed ? '+' : '−';
}

function onFilterChange() {
    // Read layer checkboxes
    for (const layer of ['A1', 'A2', 'G']) {
        STATE.layers[layer] = document.getElementById(`layer-${layer}`).checked;
    }

    // Read BSL checkboxes
    document.querySelectorAll('input[name="bsl"]').forEach(cb => {
        STATE.bsl[cb.value] = cb.checked;
    });

    // Show warning when BSL filter is partial and other layers are visible
    const bslAllChecked = Object.values(STATE.bsl).every(Boolean);
    const otherLayersVisible = STATE.layers.A2 || STATE.layers.G;
    const warn = document.getElementById('bsl-warning');
    if (warn) warn.style.display = (!bslAllChecked && otherLayersVisible) ? 'block' : 'none';

    // Read geocode confidence
    STATE.hideLow = document.getElementById('hide-low').checked;

    applyFilters();
    updateStatsBar();
    updateActiveFiltersBar();
}

// Called from the pathogen chart — redirects to AI search instead of organism filter.
function applyOrganismFilter(term) {
    if (!term) return;
    showAIQuery('facilities working with ' + term);
}

// ── AI filter ───────────────────────────────────────────────────────────────

function clearAIFilter() {
    STATE.aiFilterIds = null;
    updateActiveFilterChips();
    applyFilters();
}

function clearAllFilters() {
    STATE.aiFilterIds = null;
    STATE.hideLow = false;
    STATE.showLapsed = false;
    STATE.year = null;
    STATE.bsl    = { 'BSL-4': true, 'BSL-3': true, 'BSL-2': true, 'BSL-1': true, unknown: true };
    STATE.layers = { A1: true, A2: true, G: true };
    // Sync layer checkboxes
    ['A1', 'A2', 'G'].forEach(l => {
        const cb = document.getElementById('layer-' + l);
        if (cb) cb.checked = true;
    });
    // Sync BSL checkboxes
    document.querySelectorAll('input[name="bsl"]').forEach(cb => { cb.checked = true; });
    const allYears = document.getElementById('all-years');
    if (allYears) allYears.checked = true;
    const yearSlider = document.getElementById('year-slider');
    if (yearSlider) { yearSlider.setAttribute('disabled', ''); }
    const yearInput = document.getElementById('year-input');
    if (yearInput) { yearInput.setAttribute('disabled', ''); }
    const playBtn = document.getElementById('year-play');
    if (playBtn) { playBtn.setAttribute('disabled', ''); stopYearPlay(); }
    const hideLowCb = document.getElementById('hide-low');
    if (hideLowCb) hideLowCb.checked = false;
    const lapsedToggle = document.getElementById('lapsed-toggle');
    if (lapsedToggle) lapsedToggle.checked = false;
    const lapsedRow = document.getElementById('lapsed-threshold-row');
    if (lapsedRow) lapsedRow.style.display = 'none';
    const bslWarn = document.getElementById('bsl-warning');
    if (bslWarn) bslWarn.style.display = 'none';
    updateActiveFilterChips(); // updates legacy chip area + calls updateActiveFiltersBar()
    applyFilters();
}

function updateActiveFilterChips() {
    // Legacy AI filter chip inside filter panel body (kept for backward compat)
    const el = document.getElementById('active-filter-chips');
    if (!el) return;
    if (STATE.aiFilterIds) {
        const n = STATE.aiFilterIds.size;
        el.innerHTML =
            `<span class="filter-chip">🤖 AI filter: ${n} facilit${n !== 1 ? 'ies' : 'y'} ` +
            `<button class="filter-chip-clear" data-action="clear-ai-filter" title="Clear AI filter">×</button></span>`;
        el.style.display = 'block';
    } else {
        el.innerHTML = '';
        el.style.display = 'none';
    }
    // Also update the comprehensive active filters bar
    updateActiveFiltersBar();
}

function onAllYearsToggle() {
    const allChecked = document.getElementById('all-years').checked;
    const slider   = document.getElementById('year-slider');
    const numInput = document.getElementById('year-input');
    const playBtn  = document.getElementById('year-play');
    if (allChecked) {
        stopYearPlay();
        slider.setAttribute('disabled', '');
        numInput.setAttribute('disabled', '');
        if (playBtn) playBtn.setAttribute('disabled', '');
        STATE.year = null;
    } else {
        slider.removeAttribute('disabled');
        numInput.removeAttribute('disabled');
        if (playBtn) playBtn.removeAttribute('disabled');
        STATE.year = parseInt(slider.value);
        numInput.value = STATE.year;
    }
    applyFilters();
}

// ── Year animation ──────────────────────────────────────────────────────────

function getDataYears() {
    // Return sorted list of years that have at least one visible-layer feature
    const years = new Set();
    for (const layer of ['A1', 'A2', 'G']) {
        if (!DATA[layer]) continue;
        for (const f of DATA[layer].features) years.add(f.properties.year);
    }
    return [...years].sort((a, b) => a - b);
}

let _playYears = [];
let _playIdx   = 0;

function toggleYearPlay() {
    if (_playInterval) stopYearPlay();
    else startYearPlay();
}

function startYearPlay() {
    // Ensure specific-year mode is active
    const allYears = document.getElementById('all-years');
    if (allYears && allYears.checked) {
        allYears.checked = false;
        onAllYearsToggle();
    }
    const slider = document.getElementById('year-slider');
    const btn    = document.getElementById('year-play');
    if (!slider) return;

    // Build the list of years that actually have data
    _playYears = getDataYears();
    if (_playYears.length === 0) return;

    // Start from the beginning if at (or past) the last data year
    const current = parseInt(slider.value);
    const lastDataYear = _playYears[_playYears.length - 1];
    if (current >= lastDataYear) {
        _playIdx = 0;
    } else {
        // Resume from nearest data year >= current
        _playIdx = _playYears.findIndex(y => y >= current);
        if (_playIdx < 0) _playIdx = 0;
    }

    // Jump immediately to starting year
    _setPlayYear(_playYears[_playIdx]);

    btn.textContent = '⏸';
    btn.title = 'Pause animation';
    btn.classList.add('playing');

    _playInterval = setInterval(() => {
        _playIdx++;
        if (_playIdx >= _playYears.length) { stopYearPlay(); return; }
        _setPlayYear(_playYears[_playIdx]);
    }, 900);
}

function _setPlayYear(year) {
    const slider = document.getElementById('year-slider');
    const numInput = document.getElementById('year-input');
    slider.value   = year;
    numInput.value = year;
    STATE.year     = year;
    applyFilters();
}

function stopYearPlay() {
    clearInterval(_playInterval);
    _playInterval = null;
    const btn = document.getElementById('year-play');
    if (btn) { btn.textContent = '▶'; btn.title = 'Animate through years'; btn.classList.remove('playing'); }
}

let _yearInputTimer = null;

// Called when the number input is typed into
function onYearInput(val) {
    const y = parseInt(val);
    if (isNaN(y)) return;
    STATE.year = y;
    const slider = document.getElementById('year-slider');
    if (y >= parseInt(slider.min) && y <= parseInt(slider.max)) slider.value = y;
    clearTimeout(_yearInputTimer);
    _yearInputTimer = setTimeout(applyFilters, 400);
}

// Called when the range slider is dragged
function onYearSlider(val) {
    const y = parseInt(val);
    STATE.year = y;
    document.getElementById('year-input').value = y;
}

function initYearSlider(minYear, maxYear) {
    const slider   = document.getElementById('year-slider');
    const numInput = document.getElementById('year-input');
    slider.min   = minYear;   slider.max   = maxYear;
    numInput.min = minYear;   numInput.max = maxYear;
    document.getElementById('yr-min').textContent = minYear;
    document.getElementById('yr-max').textContent = maxYear;
    // Only set value when not already in a specific-year mode (from restoreFromHash)
    if (STATE.year === null) {
        slider.value   = maxYear;
        numInput.value = maxYear;
    }
    // Ensure disabled state matches checkbox
    const allChecked = document.getElementById('all-years').checked;
    const playBtn = document.getElementById('year-play');
    if (allChecked) {
        slider.setAttribute('disabled', '');
        numInput.setAttribute('disabled', '');
        if (playBtn) playBtn.setAttribute('disabled', '');
    } else {
        slider.removeAttribute('disabled');
        numInput.removeAttribute('disabled');
        if (playBtn) playBtn.removeAttribute('disabled');
    }
}

// ── CSV Export ─────────────────────────────────────────────────────────────

function exportCSV() {
    // FEATURE 3: added agents_summary column; filename encodes active layers
    const header = ['layer', 'id', 'name', 'country_iso3', 'country_name', 'year',
                    'containment', 'city', 'geocode_conf', 'lat', 'lon', 'agents_summary'];
    const rows = [header];

    const activeLayers = [];
    for (const layer of ['A1', 'A2', 'G']) {
        if (!STATE.layers[layer] || !DATA[layer]) continue;
        activeLayers.push(layer);
        DATA[layer].features
            .filter(f => matchesFilter(layer, f))
            .forEach(f => {
                const p = f.properties;
                const [lon, lat] = f.geometry.coordinates;
                rows.push([
                    layer,
                    p.id, p.name, p.country_iso3, p.country_name, p.year,
                    p.containment || '', p.city || '', p.geocode_conf || '',
                    lat.toFixed(5), lon.toFixed(5),
                    p.agents_summary || '',   // FEATURE 3: agents column
                ]);
            });
    }

    const csv = rows.map(r =>
        r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')
    ).join('\n');

    // FEATURE 3: filename encodes active layers (e.g. cbm-A1-G-2023.csv)
    const layerStr = activeLayers.join('-');
    const yearStr  = STATE.year ? '-' + STATE.year : '';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = `cbm-${layerStr}${yearStr}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
}

// ── URL Hash (permalink) ───────────────────────────────────────────────────

function updateHash() {
    const p = new URLSearchParams();

    const activeLayers = Object.keys(STATE.layers).filter(k => STATE.layers[k]);
    if (activeLayers.length < 3) p.set('layers', activeLayers.join(','));

    const activeBsl = Object.keys(STATE.bsl).filter(k => STATE.bsl[k]);
    if (activeBsl.length < 5) p.set('bsl', activeBsl.join(','));

    if (STATE.year)    p.set('year', STATE.year);
    if (STATE.hideLow) p.set('conf', 'nol');

    const qs = p.toString();
    const href = qs ? '#' + qs : location.pathname + location.search;
    clearTimeout(_hashTimer);
    _hashTimer = setTimeout(() => history.replaceState(null, '', href), 100);
}

function restoreFromHash() {
    const hash = location.hash.slice(1);
    if (!hash) return;
    const p = new URLSearchParams(hash);

    if (p.has('layers')) {
        const active = new Set(p.get('layers').split(','));
        for (const k of Object.keys(STATE.layers)) {
            STATE.layers[k] = active.has(k);
            const el = document.getElementById(`layer-${k}`);
            if (el) el.checked = STATE.layers[k];
        }
    }

    if (p.has('bsl')) {
        const active = new Set(p.get('bsl').split(','));
        for (const k of Object.keys(STATE.bsl)) {
            STATE.bsl[k] = active.has(k);
        }
        document.querySelectorAll('input[name="bsl"]').forEach(cb => {
            cb.checked = STATE.bsl[cb.value] ?? true;
        });
    }

    if (p.has('year')) {
        STATE.year = parseInt(p.get('year'));
        const slider   = document.getElementById('year-slider');
        const numInput = document.getElementById('year-input');
        if (slider)   { slider.value   = STATE.year; slider.disabled   = false; }
        if (numInput) { numInput.value = STATE.year; numInput.disabled = false; }
        const allYears = document.getElementById('all-years');
        if (allYears) allYears.checked = false;
    }

    if (p.get('conf') === 'nol') {
        STATE.hideLow = true;
        const el = document.getElementById('hide-low');
        if (el) el.checked = true;
    }
}

// ── Choropleth ─────────────────────────────────────────────────────────────

function choroColor(rate) {
    if (rate == null) return '#f5f5f5';
    if (rate > 0.8)   return '#08519c';
    if (rate > 0.6)   return '#2171b5';
    if (rate > 0.4)   return '#4292c6';
    if (rate > 0.2)   return '#74b4d8';
    if (rate > 0)     return '#b0d0ed';
    return '#f5f5f5';
}

// FEATURE 4: Stored world GeoJSON so updateChoropleth() can re-render without re-fetching
let _worldGeoJSON = null;

/**
 * FEATURE 4: Apply a new set of compliance rates to the existing choropleth layer.
 * Updates both fill colours and tooltips. Called on initial load and on form change.
 * @param {Object} rates - Map of iso3 → {a1_rate, submission_count, country_name}
 */
function updateChoropleth(rates) {
    if (!choroLayer) return;
    choroLayer.eachLayer(layer => {
        const feature = layer.feature;
        const iso3 = feature.properties['ISO3166-1-Alpha-3'];
        const name = feature.properties.name;
        const isRestricted = bwcMembership[iso3] === 'restricted' || RESTRICTED_NAMES.has(name);

        if (!isRestricted) {
            const d = rates[iso3];
            let newStyle;
            if (d) {
                newStyle = CHORO_STYLE_CBM(choroColor(+d.a1_rate));
            } else if (bwcMembership[iso3] === 'non_party') {
                newStyle = CHORO_STYLE_NON_PARTY;
            } else if (bwcMembership[iso3] === 'signatory') {
                newStyle = CHORO_STYLE_SIGNATORY;
            } else {
                newStyle = CHORO_STYLE_NO_DATA;
            }
            layer.setStyle(newStyle);

            // Update tooltip
            const d2 = rates[iso3];
            let bwcStatus;
            if (d2) {
                bwcStatus = `${d2.submission_count} public submission${d2.submission_count !== 1 ? 's' : ''}`;
            } else if (bwcMembership[iso3] === 'non_party') {
                bwcStatus = 'Not a BWC member';
            } else if (bwcMembership[iso3] === 'signatory') {
                bwcStatus = 'BWC signatory (signed, not ratified)';
            } else {
                bwcStatus = 'BWC state party — no public CBM data';
            }
            layer.setTooltipContent(`<strong>${name}</strong><br><small style="color:#888">${bwcStatus}</small>`);
        }
    });

    // FEATURE 4: update legend title to reflect current form
    const legendEl = document.querySelector('.map-legend');
    if (legendEl) {
        const titleEl = [...legendEl.querySelectorAll('.leg-title')]
            .find(el => el.textContent.includes('SUBMISSION RATE'));
        if (titleEl) titleEl.textContent = `SUBMISSION RATE (FORM ${choroForm})`;
    }
}

/**
 * FEATURE 4: Called when the choropleth form selector changes.
 * Fetches new compliance rates and re-renders the choropleth.
 */
async function onChoroFormChange() {
    const sel = document.getElementById('choro-form-select');
    if (!sel) return;
    choroForm = sel.value;
    try {
        const newRates = await api(`/api/map/compliance/${choroForm}`);
        const rateMap = {};
        // The form-specific endpoint returns 'rate'; normalize to 'a1_rate'
        // so the rest of the UI (choropleth, subtitle, global table) works uniformly
        newRates.forEach(c => { c.a1_rate = c.rate ?? c.a1_rate; rateMap[c.country_iso3] = c; });
        // Update the global complianceRates (used by choropleth click handlers and subtitle)
        Object.assign(complianceRates, rateMap);
        updateChoropleth(rateMap);
    } catch (e) {
        console.warn('Failed to fetch compliance for form', choroForm, e);
    }
}

async function loadChoropleth() {
    try {
        if (!_worldGeoJSON) {
            _worldGeoJSON = await fetch('/static/countries.geojson').then(r => r.json());
        }
        const world = _worldGeoJSON;

        choroLayer = L.geoJSON(world, {
            style: feature => {
                const iso3 = feature.properties['ISO3166-1-Alpha-3'];
                const name = feature.properties.name;
                const isRestricted = bwcMembership[iso3] === 'restricted' || RESTRICTED_NAMES.has(name);
                if (isRestricted) {
                    return CHORO_STYLE_RESTRICTED;
                }
                const d = complianceRates[iso3];
                if (d) {
                    return CHORO_STYLE_CBM(choroColor(+d.a1_rate));
                }
                // No CBM data — style by BWC membership
                if (bwcMembership[iso3] === 'non_party') {
                    return CHORO_STYLE_NON_PARTY;
                }
                if (bwcMembership[iso3] === 'signatory') {
                    return CHORO_STYLE_SIGNATORY;
                }
                // BWC state party but no public CBM data
                return CHORO_STYLE_NO_DATA;
            },
            onEachFeature: (feature, layer) => {
                const iso3 = feature.properties['ISO3166-1-Alpha-3'];
                const name = feature.properties.name;
                const isRestricted = bwcMembership[iso3] === 'restricted' || RESTRICTED_NAMES.has(name);
                const d = complianceRates[iso3];

                let bwcStatus;
                if (isRestricted) {
                    bwcStatus = 'Submitted CBM (restricted — not public)';
                } else if (d) {
                    bwcStatus = `${d.submission_count} public submission${d.submission_count !== 1 ? 's' : ''}`;
                } else if (bwcMembership[iso3] === 'non_party') {
                    bwcStatus = 'Not a BWC member';
                } else if (bwcMembership[iso3] === 'signatory') {
                    bwcStatus = 'BWC signatory (signed, not ratified)';
                } else {
                    bwcStatus = 'BWC state party — no public CBM data';
                }

                layer.bindTooltip(
                    `<strong>${name}</strong><br><small style="color:#888">${bwcStatus}</small>`,
                    { sticky: true, className: 'choro-tip' }
                );

                if (!isRestricted && d) {
                    layer.on('click', () => selectCountry(iso3));
                }
            },
        });

        choroLayer.addTo(map);
        choroLayer.bringToBack();
    } catch (e) {
        console.warn('Choropleth unavailable (network?):', e.message);
    }
}

// ── Legend ─────────────────────────────────────────────────────────────────

function addLegend() {
    const LegendControl = L.Control.extend({
        onAdd() {
            const div = L.DomUtil.create('div', 'map-legend');
            const dot = (c) => `<span class="legend-dot" style="background:${c}"></span>`;
            const sq  = (c) => `<span class="legend-sq"  style="background:${c}"></span>`;
            const hatch = `<span class="legend-sq legend-hatch"></span>`;
            div.innerHTML =
                `<div class="leg-header">
                    <span class="leg-header-title">Legend</span>
                    <button class="leg-toggle" data-action="toggle-legend" title="Hide legend">▼</button>
                </div>` +
                `<div class="leg-body">` +
                `<div class="leg-title">RESEARCH (BSL LEVEL)</div>` +
                [['BSL-4','#c0392b'],['BSL-3','#e67e22'],['BSL-2','#f39c12'],['BSL-1','#27ae60'],['Unknown','#95a5a6']]
                    .map(([l,c]) => `<div>${dot(c)}${l}</div>`).join('') +
                `<div class="leg-title">OTHER LAYERS</div>` +
                `<div>${dot('#8b1a1a')}Defence (A2)</div>` +
                `<div>${dot('#0a7a6a')}Vaccine (G)</div>` +
                `<div class="leg-title">SUBMISSION RATE (FORM ${choroForm})</div>` +
                [['>80%','#08519c'],['60–80%','#2171b5'],['40–60%','#4292c6'],
                 ['20–40%','#74b4d8'],['1–20%','#b0d0ed']]
                    .map(([l,c]) => `<div>${sq(c)}${l}</div>`).join('') +
                `<div class="leg-title">BWC MEMBERSHIP</div>` +
                `<div><span class="legend-sq" style="background:#5c3370;opacity:0.65"></span>Restricted (CHN/FRA/RUS/IND)</div>` +
                `<div>${sq('#b8bdd0')}State party — no public CBM</div>` +
                `<div>${sq('#d4b870')}Signatory (not ratified)</div>` +
                `<div>${hatch}Non-member</div>` +
                `</div>`;
            return div;
        },
    });
    new LegendControl({ position: 'bottomright' }).addTo(map);
}

function toggleLegend(btn) {
    const body = btn.closest('.map-legend').querySelector('.leg-body');
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    btn.textContent = hidden ? '▼' : '▲';
    btn.title = hidden ? 'Hide legend' : 'Show legend';
}

// ── Sidebar panel switcher ─────────────────────────────────────────────────

function showPanel(name) {
    document.getElementById('panel-list').style.display   = name === 'list'   ? 'flex' : 'none';
    document.getElementById('panel-detail').style.display = name === 'detail' ? 'flex' : 'none';
}

// ── Country list ───────────────────────────────────────────────────────────

function renderCountryList(countries) {
    document.getElementById('country-count').textContent = `${countries.length}`;
    const html = countries.map(c => {
        const ts   = _transparencyMap[c.country_iso3];
        const tBadge = ts != null ? transparencyBadge(ts) : '';
        return `
        <div class="country-item" data-iso3="${c.country_iso3}"
             data-action="select-country">
            <div class="country-name">${esc(c.country_name || c.country_iso3)}${tBadge}</div>
            <div class="country-meta">
                ${c.submission_count} submission${c.submission_count !== 1 ? 's' : ''}
                &nbsp;·&nbsp; ${c.facility_count} facilit${c.facility_count !== 1 ? 'ies' : 'y'}
                ${c.bsl4_count ? `&nbsp;·&nbsp; <span style="color:#c0392b">${c.bsl4_count} BSL-4</span>` : ''}
            </div>
        </div>`;
    }).join('');
    document.getElementById('country-list').innerHTML =
        html || '<div class="side-placeholder">No data</div>';
}

// Returns an HTML badge for a 0–100 transparency score
function transparencyBadge(score) {
    const cls  = score >= 65 ? 'transp-high' : score >= 35 ? 'transp-medium' : 'transp-low';
    return `<span class="transp-badge ${cls}" title="Transparency index: ${score}/100">${Math.round(score)}</span>`;
}

// ── Country detail ─────────────────────────────────────────────────────────

async function selectCountry(iso3) {
    ensureSidebarOpen();
    _currentIso3 = iso3;
    document.querySelectorAll('.country-item').forEach(el =>
        el.classList.toggle('active', el.dataset.iso3 === iso3)
    );
    showPanel('detail');
    document.getElementById('detail-title').textContent = iso3;
    document.getElementById('detail-loading').style.display = 'block';
    document.getElementById('detail-content').style.display = 'none';

    try {
        const data = await api(`/api/country/${iso3}`);
        renderCountryDetail(data);

        // Zoom map to this country's geocoded A1 facilities
        if (DATA.A1) {
            const pts = DATA.A1.features
                .filter(f => f.properties.country_iso3 === iso3)
                .map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]]);
            if (pts.length > 0) {
                map.fitBounds(L.latLngBounds(pts), { padding: [60, 60], maxZoom: 8 });
            }
        }
    } catch (e) {
        document.getElementById('detail-loading').innerHTML =
            '<span style="color:#c0392b">Error loading data.</span>';
    }
}

let _currentIso3  = null;
let _tabLoaded    = {};   // track which tabs have been fetched
let _defenceData  = null; // cached defence API response for current country
let _defenceSubtab = 'def-programmes'; // active defence sub-tab

function renderCountryDetail(data) {
    document.getElementById('detail-loading').style.display = 'none';
    document.getElementById('detail-content').style.display = 'flex';
    document.getElementById('detail-title').textContent = data.country_name;

    // Subtitle: submission count + A1 rate + transparency score + report export link
    const cr  = complianceRates[data.country_iso3 || _currentIso3];
    const ts  = _transparencyMap[data.country_iso3 || _currentIso3];
    const sub = document.getElementById('detail-subtitle');
    if (sub && cr) {
        const rate  = cr.a1_rate != null ? ` · A1 ${Math.round(cr.a1_rate * 100)}%` : '';
        const tBadge = ts != null ? ` · <span style="font-size:10px">${transparencyBadge(ts)}</span>` : '';
        const exportBtn = `<button data-action="export-country-report" data-iso3="${esc(data.country_iso3 || _currentIso3)}"
            style="background:none;border:none;color:#8090b8;font-size:10px;cursor:pointer;padding:0 0 0 6px"
            title="Export report card">↗ report</button>`;
        sub.innerHTML = `${cr.submission_count} sub${cr.submission_count !== 1 ? 's' : ''}${rate}${tBadge}${exportBtn}`;
    } else if (sub) { sub.innerHTML = ''; }

    renderComplianceGrid(data.compliance);
    renderFacilityList(data.facilities);
    // Reset lazy-loaded tabs
    _tabLoaded = {};
    _defenceData = null;
    _defenceSubtab = 'def-programmes';
    document.querySelectorAll('.dsubtab').forEach(b =>
        b.classList.toggle('active', b.dataset.subtab === 'def-programmes')
    );
    document.getElementById('defence-content').innerHTML    = '<div class="side-placeholder">Loading…</div>';
    document.getElementById('vaccine-content').innerHTML    = '<div class="side-placeholder">Loading…</div>';
    document.getElementById('legislation-content').innerHTML= '<div class="side-placeholder">Loading…</div>';
    document.getElementById('history-content').innerHTML    = '<div class="side-placeholder">Loading…</div>';
    // Switch to compliance tab by default
    switchDetailTab('compliance');
}

function switchDetailTab(name) {
    document.querySelectorAll('.dtab').forEach(b => {
        const isActive = b.dataset.tab === name;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-selected', isActive);
    });
    document.querySelectorAll('.tab-pane').forEach(p => {
        const active = p.id === `tab-${name}`;
        // Defence tab uses flex layout for its sub-tab bar
        p.style.display = active ? (p.id === 'tab-defence' ? 'flex' : 'block') : 'none';
    });
    // Lazy-load tab data
    if (_currentIso3 && !_tabLoaded[name]) {
        _tabLoaded[name] = true;
        if      (name === 'defence')     loadDefenceTab(_currentIso3);
        else if (name === 'vaccine')     loadVaccineTab(_currentIso3);
        else if (name === 'legislation') loadLegislationTab(_currentIso3);
        else if (name === 'history')     loadHistoryTab(_currentIso3);
    }
}

// ── Compliance grid ────────────────────────────────────────────────────────

function renderComplianceGrid(compliance) {
    const el    = document.getElementById('compliance-grid');
    const FORMS = ['A1', 'A2', 'B', 'C', 'E', 'F', 'G'];

    if (!compliance || compliance.length === 0) {
        el.innerHTML = '<div style="color:#8090b8;font-size:12px">No compliance data</div>';
        return;
    }

    const byYear = {};
    compliance.forEach(r => {
        if (!byYear[r.year]) byYear[r.year] = {};
        byYear[r.year][r.form] = r.status;
    });
    const years = Object.keys(byYear).map(Number).sort((a, b) => b - a);

    const tdClass = s =>
        s === 'substantive'        ? 'td-sub' :
        s === 'nothing_to_declare' ? 'td-ntd' :
        s === 'limited'            ? 'td-ltd' : 'td-abs';

    let html =
        `<table role="grid" aria-label="Compliance grid: form submission status by year">` +
        `<colgroup><col class="yr-col">${FORMS.map(() => `<col class="frm-col">`).join('')}</colgroup>` +
        `<thead><tr><th class="yr-col">Year</th>${FORMS.map(f => `<th>${f}</th>`).join('')}</tr></thead>` +
        `<tbody>`;

    years.forEach(yr => {
        html += `<tr><td class="yr-col">${yr}</td>`;
        FORMS.forEach(f => {
            const s = byYear[yr][f];
            const label = s ? s.replace(/_/g, ' ') : 'absent';
            html += `<td class="${tdClass(s)}" title="${f}: ${label}" role="gridcell" aria-label="${f} ${yr}: ${label}"></td>`;
        });
        html += '</tr>';
    });

    el.innerHTML = html + '</tbody></table>';
}

// ── Facility list in sidebar ───────────────────────────────────────────────

function renderFacilityList(facilities) {
    const el = document.getElementById('facility-list');
    if (!facilities || facilities.length === 0) {
        el.innerHTML = '<div style="color:#8090b8;font-size:12px;padding:8px 0">No declared research facilities</div>';
        return;
    }
    el.innerHTML = facilities.map(f => {
        const yrs = f.years_declared
            ? `${f.years_declared.length} year${f.years_declared.length !== 1 ? 's' : ''}`
            : '';
        // FEATURE 7: source link in facility list
        const sourceLink = f.latest_source_url
            ? `<a href="${esc(f.latest_source_url)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:10px;margin-left:6px">source ↗</a>`
            : '';
        // FEATURE 2: agents second meta line (truncated to 60 chars)
        const agentsMeta = f.agents_summary
            ? `<div class="fac-meta" style="color:#5a6a50">${esc(f.agents_summary.slice(0, 60))}${f.agents_summary.length > 60 ? '…' : ''}</div>`
            : '';
        return `
            <div class="fac-item" data-action="show-entity" data-entity-id="${esc(f.canonical_facility_id)}"
                <div class="fac-name">${esc(f.canonical_name || '[Unnamed facility]')}</div>
                <div class="fac-meta">
                    ${f.latest_containment
                        ? `<span style="color:${bslColor(f.latest_containment)}">${esc(f.latest_containment)}</span> &nbsp;·&nbsp; `
                        : ''}${yrs}${sourceLink}
                </div>
                ${agentsMeta}
            </div>`;
    }).join('');
}

// ── Tab loaders ────────────────────────────────────────────────────────────

async function loadDefenceTab(iso3) {
    const el = document.getElementById('defence-content');
    try {
        const data = await api(`/api/country/${iso3}/defence`);
        _defenceData = data;
        if (!data.programmes.length && !data.entities.length) {
            el.innerHTML = '<div class="side-placeholder">No defence data declared</div>';
            return;
        }
        renderDefenceProgrammes(data.programmes);
    } catch (e) {
        el.innerHTML = '<div class="side-placeholder" style="color:#c0392b">Error loading data</div>';
    }
}

function switchDefenceSubtab(name) {
    _defenceSubtab = name;
    document.querySelectorAll('.dsubtab').forEach(b => {
        const isActive = b.dataset.subtab === name;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-selected', isActive);
    });
    if (!_defenceData) return;
    if (name === 'def-programmes') {
        renderDefenceProgrammes(_defenceData.programmes);
    } else {
        renderDefenceFacilityList(_defenceData.entities);
    }
}

function renderDefenceProgrammes(programmes) {
    const el = document.getElementById('defence-content');
    if (!programmes || !programmes.length) {
        el.innerHTML = '<div class="side-placeholder">No programmes declared</div>';
        return;
    }
    const uniqueYears = [...new Set(programmes.map(p => p.year))].sort((a, b) => b - a);
    el.innerHTML =
        `<div class="side-section-label" style="padding:10px 16px 4px">PROGRAMMES (FORM A2) · ${uniqueYears.length} year${uniqueYears.length !== 1 ? 's' : ''}</div>` +
        programmes.map(p => `
            <div class="def-prog">
                <div class="def-prog-name">${esc(p.programme_name || 'National Programme')}
                    <span style="color:#8090b0;font-weight:400;margin-left:6px">${p.year}</span></div>
                ${p.responsible_org ? `<div class="def-prog-org">${esc(p.responsible_org)}</div>` : ''}
                ${p.objectives_summary ? `<div class="def-prog-obj">${esc(p.objectives_summary)}</div>` : ''}
                ${p.total_funding_amount ? `<div class="def-prog-org" style="margin-top:3px">Funding: ${esc(p.total_funding_amount)} ${esc(p.total_funding_currency || '')}</div>` : ''}
            </div>`).join('');
}

function renderDefenceFacilityList(entities) {
    const el = document.getElementById('defence-content');
    if (!entities || !entities.length) {
        el.innerHTML = '<div class="side-placeholder">No defence facilities declared</div>';
        return;
    }
    el.innerHTML =
        `<div class="side-section-label" style="padding:10px 16px 4px">DECLARED FACILITIES · ${entities.length}</div>` +
        entities.map(e => {
            const yrs = e.first_year === e.last_year
                ? String(e.first_year)
                : `${e.first_year}–${e.last_year}`;
            const bsl = e.has_bsl4 ? `<span style="color:${bslColor('BSL-4')}">BSL-4</span>`
                       : e.has_bsl3 ? `<span style="color:${bslColor('BSL-3')}">BSL-3</span>` : '';
            return `<div class="fac-item" data-action="show-defence-entity" data-entity-id="${esc(e.canonical_id)}"
                <div class="fac-name">${esc(e.canonical_name || '[Unnamed facility]')}</div>
                <div class="fac-meta">${bsl ? bsl + ' &nbsp;·&nbsp; ' : ''}${yrs}</div>
            </div>`;
        }).join('');
}

async function showDefenceEntityModal(entityId) {
    map.closePopup();
    document.getElementById('modal-title').textContent = 'Loading…';
    document.getElementById('modal-body').innerHTML = '<div class="text-center py-4 text-muted">Loading…</div>';
    entityModal.show();
    try {
        const data = await api(`/api/entity/defence/${entityId}`);
        renderDefenceEntityModal(data);
    } catch (e) {
        document.getElementById('modal-body').innerHTML = '<div class="text-danger">Error loading facility data.</div>';
    }
}

function renderDefenceEntityModal(data) {
    document.getElementById('modal-title').textContent = data.canonical_name || '[Unnamed facility]';

    let html = `
        <div class="text-muted small mb-3">
            <strong>${esc(data.country_name || data.country_iso3)}</strong>
            &nbsp;·&nbsp; ID: <code>${esc(data.canonical_defence_facility_id)}</code>
            &nbsp;·&nbsp; <span style="color:#8b4a4a">Defence facility</span>
            &nbsp;·&nbsp; ${data.first_year}–${data.last_year}
        </div>`;

    if (data.all_names && data.all_names.length > 1) {
        html += `<div class="mb-3"><small class="text-muted"><strong>Also known as:</strong> ${data.all_names.map(esc).join('; ')}</small></div>`;
    }

    const yearGroups = [];
    for (const yr of (data.year_records || [])) {
        const last = yearGroups[yearGroups.length - 1];
        if (last && last.year === yr.year) { last.rows.push(yr); }
        else { yearGroups.push({ year: yr.year, rows: [yr] }); }
    }

    function _defenceYrRow(yr, showYear) {
        const bslParts = [];
        if (yr.bsl4_area_m2) bslParts.push(`BSL-4: ${yr.bsl4_area_m2} m²`);
        if (yr.bsl3_area_m2) bslParts.push(`BSL-3: ${yr.bsl3_area_m2} m²`);
        if (yr.bsl2_area_m2) bslParts.push(`BSL-2: ${yr.bsl2_area_m2} m²`);
        const kvs = [
            ['Facility name',    yr.facility_name],
            ['City',             yr.city],
            ['Address',          yr.address],
            ['Containment',      bslParts.join(', ') || null],
            ['Personnel (total)',yr.personnel_total],
            ['Personnel (mil.)', yr.personnel_military],
            ['Personnel (civ.)', yr.personnel_civilian],
            ['MoD funded',       yr.mod_funded != null ? (yr.mod_funded ? 'Yes' : 'No') : null],
            ['Work description', yr.work_description],
            ['Funding source',   yr.funding_source],
        ].filter(([, v]) => v != null && v !== '');
        const head = showYear ? `
            <div class="yr-head">${yr.year}
                ${yr.source_url ? `<a href="${esc(yr.source_url)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:11px;margin-left:8px">source ↗</a>` : ''}
                ${yr.confidence != null ? `<small class="text-muted fw-normal ms-2">confidence ${Math.round(yr.confidence * 100)}%</small>` : ''}
                ${yr.geocode_confidence ? `<small class="text-muted fw-normal ms-2">geocode: ${yr.geocode_confidence}</small>` : ''}
            </div>` : '';
        return `${head}<dl class="yr-kv">${kvs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join('')}</dl>`;
    }

    html += yearGroups.map(({ year, rows }) => {
        if (rows.length === 1) {
            return `<div class="year-record">${_defenceYrRow(rows[0], true)}</div>`;
        }
        const srcUrl = rows[0].source_url;
        return `<div class="year-record">
            <div class="yr-head">${year}
                ${srcUrl ? `<a href="${esc(srcUrl)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:11px;margin-left:8px">source ↗</a>` : ''}
                <small class="text-muted fw-normal ms-2">${rows.length} sub-facilities</small>
            </div>
            ${rows.map((r, i) => `
                <div style="border-left:2px solid #2d3a55;margin:6px 0 6px 8px;padding-left:10px">
                    <div style="font-size:0.75rem;color:#8090b0;margin-bottom:2px">Facility ${i + 1}</div>
                    ${_defenceYrRow(r, false)}
                </div>`).join('')}
        </div>`;
    }).join('') || '<div class="text-muted">No year records found.</div>';

    document.getElementById('modal-body').innerHTML = html;
}

// ── Vaccine entity modal ─────────────────────────────────────────────────────

async function showVaccineEntityModal(entityId) {
    map.closePopup();
    document.getElementById('modal-title').textContent = 'Loading…';
    document.getElementById('modal-body').innerHTML = '<div class="text-center py-4 text-muted">Loading…</div>';
    entityModal.show();
    try {
        const data = await api(`/api/entity/vaccine/${entityId}`);
        renderVaccineEntityModal(data);
    } catch (e) {
        document.getElementById('modal-body').innerHTML =
            `<div class="text-center py-4 text-danger">Failed to load vaccine facility: ${esc(String(e))}</div>`;
    }
}

function renderVaccineEntityModal(data) {
    document.getElementById('modal-title').textContent =
        data.canonical_name || '[Unnamed vaccine facility]';

    const yr = data.year_records || [];
    const header = `<div class="em-header">
        <div class="em-country">${esc(data.country_name || data.country_iso3)}</div>
        <div class="em-meta">${yr.length} year record${yr.length !== 1 ? 's' : ''}
            &nbsp;·&nbsp; ${data.first_year || '?'}–${data.last_year || '?'}</div>
    </div>`;

    const recordsHtml = yr.map(rec => {
        const kvs = [
            ['Facility name',    rec.facility_name],
            ['City',             rec.city],
            ['Address',          rec.address],
            ['Diseases covered', rec.diseases_covered],
            ['Vaccines',         rec.vaccines_summary],
        ].filter(([, v]) => v);
        return `
            <div class="year-record">
                <div class="yr-head">${rec.year}
                    ${rec.source_url
                        ? `<a href="${esc(rec.source_url)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:11px;margin-left:8px">source ↗</a>`
                        : `<small class="text-muted fw-normal ms-2">${esc(rec.document_id)}</small>`}
                    ${rec.confidence != null ? `<small class="text-muted fw-normal ms-2">confidence ${Math.round(rec.confidence * 100)}%</small>` : ''}
                </div>
                <dl class="yr-kv">${kvs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join('')}</dl>
            </div>`;
    }).join('') || '<div class="text-muted">No year records found.</div>';

    document.getElementById('modal-body').innerHTML = header + recordsHtml;
}

async function loadVaccineTab(iso3) {
    const el = document.getElementById('vaccine-content');
    try {
        const data = await api(`/api/country/${iso3}/vaccine`);
        if (!data.entities.length) {
            el.innerHTML = '<div class="side-placeholder">No vaccine facilities declared</div>';
            return;
        }
        el.innerHTML = `<div class="side-section-label" style="padding:10px 16px 4px">VACCINE FACILITIES (FORM G)</div>` +
            data.entities.map(vf => {
                const recs = data.records.filter(r => r.canonical_vaccine_facility_id === vf.canonical_id);
                const years = recs.map(r => r.year).filter(Boolean);
                const yStr = years.length
                    ? `${Math.min(...years)}–${Math.max(...years)}`
                    : (vf.first_year ? `${vf.first_year}–${vf.last_year}` : '');
                const diseases = recs.find(r => r.diseases_covered)?.diseases_covered || '';
                return `<div class="fac-item" data-action="show-vaccine-entity" data-entity-id="${esc(vf.canonical_id)}"
                    ><div class="fac-name">${esc(vf.canonical_name || '[Unnamed]')}</div>
                    <div class="fac-meta">${yStr}${diseases ? ' · ' + esc(diseases.slice(0,60)) : ''}</div>
                </div>`;
            }).join('');
    } catch (e) {
        el.innerHTML = '<div class="side-placeholder" style="color:#c0392b">Error loading data</div>';
    }
}

async function loadLegislationTab(iso3) {
    const el = document.getElementById('legislation-content');
    try {
        const data = await api(`/api/country/${iso3}/legislation`);
        if (!data.length) {
            el.innerHTML = '<div class="side-placeholder">No legislation data declared</div>';
            return;
        }
        const yn = v => v === true  ? `<span class="leg-yn-yes">Yes</span>`
                      : v === false ? `<span class="leg-yn-no">No</span>`
                      : '—';
        const cats = [
            ['prohibitions', 'Prohibitions'],
            ['exports',      'Export ctrl'],
            ['imports',      'Import ctrl'],
            ['biosafety',    'Biosafety'],
        ];
        const flds = ['legislation', 'regulations', 'other_measures'];
        let html = `<div style="padding:8px 16px 4px;color:#8090b8;font-size:10px;font-weight:700;letter-spacing:0.08em">FORM E — ${data.length} YEAR${data.length !== 1 ? 'S' : ''}</div>`;
        data.forEach(rec => {
            const laws = rec.key_laws && Array.isArray(rec.key_laws) ? rec.key_laws : [];
            html += `<div class="leg-year-block">
                <div class="leg-year-head">${rec.year}
                    ${rec.source_url ? `<a href="${esc(rec.source_url)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:10px;margin-left:8px">source ↗</a>` : ''}
                </div>
                <table class="leg-table">
                    <tr><td></td><td>Legis.</td><td>Regs.</td><td>Other</td><td>Amended</td></tr>
                    ${cats.map(([key, label]) => `
                    <tr><td>${label}</td>
                        ${flds.map(f => `<td>${yn(rec[key + '_' + f])}</td>`).join('')}
                        <td>${yn(rec[key + '_amended'])}</td>
                    </tr>`).join('')}
                </table>
                ${laws.length ? `<div class="leg-laws">${esc(laws.join('; ').slice(0, 200))}</div>` : ''}
            </div>`;
        });
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="side-placeholder" style="color:#c0392b">Error loading data</div>';
    }
}

async function loadHistoryTab(iso3) {
    const el = document.getElementById('history-content');
    try {
        const data = await api(`/api/country/${iso3}/past-programmes`);
        if (!data.length) {
            el.innerHTML = '<div class="side-placeholder">No past programme data declared</div>';
            return;
        }
        el.innerHTML = `<div style="padding:8px 16px 4px;color:#8090b8;font-size:10px;font-weight:700;letter-spacing:0.08em">FORM F — PAST PROGRAMMES</div>` +
            data.map(rec => {
                const hasBadge = (flag, cls, label) =>
                    `<span class="hist-badge ${flag ? cls : 'hist-none'}">${label}: ${flag ? 'Yes' : 'No'}</span>`;
                return `<div class="hist-item">
                    <div class="hist-year">${rec.year}
                        ${rec.source_url ? `<a href="${esc(rec.source_url)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:10px;margin-left:8px">source ↗</a>` : ''}
                    </div>
                    <div style="margin-bottom:4px">
                        ${hasBadge(rec.has_offensive_programme, 'hist-offensive', 'Offensive')}
                        ${hasBadge(rec.has_defensive_programme, 'hist-defensive', 'Defensive')}
                    </div>
                    ${rec.offensive_summary ? `<div class="hist-summary">${esc(rec.offensive_summary.slice(0, 300))}</div>` : ''}
                    ${rec.defensive_summary ? `<div class="hist-summary" style="margin-top:3px">${esc(rec.defensive_summary.slice(0, 300))}</div>` : ''}
                </div>`;
            }).join('');
    } catch (e) {
        el.innerHTML = '<div class="side-placeholder" style="color:#c0392b">Error loading data</div>';
    }
}

// ── Entity modal ───────────────────────────────────────────────────────────

async function showEntityModal(entityId) {
    map.closePopup();
    document.getElementById('modal-title').textContent = 'Loading…';
    document.getElementById('modal-body').innerHTML = '<div class="text-center py-4 text-muted">Loading…</div>';
    entityModal.show();

    try {
        const data = await api(`/api/entity/${entityId}`);
        renderEntityModal(data);
    } catch (e) {
        document.getElementById('modal-body').innerHTML = '<div class="text-danger">Error loading facility data.</div>';
    }
}

function renderEntityModal(data) {
    document.getElementById('modal-title').textContent = data.canonical_name || '[Unnamed facility]';
    const yr = data.year_records || [];

    const header = `
        <div class="text-muted small mb-2">
            <strong>${esc(data.country_name || data.country_iso3)}</strong>
            &nbsp;·&nbsp; ID: <code>${esc(data.canonical_facility_id)}</code>
            ${data.latest_containment
                ? `&nbsp;·&nbsp; <span style="color:${bslColor(data.latest_containment)}">${esc(data.latest_containment)}</span>`
                : ''}
            &nbsp;·&nbsp; ${yr.length} year${yr.length !== 1 ? 's' : ''} on record
        </div>
        ${data.all_names && data.all_names.length > 1
            ? `<div class="mb-2"><small class="text-muted"><strong>Also known as:</strong> ${data.all_names.map(esc).join('; ')}</small></div>`
            : ''}`;

    // Build year-records panel
    const recordsHtml = yr.map(rec => {
        const bsl4 = rec.has_bsl4 != null
            ? (rec.has_bsl4 ? `Yes${rec.bsl4_area_m2 ? ` (${rec.bsl4_area_m2} m²)` : ''}` : 'No') : null;
        const bsl3 = rec.has_bsl3 != null
            ? (rec.has_bsl3 ? `Yes${rec.bsl3_area_m2 ? ` (${rec.bsl3_area_m2} m²)` : ''}` : 'No') : null;
        const kvs = [
            ['Facility name',       rec.facility_name],
            ['Organisation',        rec.responsible_org],
            ['City',                rec.city],
            ['Address',             rec.address],
            ['BSL-4 unit',          bsl4],
            ['BSL-3 unit',          bsl3],
            ['Highest containment', rec.highest_containment],
            ['MoD funded',          rec.mod_funded != null ? (rec.mod_funded ? 'Yes' : 'No') : null],
            ['Agents / activities', rec.agents_summary],
        ].filter(([, v]) => v);
        const flagUI = rec.flagged_for_review
            ? `<span class="flag-badge">🚩 Flagged${rec.flag_note ? ': ' + esc(rec.flag_note) : ''}</span>
               <button class="flag-btn ms-2" data-action="unflag-facility" data-entity-id="${esc(data.canonical_facility_id)}" data-year="${rec.year}">Unflag</button>`
            : `<button class="flag-btn" data-action="flag-facility" data-entity-id="${esc(data.canonical_facility_id)}" data-year="${rec.year}">Flag for review</button>`;
        return `
            <div class="year-record">
                <div class="yr-head">${rec.year}
                    ${rec.source_url
                        ? `<a href="${esc(rec.source_url)}" target="_blank" rel="noopener noreferrer" class="popup-link" style="font-size:11px;margin-left:8px">source ↗</a>`
                        : `<small class="text-muted fw-normal ms-2">${esc(rec.document_id)}</small>`}
                    ${rec.confidence != null ? `<small class="text-muted fw-normal ms-2">confidence ${Math.round(rec.confidence * 100)}%</small>` : ''}
                    ${rec.geocode_confidence ? `<small class="text-muted fw-normal ms-2">geocode: ${rec.geocode_confidence}</small>` : ''}
                    ${flagUI}
                </div>
                <dl class="yr-kv">${kvs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join('')}</dl>
            </div>`;
    }).join('') || '<div class="text-muted">No year records found.</div>';

    // Build changes and timeline panels (only show tabs if ≥2 records)
    const changesHtml  = renderFacilityChangesTab(yr);
    const timelineHtml = renderTimelineTab(yr);
    const showChangeTab = yr.length >= 2;

    const tabBar = `<div class="em-tab-bar">
        <button class="em-tab active" data-tab="records"  data-action="switch-entity-tab">Year records (${yr.length})</button>
        ${showChangeTab ? `<button class="em-tab" data-tab="changes"  data-action="switch-entity-tab">Changes</button>` : ''}
        ${showChangeTab ? `<button class="em-tab" data-tab="timeline" data-action="switch-entity-tab">Timeline</button>` : ''}
    </div>`;

    const html = header + tabBar +
        `<div id="em-records-panel">${recordsHtml}</div>` +
        (showChangeTab ? `<div id="em-changes-panel"  style="display:none">${changesHtml}</div>`  : '') +
        (showChangeTab ? `<div id="em-timeline-panel" style="display:none">${timelineHtml}</div>` : '');

    document.getElementById('modal-body').innerHTML = html;
}

function switchEntityTab(btn, tab) {
    btn.closest('.em-tab-bar').querySelectorAll('.em-tab').forEach(b => {
        const isActive = b.dataset.tab === tab;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-selected', isActive);
    });
    const body = document.getElementById('modal-body');
    const panels = { records: 'em-records-panel', changes: 'em-changes-panel', timeline: 'em-timeline-panel' };
    Object.entries(panels).forEach(([key, id]) => {
        const el = body.querySelector(`#${id}`);
        if (el) el.style.display = key === tab ? '' : 'none';
    });
    // Attach hover listeners once the timeline SVG is visible
    if (tab === 'timeline') {
        const tlPanel = body.querySelector('#em-timeline-panel');
        if (tlPanel) initTimelineHover(tlPanel);
    }
}

// ── Search ─────────────────────────────────────────────────────────────────

function initSearch() {
    const input   = document.getElementById('search-input');
    const results = document.getElementById('search-results');

    input.addEventListener('input', () => {
        if (_searchMode === 'ai') return;  // AI mode: wait for Enter
        clearTimeout(searchTimer);
        const q = input.value.trim();
        if (q.length < 2) { results.classList.remove('open'); input.classList.remove('searching'); return; }
        input.classList.add('searching');
        searchTimer = setTimeout(() => doSearch(q), 300);
    });

    input.addEventListener('keydown', e => {
        // AI mode: Enter submits to the AI endpoint
        if (_searchMode === 'ai') {
            if (e.key === 'Enter') {
                e.preventDefault();
                const q = input.value.trim();
                if (q.length >= 3) {
                    results.classList.remove('open');
                    showAIQuery(q);
                }
            }
            return;
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

    // Close dropdown on clicks outside — mousedown fires before blur, so result
    // clicks still register.  This avoids the fragile 200ms setTimeout approach.
    document.addEventListener('mousedown', e => {
        if (!results.contains(e.target) && e.target !== input) {
            results.classList.remove('open');
            input.setAttribute('aria-expanded', 'false');
        }
    });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') { results.classList.remove('open'); input.setAttribute('aria-expanded', 'false'); input.blur(); }
    });

    _syncSearchModeVisuals(document.getElementById('search-mode-btn'), input);
}

function _syncSearchModeVisuals(btn, input) {
    if (_searchMode === 'ai') {
        if (btn) { btn.classList.add('ai-active'); btn.title = 'Switch back to facility search'; }
        if (input) { input.placeholder = 'Ask anything, e.g. "BSL-4 labs in Eastern Europe"…'; input.classList.add('ai-mode'); }
    } else {
        if (btn) { btn.classList.remove('ai-active'); btn.title = 'Switch to AI natural language search'; }
        if (input) { input.placeholder = 'Search facilities, organisms…'; input.classList.remove('ai-mode'); }
    }
}

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

const _LAYER_LABEL = { A1: 'Research', A2: 'Defence', G: 'Vaccine' };
const _LAYER_COLOR = { A1: '#4a8ad4', A2: '#8b1a1a', G: '#0a7a6a' };

// Highlight all occurrences of `term` within `text` (case-insensitive).
// Returns an HTML string with matches wrapped in <mark>.
function highlightTerm(text, term) {
    if (!term || !text) return esc(text || '');
    const re = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    return esc(text).replace(re, m => `<mark class="sr-hl">${m}</mark>`);
}

async function doSearch(q) {
    const input   = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    try {
        const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
        input.classList.remove('searching');
        results.innerHTML = data.length === 0
            ? '<li style="color:#8899c8;font-size:12px;padding:10px 14px">No results found</li>'
            : data.map((f, i) => {
                const layerColor = _LAYER_COLOR[f.layer] || '#4a8ad4';
                const layerLabel = _LAYER_LABEL[f.layer] || f.layer;
                const isActivity = f.match_type === 'activity';
                const actionAttrs = f.id
                    ? `data-action="select-search-result" data-entity-id="${esc(f.id)}" data-iso3="${esc(f.country_iso3)}" data-layer="${esc(f.layer)}"`
                    : `data-action="select-country" data-iso3="${esc(f.country_iso3)}"`;

                // For activity matches show a short snippet of the matched text
                let snippetHtml = '';
                if (isActivity && f.activity_snippet) {
                    const snippet = f.activity_snippet.length > 120
                        ? f.activity_snippet.slice(0, 120) + '…'
                        : f.activity_snippet;
                    snippetHtml = `<div class="sr-snippet">${highlightTerm(snippet, q)}</div>`;
                }

                const tagHtml = isActivity
                    ? `<span class="sr-tag sr-tag-activity">activity</span>`
                    : `<span class="sr-tag" style="color:${layerColor}">${layerLabel}</span>`;

                return `<li data-idx="${i}" ${actionAttrs}>
                    <div>${highlightTerm(f.name || '[Unnamed]', q)} ${tagHtml}</div>
                    <div class="sr-meta">${esc(f.country_name || f.country_iso3)}</div>
                    ${snippetHtml}
                </li>`;
              }).join('');
        results.classList.add('open');
        input.setAttribute('aria-expanded', 'true');
    } catch (e) {
        input.classList.remove('searching');
        console.error('Search error:', e);
    }
}

async function selectSearchResult(entityId, iso3, layer) {
    document.getElementById('search-results').classList.remove('open');
    document.getElementById('search-input').setAttribute('aria-expanded', 'false');
    document.getElementById('search-input').value = '';
    await selectCountry(iso3);
    if (layer === 'A1' && entityId) {
        // Switch to Research tab then show entity modal
        switchDetailTab('research');
        showEntityModal(entityId);
    } else if (layer === 'G') {
        switchDetailTab('vaccine');
    } else if (layer === 'A2') {
        switchDetailTab('defence');
    }
}

// ── Copy permalink ─────────────────────────────────────────────────────────

function copyPermalink() {
    updateHash();
    // Small delay to let the hash settle
    setTimeout(() => {
        navigator.clipboard.writeText(location.href).then(() => {
            const btn = document.getElementById('fp-copy');
            if (!btn) return;
            const orig = btn.textContent;
            btn.textContent = '✓ Copied!';
            setTimeout(() => { btn.textContent = orig; }, 1800);
        }).catch(() => {
            // Fallback for browsers without clipboard API
            prompt('Copy this link:', location.href);
        });
    }, 120);
}

// ── Global compliance table ─────────────────────────────────────────────────

function showGlobalTable() {
    const rows = _countriesData.map(c => {
        const cr = complianceRates[c.country_iso3] || {};
        return { ...c, a1_rate: cr.a1_rate != null ? +cr.a1_rate : null };
    });
    renderGlobalTable(rows, _tableSort.col, _tableSort.dir);
    bootstrap.Modal.getOrCreateInstance(document.getElementById('global-table-modal')).show();
}

function sortGlobalTable(col) {
    const newDir = (_tableSort.col === col && _tableSort.dir === 'desc') ? 'asc' : 'desc';
    const rows = _countriesData.map(c => {
        const cr = complianceRates[c.country_iso3] || {};
        return { ...c, a1_rate: cr.a1_rate != null ? +cr.a1_rate : null };
    });
    renderGlobalTable(rows, col, newDir);
}

function renderGlobalTable(rows, sortCol, sortDir) {
    _tableSort = { col: sortCol, dir: sortDir };

    const sorted = [...rows].sort((a, b) => {
        let av = a[sortCol], bv = b[sortCol];
        const nullVal = sortDir === 'asc' ? Infinity : -Infinity;
        if (av == null) av = typeof bv === 'string' ? (sortDir === 'asc' ? 'zzz' : '') : nullVal;
        if (bv == null) bv = typeof av === 'string' ? (sortDir === 'asc' ? 'zzz' : '') : nullVal;
        if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
        return sortDir === 'asc' ? av - bv : bv - av;
    });

    const arrow = col => col !== sortCol ? '' : (sortDir === 'asc' ? ' ▲' : ' ▼');
    const th = (col, label) =>
        `<th class="gt-th-sort${col === sortCol ? ' gt-sorted' : ''}" data-action="sort-global-table" data-col="${col}">${label}${arrow(col)}</th>`;

    let html = `<table class="gt-table">
        <thead><tr>
            ${th('country_name',   'Country')}
            ${th('submission_count','Submissions')}
            ${th('a1_rate',        'A1 rate')}
            ${th('facility_count', 'Research fac.')}
            ${th('bsl4_count',     'BSL-4')}
            ${th('latest_year',    'Latest year')}
        </tr></thead><tbody>`;

    sorted.forEach(c => {
        const rate = c.a1_rate != null
            ? `<span class="gt-rate" style="background:${choroColor(c.a1_rate)}">${Math.round(c.a1_rate * 100)}%</span>`
            : `<span class="gt-rate gt-rate-none">—</span>`;
        const bsl4 = c.bsl4_count
            ? `<strong style="color:#c0392b">${c.bsl4_count}</strong>`
            : '<span style="color:#aaa">—</span>';
        html += `<tr class="gt-row" data-action="select-country-close-modal" data-iso3="${esc(c.country_iso3)}" data-modal-id="global-table-modal">
            <td>${esc(c.country_name || c.country_iso3)}</td>
            <td>${c.submission_count || 0}</td>
            <td>${rate}</td>
            <td>${c.facility_count || 0}</td>
            <td>${bsl4}</td>
            <td>${c.latest_year || '—'}</td>
        </tr>`;
    });

    html += '</tbody></table>';
    document.getElementById('gt-tbody').innerHTML = html;
}

// ── FEATURE 5: Longitudinal trends chart + pathogen frequency ─────────────────

let _trendsChartLoaded = false;
let _trendsPathogens   = null;  // null = not yet fetched
let _trendsChanges     = null;  // null = not yet fetched
let _trendsCapacity    = null;  // null = not yet fetched

async function showTrends() {
    const modalEl = document.getElementById('trends-modal');
    if (!modalEl) return;
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

    document.getElementById('trends-body').innerHTML =
        `<div id="trends-tab-bar">
            <button class="trends-tab active" data-tab="chart"    data-action="switch-trends-tab">📈 Trends</button>
            <button class="trends-tab" data-tab="pathogens"        data-action="switch-trends-tab">🦠 Pathogens</button>
            <button class="trends-tab" data-tab="changes"          data-action="switch-trends-tab">🔄 Changes</button>
            <button class="trends-tab" data-tab="capacity"         data-action="switch-trends-tab">🏗 BSL-4 Capacity</button>
         </div>
         <div id="trends-chart-panel"><div class="text-center py-4 text-muted">Loading…</div></div>
         <div id="trends-pathogen-panel"  style="display:none"><div class="text-center py-4 text-muted">Loading…</div></div>
         <div id="trends-changes-panel"   style="display:none"><div class="text-center py-4 text-muted">Loading…</div></div>
         <div id="trends-capacity-panel"  style="display:none"><div class="text-center py-4 text-muted">Loading…</div></div>`;

    _trendsChartLoaded = false;
    _trendsPathogens   = null;
    _trendsChanges     = null;
    _trendsCapacity    = null;
    modal.show();
    loadTrendsChart();
}

function switchTrendsTab(tab) {
    document.querySelectorAll('.trends-tab').forEach(b => {
        const isActive = b.dataset.tab === tab;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-selected', isActive);
    });
    document.getElementById('trends-chart-panel').style.display    = tab === 'chart'    ? '' : 'none';
    document.getElementById('trends-pathogen-panel').style.display = tab === 'pathogens'? '' : 'none';
    document.getElementById('trends-changes-panel').style.display  = tab === 'changes'  ? '' : 'none';
    document.getElementById('trends-capacity-panel').style.display = tab === 'capacity' ? '' : 'none';
    if (tab === 'pathogens' && !_trendsPathogens) loadPathogens();
    if (tab === 'changes'   && !_trendsChanges)   loadNotableChanges();
    if (tab === 'capacity'  && !_trendsCapacity)  loadBsl4Capacity();
}

async function loadTrendsChart() {
    try {
        const d = await api('/api/stats/timeline');
        const panel = document.getElementById('trends-chart-panel');
        panel.innerHTML = renderTrendsChart(d);
        _trendsChartLoaded = true;
        // Set up interactive hover tooltips on the SVG after insertion
        setupTrendsChartHover(d, panel);
    } catch (e) {
        document.getElementById('trends-chart-panel').innerHTML =
            '<div class="text-danger">Failed to load timeline data.</div>';
    }
}

async function loadPathogens() {
    const panel = document.getElementById('trends-pathogen-panel');
    if (!panel) return;
    try {
        const data = await api('/api/pathogens/frequency');
        _trendsPathogens = data;
        renderPathogenChart(data, panel);
    } catch (e) {
        panel.innerHTML = '<div class="text-danger">Failed to load pathogen data.</div>';
    }
}

function renderPathogenChart(data, container) {
    if (!data || !data.length) {
        container.innerHTML = '<div class="text-muted text-center py-4">No organism data available.</div>';
        return;
    }
    const maxCount = Math.max(...data.map(d => d.count), 1);
    container.innerHTML =
        `<p class="pathogen-chart-header">Number of unique declared research facilities mentioning each organism. Click any row to filter the map.</p>` +
        data.map(d => {
            const pct = (d.count / maxCount * 100).toFixed(1);
            return `<div class="pathogen-row" data-action="apply-organism-filter" data-term="${esc(d.term)}"
                <span class="pathogen-label">${esc(d.label)}</span>
                <div class="pathogen-bar-wrap"><div class="pathogen-bar" style="width:${pct}%"></div></div>
                <span class="pathogen-count">${d.count}</span>
            </div>`;
        }).join('');
}

/**
 * FEATURE 5: Render a self-contained SVG line chart for the trends modal.
 * No external library — pure SVG with hand-computed coordinates.
 */
function renderTrendsChart(d) {
    const W = 580, H = 260;
    const pad = { top: 20, right: 20, bottom: 40, left: 50 };
    const innerW = W - pad.left - pad.right;
    const innerH = H - pad.top - pad.bottom;

    const years = d.years || [];
    if (!years.length) return '<p class="text-muted">No data.</p>';

    const series = [
        { key: 'a1_facility_years',   label: 'Research facility-years', color: '#4a8ad4' },
        { key: 'bsl4_facility_years', label: 'BSL-4 facility-years',    color: '#c0392b' },
        { key: 'submitting_countries',label: 'Submitting countries',     color: '#27ae60' },
    ];

    // Compute extents
    const allVals = series.flatMap(s => d[s.key] || []).filter(v => v != null);
    const maxVal  = Math.max(...allVals, 1);
    const minYear = Math.min(...years);
    const maxYear = Math.max(...years);
    const yearRange = maxYear - minYear || 1;

    const xScale = year  => pad.left + ((year - minYear) / yearRange) * innerW;
    const yScale = value => pad.top  + (1 - value / maxVal) * innerH;

    // Identify gap regions: consecutive year pairs where gap > 1 year.
    // Rendered as a shaded band so the viewer sees data is absent — not interpolated.
    let gapRects = '';
    for (let i = 0; i < years.length - 1; i++) {
        if (years[i + 1] - years[i] > 1) {
            const x1 = xScale(years[i]).toFixed(1);
            const x2 = xScale(years[i + 1]).toFixed(1);
            const midX = ((+x1 + +x2) / 2).toFixed(1);
            const midY = (pad.top + innerH / 2).toFixed(1);
            gapRects +=
                `<rect x="${x1}" y="${pad.top}" width="${(+x2 - +x1).toFixed(1)}" height="${innerH}" fill="url(#gap-hatch)" opacity="0.35"/>` +
                `<line x1="${x1}" y1="${pad.top}" x2="${x1}" y2="${pad.top + innerH}" stroke="#bbb" stroke-width="1" stroke-dasharray="3,3"/>` +
                `<line x1="${x2}" y1="${pad.top}" x2="${x2}" y2="${pad.top + innerH}" stroke="#bbb" stroke-width="1" stroke-dasharray="3,3"/>` +
                `<text x="${midX}" y="${midY}" text-anchor="middle" font-size="9" fill="#aaa" ` +
                `transform="rotate(-90,${midX},${midY})">no public data</text>`;
        }
    }

    // Build polylines — broken into separate segments at each gap so no line is
    // drawn through years with no data (which would imply interpolation).
    const polylines = series.map(s => {
        const vals = d[s.key] || [];
        const segments = [];
        let current = [];
        for (let i = 0; i < years.length; i++) {
            if (vals[i] == null) { if (current.length) { segments.push(current); current = []; } continue; }
            if (current.length && years[i] - years[i - 1] > 1) { segments.push(current); current = []; }
            current.push(`${xScale(years[i]).toFixed(1)},${yScale(vals[i]).toFixed(1)}`);
        }
        if (current.length) segments.push(current);
        return segments
            .map(pts => `<polyline points="${pts.join(' ')}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`)
            .join('');
    });

    // X-axis tick marks (every 5 years)
    const xTicks = years.filter(y => y % 5 === 0).map(y =>
        `<line x1="${xScale(y).toFixed(1)}" y1="${pad.top + innerH}" x2="${xScale(y).toFixed(1)}" y2="${pad.top + innerH + 5}" stroke="#ccc"/>` +
        `<text x="${xScale(y).toFixed(1)}" y="${pad.top + innerH + 16}" text-anchor="middle" font-size="10" fill="#777">${y}</text>`
    ).join('');

    // Y-axis ticks (4 ticks)
    const ySteps = 4;
    const yTicks = Array.from({length: ySteps + 1}, (_, i) => {
        const v = Math.round((maxVal / ySteps) * i);
        const y = yScale(v).toFixed(1);
        return `<line x1="${pad.left - 4}" y1="${y}" x2="${pad.left}" y2="${y}" stroke="#ccc"/>` +
               `<text x="${pad.left - 6}" y="${(+y + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#777">${v}</text>`;
    }).join('');

    // Axes
    const axes =
        `<line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + innerH + 1}" stroke="#ccc"/>` +
        `<line x1="${pad.left}" y1="${pad.top + innerH}" x2="${pad.left + innerW}" y2="${pad.top + innerH}" stroke="#ccc"/>`;

    const svg = `<svg class="trends-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="gap-hatch" x="0" y="0" width="8" height="8" patternUnits="userSpaceOnUse">
            <line x1="0" y1="8" x2="8" y2="0" stroke="#999" stroke-width="0.8"/>
            <line x1="-2" y1="2" x2="2" y2="-2" stroke="#999" stroke-width="0.8"/>
            <line x1="6" y1="10" x2="10" y2="6" stroke="#999" stroke-width="0.8"/>
          </pattern>
        </defs>
        ${axes}${gapRects}${xTicks}${yTicks}${polylines.join('')}
    </svg>`;

    // Legend
    const legend = `<div class="trends-legend">
        ${series.map(s =>
            `<div class="trends-legend-item">
                <span class="trends-legend-line" style="background:${s.color}"></span>
                <span>${s.label}</span>
            </div>`
        ).join('')}
    </div>`;

    return svg + legend;
}

// ── Review key auth ─────────────────────────────────────────────────────────
//
// The API requires X-Review-Key on all flag/unflag writes and on GET /api/flagged.
// The key is stored in sessionStorage so the user only needs to enter it once
// per browser session.  It is never persisted to localStorage or sent with any
// other request.

const REVIEW_KEY_STORAGE = 'cbm_review_key';

/**
 * Return the stored review key, prompting the user if it has not been entered
 * yet this session.  Returns null if the user cancels the prompt.
 */
function getWriteKey() {
    let key = sessionStorage.getItem(REVIEW_KEY_STORAGE);
    if (!key) {
        key = prompt('Enter review key to continue:');
        if (!key) return null;
        sessionStorage.setItem(REVIEW_KEY_STORAGE, key);
    }
    return key;
}

/**
 * Called when the API returns 401 — clears the cached key so the user is
 * re-prompted on their next action.
 */
function clearWriteKey() {
    sessionStorage.removeItem(REVIEW_KEY_STORAGE);
}

/**
 * Wrapper around fetch for write operations that adds the review key header
 * and handles 401 by clearing the cached key and surfacing a clear error.
 */
async function authedPost(url, body) {
    const key = getWriteKey();
    if (key === null) throw new Error('Cancelled');
    const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Review-Key': key },
        body: JSON.stringify(body),
    });
    if (resp.status === 401) {
        clearWriteKey();
        throw new Error('Incorrect review key — please try again.');
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp;
}

/**
 * Wrapper around the api() helper for protected GET endpoints (e.g. /api/flagged).
 * Returns null without throwing if no key is stored (badge just stays hidden).
 */
async function authedGet(path) {
    const key = sessionStorage.getItem(REVIEW_KEY_STORAGE);
    if (!key) return null;  // Don't prompt for reads; just stay silent
    const resp = await fetch(path, { headers: { 'X-Review-Key': key } });
    if (resp.status === 401) { clearWriteKey(); return null; }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

// ── FEATURE 8: Flag for review ─────────────────────────────────────────────────

async function flagFacility(entityId, year) {
    const note = prompt(`Add a note for flagging ${entityId} (${year}):`, '') ?? '';
    if (note === null) return; // user cancelled
    try {
        await authedPost(`/api/entity/${encodeURIComponent(entityId)}/flag/${year}`,
                         { flag: true, note: note || null });
        // Re-fetch and re-render the entity modal, then update navbar badge
        showEntityModal(entityId);
        await refreshReviewBadge();
    } catch (e) {
        if (e.message !== 'Cancelled') alert('Failed to flag record: ' + e.message);
    }
}

async function unflagFacility(entityId, year) {
    try {
        await authedPost(`/api/entity/${encodeURIComponent(entityId)}/flag/${year}`,
                         { flag: false, note: null });
        // Refresh entity modal if open, then update queue badge and list
        showEntityModal(entityId);
        await refreshReviewBadge();
        // If review modal is currently open, reload its contents
        const modal = document.getElementById('review-modal');
        if (modal && modal.classList.contains('show')) loadReviewQueue();
    } catch (e) {
        if (e.message !== 'Cancelled') alert('Failed to unflag record: ' + e.message);
    }
}

// ── Review Queue ────────────────────────────────────────────────────────────

/**
 * Fetch flagged count and update the navbar badge.
 * Only runs if a review key is already cached — never prompts automatically.
 * Called on init and after any flag/unflag action.
 */
async function refreshReviewBadge() {
    try {
        const rows = await authedGet('/api/flagged');
        const btn   = document.getElementById('navbar-review');
        const badge = document.getElementById('review-badge');
        if (!btn || !badge) return;
        if (!rows || rows.length === 0) {
            btn.style.display = 'none';
        } else {
            badge.textContent = rows.length;
            btn.style.display = '';
        }
    } catch (_) { /* non-critical */ }
}

/** Open the review queue modal and load current flags. */
function showReviewQueue() {
    const modal = new bootstrap.Modal(document.getElementById('review-modal'));
    modal.show();
    loadReviewQueue();
}

/** Populate the review queue modal body from /api/flagged. */
async function loadReviewQueue() {
    const body = document.getElementById('review-modal-body');
    if (!body) return;
    body.innerHTML = '<div class="text-center py-4 text-muted">Loading…</div>';
    try {
        // authedGet prompts if no key stored, returns null on cancel/401
        let rows = await authedGet('/api/flagged');
        if (rows === null) {
            // Key was missing or wrong — prompt explicitly this time
            const key = getWriteKey();
            if (!key) { body.innerHTML = '<div class="text-center py-4 text-muted">Key required to view review queue.</div>'; return; }
            const resp = await fetch('/api/flagged', { headers: { 'X-Review-Key': key } });
            if (resp.status === 401) { clearWriteKey(); body.innerHTML = '<div class="text-center py-4 text-danger">Incorrect review key.</div>'; return; }
            rows = await resp.json();
        }
        if (rows.length === 0) {
            body.innerHTML = '<div class="text-center py-4 text-muted">No facilities are currently flagged for review.</div>';
            return;
        }
        const tableRows = rows.map(r => `
            <tr class="rq-row">
                <td>
                    <a class="rq-entity-link" href="#"
                       data-action="review-show-entity" data-entity-id="${esc(r.canonical_facility_id)}"
                    >${esc(r.canonical_name)}</a>
                </td>
                <td>${esc(r.country_iso3)}</td>
                <td>${r.year}</td>
                <td class="rq-note">${r.flag_note ? esc(r.flag_note) : '<span class="text-muted">—</span>'}</td>
                <td>
                    ${r.source_url
                        ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer" class="rq-src-link">source ↗</a>`
                        : ''}
                </td>
                <td>
                    <button class="flag-btn rq-unflag-btn"
                            data-action="rq-unflag" data-entity-id="${esc(r.canonical_facility_id)}" data-year="${r.year}">
                        Unflag
                    </button>
                </td>
            </tr>`).join('');

        body.innerHTML = `
            <table class="rq-table">
                <thead>
                    <tr>
                        <th>Facility</th>
                        <th>Country</th>
                        <th>Year</th>
                        <th>Note</th>
                        <th>Source</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>${tableRows}</tbody>
            </table>`;
    } catch (e) {
        body.innerHTML = `<div class="text-center py-4 text-danger">Failed to load flags: ${esc(e.message)}</div>`;
    }
}

/**
 * Unflag directly from the review queue row without opening the entity modal.
 * Removes the row on success and refreshes the badge.
 */
async function rqUnflag(entityId, year, btn) {
    btn.disabled = true;
    btn.textContent = '…';
    try {
        await authedPost(`/api/entity/${encodeURIComponent(entityId)}/flag/${year}`,
                         { flag: false, note: null });
        // Remove the row from the table
        btn.closest('tr').remove();
        // If table body is now empty, show the empty state
        const tbody = document.querySelector('#review-modal-body tbody');
        if (tbody && tbody.children.length === 0) {
            document.getElementById('review-modal-body').innerHTML =
                '<div class="text-center py-4 text-muted">No facilities are currently flagged for review.</div>';
        }
        await refreshReviewBadge();
    } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Unflag';
        if (e.message !== 'Cancelled') alert('Failed to unflag: ' + e.message);
    }
}

// ── Notable changes ────────────────────────────────────────────────────────

async function loadNotableChanges() {
    const panel = document.getElementById('trends-changes-panel');
    if (!panel) return;
    try {
        const data = await api('/api/changes/notable');
        _trendsChanges = data;
        renderChangesPanel(data, panel);
    } catch (e) {
        panel.innerHTML = '<div class="text-danger">Failed to load changes data.</div>';
    }
}

const _CHG_SEVERITY = { high: { cls: 'chg-high', label: 'High' }, medium: { cls: 'chg-med', label: 'Medium' } };
const _CHG_ICONS = {
    bsl4_gained:      '⬆ BSL-4 gained',
    bsl4_lost:        '⬇ BSL-4 lost',
    bsl4_area_change: '📐 BSL-4 area',
    bsl3_area_change: '📐 BSL-3 area',
    containment_change: '🔒 Containment',
    personnel_change:   '👥 Personnel',
};

function renderChangesPanel(data, container) {
    if (!data || !data.length) {
        container.innerHTML = '<div class="text-muted text-center py-4">No notable changes found in the dataset.</div>';
        return;
    }

    // Group by type for a summary header
    const byType = {};
    data.forEach(c => { byType[c.type] = (byType[c.type] || 0) + 1; });

    const summary = Object.entries(byType)
        .sort((a, b) => b[1] - a[1])
        .map(([t, n]) => `<span class="chg-summary-item">${_CHG_ICONS[t] || t}: <strong>${n}</strong></span>`)
        .join('');

    container.innerHTML =
        `<div class="chg-summary">${summary}</div>` +
        `<p class="chg-header">Year-on-year changes at facilities with ≥3 years on record. Click a row to open the facility.</p>` +
        data.map(c => {
            const sev = _CHG_SEVERITY[c.severity] || { cls: 'chg-med', label: '' };
            const icon = _CHG_ICONS[c.type] || '•';
            const yearRange = `${c.from_year}→${c.to_year}`;
            const actionAttrs = c.canonical_facility_id
                ? `data-action="show-entity" data-entity-id="${esc(c.canonical_facility_id)}"`
                : `data-action="select-country" data-iso3="${esc(c.country_iso3)}"`;
            return `<div class="chg-row ${sev.cls}" ${actionAttrs}
                <div class="chg-left">
                    <span class="chg-type-icon">${icon}</span>
                    <div>
                        <div class="chg-facility">${esc(c.facility_name || '[Unnamed]')}</div>
                        <div class="chg-meta">${esc(c.country_name || c.country_iso3)} &nbsp;·&nbsp; ${yearRange} &nbsp;·&nbsp; ${c.years_on_record} yrs on record</div>
                    </div>
                </div>
                <div class="chg-detail">${esc(c.label)}</div>
            </div>`;
        }).join('');
}

// Per-facility change diff (used in entity modal "Changes" tab)
function computeFacilityDiffs(yearRecords) {
    // yearRecords are ordered DESC by year; reverse for sequential comparison
    const asc = [...yearRecords].reverse();
    const diffs = [];
    for (let i = 1; i < asc.length; i++) {
        const prev = asc[i - 1], curr = asc[i];
        if (curr.year - prev.year > 4) continue; // skip long gaps

        const items = [];

        // Containment level
        if (prev.highest_containment !== curr.highest_containment &&
            prev.highest_containment && curr.highest_containment) {
            items.push({ key: 'Containment', from: prev.highest_containment, to: curr.highest_containment, dir: '' });
        }

        // BSL-4 area
        const p4 = prev.bsl4_area_m2, c4 = curr.bsl4_area_m2;
        if (p4 != null && c4 != null && p4 !== c4) {
            const pct = p4 > 0 ? ((c4 - p4) / p4 * 100).toFixed(0) : null;
            items.push({ key: 'BSL-4 area (m²)', from: p4, to: c4,
                         dir: c4 > p4 ? 'up' : 'down', pct });
        } else if (!p4 && c4) {
            items.push({ key: 'BSL-4 area (m²)', from: 'none', to: c4, dir: 'up' });
        } else if (p4 && !c4) {
            items.push({ key: 'BSL-4 area (m²)', from: p4, to: 'none declared', dir: 'down' });
        }

        // BSL-3 area
        const p3 = prev.bsl3_area_m2, c3 = curr.bsl3_area_m2;
        if (p3 != null && c3 != null && p3 !== c3) {
            const pct = p3 > 0 ? ((c3 - p3) / p3 * 100).toFixed(0) : null;
            items.push({ key: 'BSL-3 area (m²)', from: p3, to: c3,
                         dir: c3 > p3 ? 'up' : 'down', pct });
        }

        // Personnel
        const pp = prev.personnel_total, cp = curr.personnel_total;
        if (pp != null && cp != null && pp !== cp) {
            const pct = pp > 0 ? ((cp - pp) / pp * 100).toFixed(0) : null;
            items.push({ key: 'Personnel (total)', from: pp, to: cp,
                         dir: cp > pp ? 'up' : 'down', pct });
        }

        // MoD funded status
        if (prev.mod_funded != null && curr.mod_funded != null && prev.mod_funded !== curr.mod_funded) {
            items.push({ key: 'MoD funded', from: prev.mod_funded ? 'Yes' : 'No',
                         to: curr.mod_funded ? 'Yes' : 'No', dir: '' });
        }

        if (items.length) diffs.push({ from_year: prev.year, to_year: curr.year, items });
    }
    return diffs.reverse(); // most recent first
}

function renderFacilityChangesTab(yearRecords) {
    const diffs = computeFacilityDiffs(yearRecords);
    if (!diffs.length) {
        return '<div class="text-muted p-3" style="font-size:13px">No notable year-on-year changes detected.</div>';
    }
    return diffs.map(d =>
        `<div class="fac-diff-block">
            <div class="fac-diff-head">${d.from_year} → ${d.to_year}</div>
            ${d.items.map(item => {
                const arrow = item.dir === 'up'   ? '<span class="diff-arrow up">▲</span>'
                            : item.dir === 'down' ? '<span class="diff-arrow down">▼</span>'
                            : '<span class="diff-arrow">↔</span>';
                const pct = item.pct ? ` <span class="diff-pct">(${item.pct > 0 ? '+' : ''}${item.pct}%)</span>` : '';
                return `<div class="fac-diff-row">
                    <span class="diff-key">${esc(item.key)}</span>
                    <span class="diff-val">${esc(String(item.from))} ${arrow} <strong>${esc(String(item.to))}</strong>${pct}</span>
                </div>`;
            }).join('')}
        </div>`
    ).join('');
}

// ── AI Query ───────────────────────────────────────────────────────────────

let _aiResults = null;  // last AI query result set, for export / show-on-map
let _aiQuery   = '';    // last AI query string, for export filename / header

function showAIQuery(initialQuery = '') {
    const modalEl = document.getElementById('ai-query-modal');
    const modal   = bootstrap.Modal.getOrCreateInstance(modalEl);
    if (initialQuery) {
        const inp = document.getElementById('ai-query-input');
        if (inp) {
            inp.value = initialQuery;
            // Clear any stale results from a previous query
            document.getElementById('ai-query-results').innerHTML  = '';
            document.getElementById('ai-query-rationale').style.display = 'none';
        }
    }
    modal.show();
    // Auto-run if a query was passed (e.g. from the unified search bar)
    if (initialQuery) setTimeout(runAIQuery, 350);
}

async function runAIQuery() {
    const input      = document.getElementById('ai-query-input');
    const resultsEl  = document.getElementById('ai-query-results');
    const rationaleEl= document.getElementById('ai-query-rationale');
    const btn        = document.getElementById('ai-query-submit');
    const q = input?.value?.trim();
    if (!q || q.length < 3) return;
    _aiQuery = q;

    resultsEl.innerHTML = '<div class="text-center py-3 text-muted">Querying AI…</div>';
    rationaleEl.style.display = 'none';
    if (btn) btn.disabled = true;

    try {
        const resp = await fetch('/api/natural-query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ q }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `${resp.status}`);
        }
        const data = await resp.json();

        if (data.rationale) {
            rationaleEl.textContent = '🤖 ' + data.rationale;
            rationaleEl.style.display = 'block';
        }

        const facilities = data.facilities || [];
        _aiResults = facilities;

        if (!facilities.length) {
            resultsEl.innerHTML = '<div class="text-muted text-center py-3">No matching facilities found. Try rephrasing your query.</div>';
            return;
        }

        resultsEl.innerHTML =
            `<div class="ai-results-header">
                <span>${facilities.length} matching facilit${facilities.length !== 1 ? 'ies' : 'y'}</span>
                <div class="ai-results-actions">
                    <button class="fp-btn" data-action="apply-ai-filter">Show on map</button>
                    <button class="fp-btn" data-action="export-ai-results">Export CSV</button>
                </div>
             </div>
             <div class="ai-results-list">` +
            facilities.map(f =>
                `<div class="ai-result-item">
                    <div class="ai-result-name">${esc(f.name || '[Unnamed]')}</div>
                    <div class="ai-result-meta">
                        ${esc(f.country_name || f.country_iso3)}
                        ${f.latest_containment
                            ? ` &nbsp;·&nbsp; <span style="color:${bslColor(f.latest_containment)}">${esc(f.latest_containment)}</span>`
                            : ''}
                    </div>
                 </div>`
            ).join('') +
            `</div>`;
    } catch (e) {
        const hint = e.message.includes('503') ? ' — ANTHROPIC_API_KEY not configured on this server' : '';
        resultsEl.innerHTML = `<div class="text-danger">Search failed: ${esc(e.message)}${hint}</div>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

function applyAIFilter(facilities) {
    if (!facilities || !facilities.length) return;
    STATE.aiFilterIds = new Set(facilities.map(f => f.id).filter(Boolean));
    bootstrap.Modal.getOrCreateInstance(document.getElementById('ai-query-modal'))?.hide();
    updateActiveFilterChips();
    applyFilters();
    // Zoom map to show filtered results
    setTimeout(() => {
        if (CLUSTERS.A1 && CLUSTERS.A1.getLayers().length > 0) {
            try {
                const bounds = CLUSTERS.A1.getBounds();
                if (bounds.isValid()) map.fitBounds(bounds.pad(0.15));
            } catch (_) {}
        }
    }, 350); // wait for modal fade-out to complete
}

function exportAIResults(facilities) {
    if (!facilities || !facilities.length) return;
    const today = new Date().toISOString().slice(0, 10);
    const slug = (_aiQuery || 'results')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 60);
    const filename = `cbm-ai-${slug}-${today}.csv`;
    const meta = [
        ['# CBM Facility Explorer — AI Search Export'],
        [`# Query: "${(_aiQuery || '').replace(/"/g, '\\"')}"`],
        [`# Date: ${today}`],
        [`# Results: ${facilities.length} facilit${facilities.length !== 1 ? 'ies' : 'y'}`],
        ['#'],
    ];
    const header = ['id', 'name', 'country_iso3', 'country_name', 'latest_containment', 'years_declared'];
    const rows = facilities.map(f => [
        f.id || '', f.name || '', f.country_iso3 || '', f.country_name || '',
        f.latest_containment || '',
        Array.isArray(f.years_declared) ? f.years_declared.join('|') : (f.years_declared || ''),
    ]);
    const csvRows = [
        ...meta.map(r => r[0]),
        header.map(v => `"${v}"`).join(','),
        ...rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')),
    ];
    const csv = csvRows.join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
}

// ── Active filter chips bar (inside filter panel, below actions) ─────────────

// Shows dismissible chips for every non-default filter that is currently active.
function updateActiveFiltersBar() {
    const el = document.getElementById('map-filter-chips');
    if (!el) return;

    const chips = [];

    if (STATE.aiFilterIds) {
        const n = STATE.aiFilterIds.size;
        chips.push(`<span class="map-chip">🤖 AI: ${n} facilit${n !== 1 ? 'ies' : 'y'}
            <button class="map-chip-clear" data-action="clear-ai-filter" title="Clear">×</button></span>`);
    }
    if (STATE.showLapsed) {
        chips.push(`<span class="map-chip">⏳ Lapsed ≥${STATE.lapsedThreshold}y
            <button class="map-chip-clear" data-action="clear-lapsed-chip" title="Clear">×</button></span>`);
    }
    if (STATE.year !== null) {
        chips.push(`<span class="map-chip">📅 Year: ${STATE.year}
            <button class="map-chip-clear" data-action="clear-year-chip" title="Clear">×</button></span>`);
    }
    const bslAll = Object.values(STATE.bsl).every(Boolean);
    if (!bslAll) {
        const active = Object.keys(STATE.bsl).filter(k => STATE.bsl[k]);
        chips.push(`<span class="map-chip">🔒 BSL: ${active.join(', ')}</span>`);
    }
    if (STATE.hideLow) {
        chips.push(`<span class="map-chip">📍 High/med geocode only
            <button class="map-chip-clear" data-action="clear-hide-low-chip" title="Clear">×</button></span>`);
    }

    if (chips.length > 0) {
        chips.push(`<span class="map-chip map-chip--clear-all" data-action="clear-all-filters">✕ Clear all</span>`);
    }

    if (chips.length) {
        el.innerHTML = chips.join('');
        el.style.display = 'flex';
    } else {
        el.innerHTML = '';
        el.style.display = 'none';
    }
}

// ── Interactive chart tooltip (hover over Trends SVG) ────────────────────────

/**
 * Attaches mousemove/mouseleave handlers to the SVG trend chart.
 * Creates a floating tooltip div showing exact values for the nearest year.
 * @param {Object} d   — data from /api/stats/timeline
 * @param {Element} container — DOM element containing the SVG
 */
function setupTrendsChartHover(d, container) {
    const svg = container.querySelector('svg.trends-chart');
    if (!svg || !d || !d.years || !d.years.length) return;

    // Reuse or create a singleton tooltip div
    let tip = document.getElementById('trends-tooltip');
    if (!tip) {
        tip = document.createElement('div');
        tip.id = 'trends-tooltip';
        tip.className = 'trends-tooltip';
        document.body.appendChild(tip);
    }

    const CHART_W = 580;
    const PAD_LEFT = 50, PAD_RIGHT = 20;
    const innerW = CHART_W - PAD_LEFT - PAD_RIGHT;
    const minYear = d.years[0], maxYear = d.years[d.years.length - 1];
    const yearRange = maxYear - minYear || 1;

    const series = [
        { key: 'a1_facility_years',    label: 'Research facility-years', color: '#4a8ad4' },
        { key: 'bsl4_facility_years',  label: 'BSL-4 facility-years',    color: '#c0392b' },
        { key: 'submitting_countries', label: 'Submitting countries',     color: '#27ae60' },
    ];

    svg.addEventListener('mousemove', e => {
        const rect  = svg.getBoundingClientRect();
        const scale = rect.width / CHART_W;  // viewBox to rendered scale
        const relX  = (e.clientX - rect.left) / scale;

        // Map pixel X back to a year
        const rawYear = minYear + ((relX - PAD_LEFT) / innerW) * yearRange;
        const clampedYear = Math.round(rawYear);

        // Find the nearest actual year in the dataset
        let nearestIdx = -1, nearestDist = Infinity;
        d.years.forEach((y, i) => {
            const dist = Math.abs(y - clampedYear);
            if (dist < nearestDist) { nearestDist = dist; nearestIdx = i; }
        });

        if (nearestIdx === -1 || nearestDist > 2 || relX < PAD_LEFT || relX > PAD_LEFT + innerW) {
            tip.style.display = 'none';
            return;
        }

        const yr = d.years[nearestIdx];
        const lines = series
            .map(s => {
                const v = (d[s.key] || [])[nearestIdx];
                if (v == null) return null;
                return `<span style="color:${s.color}">■</span> ${s.label}: <strong>${v.toLocaleString()}</strong>`;
            })
            .filter(Boolean)
            .join('<br>');

        tip.innerHTML = `<strong>${yr}</strong><br>${lines}`;
        tip.style.display = 'block';
        tip.style.left = (e.clientX + 14) + 'px';
        tip.style.top  = (e.clientY - 14) + 'px';
    });

    svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
}

// ── BSL-4 capacity chart ─────────────────────────────────────────────────────

async function loadBsl4Capacity() {
    const panel = document.getElementById('trends-capacity-panel');
    if (!panel) return;
    try {
        const data = await api('/api/stats/bsl4-capacity');
        _trendsCapacity = data;
        renderBsl4CapacityChart(data, panel);
    } catch (e) {
        panel.innerHTML = '<div class="text-danger">Failed to load BSL-4 capacity data.</div>';
    }
}

/**
 * Renders a multi-line SVG chart of declared BSL-4 area (m²) per year per country.
 * Top-N countries are shown individually; the rest are aggregated as "Other".
 */
function renderBsl4CapacityChart(rows, container) {
    if (!rows || !rows.length) {
        container.innerHTML = '<div class="text-muted text-center py-4">No BSL-4 area data in the database.</div>';
        return;
    }

    // Build year × country matrix
    const years = [...new Set(rows.map(r => r.year))].sort((a, b) => a - b);
    const countryTotals = {};
    rows.forEach(r => {
        countryTotals[r.country_iso3] = (countryTotals[r.country_iso3] || 0) + (r.total_bsl4_area_m2 || 0);
    });

    // Show top 8 countries individually; rest summed as "Other"
    const TOP_N = 8;
    const sorted = Object.entries(countryTotals).sort((a, b) => b[1] - a[1]);
    const topCountries = sorted.slice(0, TOP_N).map(([iso3]) => iso3);

    // Lookup: year → iso3 → area
    const lookup = {};
    rows.forEach(r => {
        if (!lookup[r.year]) lookup[r.year] = {};
        lookup[r.year][r.country_iso3] = r.total_bsl4_area_m2 || 0;
    });

    // Country name lookup
    const nameMap = {};
    rows.forEach(r => { nameMap[r.country_iso3] = r.country_name || r.country_iso3; });

    // Build series: one per top country + one "Other"
    const PALETTE = ['#c0392b','#2471a3','#27ae60','#e67e22','#8e44ad','#17a589','#f39c12','#5d6d7e'];
    const series = topCountries.map((iso3, i) => ({
        iso3, label: nameMap[iso3] || iso3, color: PALETTE[i % PALETTE.length],
        values: years.map(y => lookup[y]?.[iso3] || 0),
    }));

    // "Other" series
    const otherValues = years.map(y => {
        let s = 0;
        Object.keys(lookup[y] || {}).forEach(iso3 => {
            if (!topCountries.includes(iso3)) s += lookup[y][iso3] || 0;
        });
        return s;
    });
    if (otherValues.some(v => v > 0)) {
        series.push({ iso3: 'OTHER', label: 'Other countries', color: '#888', values: otherValues });
    }

    const W = 580, H = 260;
    const pad = { top: 20, right: 20, bottom: 40, left: 60 };
    const innerW = W - pad.left - pad.right;
    const innerH = H - pad.top - pad.bottom;

    const minYear = years[0], maxYear = years[years.length - 1];
    const yearRange = maxYear - minYear || 1;

    const maxVal = Math.max(...series.flatMap(s => s.values), 1);
    const xScale = y => pad.left + ((y - minYear) / yearRange) * innerW;
    const yScale = v => pad.top  + (1 - v / maxVal) * innerH;

    // Build polylines
    const polylines = series.map(s =>
        `<polyline points="${years.map((y, i) => `${xScale(y).toFixed(1)},${yScale(s.values[i]).toFixed(1)}`).join(' ')}"
            fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`
    ).join('');

    // Axes
    const axes = `<line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top+innerH+1}" stroke="#333"/>
        <line x1="${pad.left}" y1="${pad.top+innerH}" x2="${pad.left+innerW}" y2="${pad.top+innerH}" stroke="#333"/>`;

    const xTicks = years.filter(y => y % 5 === 0).map(y =>
        `<text x="${xScale(y).toFixed(1)}" y="${pad.top+innerH+16}" text-anchor="middle" font-size="10" fill="#8090b0">${y}</text>`
    ).join('');

    const ySteps = 4;
    const yTicks = Array.from({length: ySteps + 1}, (_, i) => {
        const v = Math.round((maxVal / ySteps) * i);
        const y = yScale(v).toFixed(1);
        return `<text x="${pad.left-6}" y="${(+y+3).toFixed(1)}" text-anchor="end" font-size="10" fill="#8090b0">${v >= 1000 ? (v/1000).toFixed(0)+'k' : v}</text>
                <line x1="${pad.left-3}" y1="${y}" x2="${pad.left}" y2="${y}" stroke="#555"/>`;
    }).join('');

    const svg = `<svg class="trends-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">
        ${axes}${xTicks}${yTicks}${polylines}
        <text x="${pad.left-38}" y="${pad.top+innerH/2}" text-anchor="middle" font-size="9" fill="#8899c8" transform="rotate(-90,${pad.left-38},${pad.top+innerH/2})">m² declared</text>
    </svg>`;

    const legend = `<div class="capacity-country-legend">
        ${series.map(s =>
            `<div class="cap-leg-item"><span class="cap-leg-line" style="background:${s.color}"></span>${esc(s.label)}</div>`
        ).join('')}
    </div>`;

    container.innerHTML =
        `<p class="capacity-chart-header">Total declared BSL-4 laboratory area (m²) per country per year. Only includes submissions reporting a positive BSL-4 area figure.</p>` +
        svg + legend;
}

// ── Country comparison ────────────────────────────────────────────────────────

let _compareModal = null;

function showCompare() {
    const modalEl = document.getElementById('compare-modal');
    if (!modalEl) return;

    // Populate selects with all known countries
    const selA = document.getElementById('cmp-country-a');
    const selB = document.getElementById('cmp-country-b');
    if (selA && _countriesData.length && selA.options.length <= 1) {
        _countriesData.forEach(c => {
            const opt = `<option value="${c.country_iso3}">${esc(c.country_name || c.country_iso3)}</option>`;
            selA.insertAdjacentHTML('beforeend', opt);
            selB.insertAdjacentHTML('beforeend', opt);
        });
        // Pre-fill with currently viewed country if any
        if (_currentIso3) selA.value = _currentIso3;
    }

    _compareModal = bootstrap.Modal.getOrCreateInstance(modalEl);
    _compareModal.show();
}

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

function renderComparison(a, b) {
    // Helper: count A1 facilities and BSL-4 from compliance + facilities array
    const bsl4Count = d => d.facilities.filter(f => (f.latest_containment || '').includes('4')).length;
    const bsl3Count = d => d.facilities.filter(f => (f.latest_containment || '').includes('3')).length;

    // Extract organism names from agents_summary.  Split on semicolons/commas
    // first (these delimit distinct organisms), then keep terms that look like
    // scientific or common names rather than single short words.
    const organisms = d => {
        const terms = new Set();
        d.facilities.forEach(f => {
            if (!f.agents_summary) return;
            f.agents_summary.split(/[;,\/]/).forEach(part => {
                const t = part.trim();
                if (t.length > 3) terms.add(t);
            });
        });
        return [...terms].slice(0, 12).join('; ');
    };

    const years = d => {
        const byYear = {};
        d.compliance.forEach(r => {
            if (!byYear[r.year]) byYear[r.year] = {};
            byYear[r.year][r.form] = r.status;
        });
        return byYear;
    };

    const miniGrid = (byYear) => {
        const sortedYears = Object.keys(byYear).map(Number).sort((a, b) => a - b);
        return `<div class="cmp-compliance-mini">${sortedYears.map(yr => {
            const a1 = byYear[yr]['A1'];
            const cls = a1 === 'substantive' ? 'td-sub' : a1 === 'nothing_to_declare' ? 'td-ntd' : 'td-abs';
            return `<span class="cmp-yr-dot ${cls}" title="${yr}: ${a1 || 'absent'}"></span>`;
        }).join('')}</div>`;
    };

    const tsA = _transparencyMap[a.country_iso3];
    const tsB = _transparencyMap[b.country_iso3];
    const subA = (a.compliance.map(r => r.year)).filter((v,i,arr)=>arr.indexOf(v)===i).length;
    const subB = (b.compliance.map(r => r.year)).filter((v,i,arr)=>arr.indexOf(v)===i).length;

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

    return `<div class="cmp-grid">${col(a, tsA, subA)}${col(b, tsB, subB)}</div>
        <div style="text-align:right;margin-top:12px">
            <button class="cmp-export-btn" data-action="export-comparison" data-iso3-a="${esc(a.country_iso3)}" data-iso3-b="${esc(b.country_iso3)}">⬇ Export comparison CSV</button>
        </div>`;
}

function exportComparison(iso3a, iso3b) {
    const dataA = _countriesData.find(c => c.country_iso3 === iso3a);
    const dataB = _countriesData.find(c => c.country_iso3 === iso3b);
    if (!dataA || !dataB) return;
    const rows = [
        ['metric', iso3a, iso3b],
        ['Country', dataA.country_name || iso3a, dataB.country_name || iso3b],
        ['Submissions', dataA.submission_count, dataB.submission_count],
        ['Latest year', dataA.latest_year, dataB.latest_year],
        ['Research facilities', dataA.facility_count, dataB.facility_count],
        ['BSL-4 facilities', dataA.bsl4_count || 0, dataB.bsl4_count || 0],
        ['Transparency score', _transparencyMap[iso3a] ?? '', _transparencyMap[iso3b] ?? ''],
    ];
    const csv = rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g,'""')}"`).join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'}));
    a.download = `cbm-compare-${iso3a}-${iso3b}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
}

// ── Country report card export ────────────────────────────────────────────────

/**
 * Generates a self-contained HTML report card for the currently selected country
 * and opens it in a new browser tab. Designed to be printable.
 */
async function exportCountryReport(iso3) {
    if (!iso3) iso3 = _currentIso3;
    if (!iso3) return;
    const data = await api(`/api/country/${iso3}`).catch(() => null);
    if (!data) return;

    const ts = _transparencyMap[iso3];
    const cr = complianceRates[iso3] || {};
    const bsl4facs = data.facilities.filter(f => (f.latest_containment || '').includes('4'));
    const subYears = [...new Set(data.compliance.map(r => r.year))].sort((a,b) => b - a);
    const a1sub = data.compliance.filter(r => r.form === 'A1' && r.status === 'substantive').length;

    const html = `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>CBM Report: ${esc(data.country_name)}</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; color: #222; font-size: 13px; }
  h1 { font-size: 22px; border-bottom: 2px solid #ccc; padding-bottom: 8px; }
  h2 { font-size: 14px; font-weight: 700; color: #444; margin-top: 20px; letter-spacing: 0.05em; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #ddd; padding: 5px 10px; text-align: left; font-size: 12px; }
  th { background: #f0f2f8; font-weight: 700; color: #555; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 700; }
  .bsl4 { background: #fdecea; color: #c0392b; }
  .bsl3 { background: #fff4e5; color: #e67e22; }
  .footer { margin-top: 30px; color: #888; font-size: 11px; border-top: 1px solid #eee; padding-top: 8px; }
</style></head>
<body>
<h1>CBM Report Card: ${esc(data.country_name)}</h1>
<p><strong>ISO3:</strong> ${esc(iso3)} &nbsp; <strong>Submissions:</strong> ${subYears.length}
(${subYears[subYears.length-1] || '?'}–${subYears[0] || '?'})
&nbsp; <strong>A1 substantive rate:</strong> ${cr.a1_rate != null ? Math.round(cr.a1_rate*100)+'%' : 'N/A'}
${ts != null ? `&nbsp; <strong>Transparency index:</strong> ${ts}/100` : ''}</p>

<h2>SUBMISSION YEARS</h2>
<p>${subYears.join(', ')}</p>

<h2>DECLARED RESEARCH FACILITIES (A1)</h2>
${data.facilities.length ? `<table>
<tr><th>Facility</th><th>Containment</th><th>Years</th></tr>
${data.facilities.map(f => `<tr>
  <td>${esc(f.canonical_name) || '[Unnamed]'}</td>
  <td>${f.latest_containment
      ? `<span class="badge ${(f.latest_containment || '').includes('4') ? 'bsl4' : 'bsl3'}">${esc(f.latest_containment)}</span>`
      : '&mdash;'}</td>
  <td>${(f.years_declared || []).length}</td>
</tr>`).join('')}
</table>` : '<p><em>No A1 facilities declared.</em></p>'}

${bsl4facs.length ? `<h2>BSL-4 FACILITIES</h2>
<ul>${bsl4facs.map(f => `<li><strong>${esc(f.canonical_name) || '[Unnamed]'}</strong></li>`).join('')}</ul>` : ''}

<div class="footer">Generated from CBM Facility Explorer &middot; bwc-cbm.un.org &middot; Data as of ${new Date().toISOString().slice(0, 10)}</div>
</body></html>`;

    const blob = new Blob([html], {type: 'text/html'});
    const url  = URL.createObjectURL(blob);
    const win  = window.open(url, '_blank');
    if (!win) {
        // Fallback: download file
        const a = document.createElement('a');
        a.href = url; a.download = `cbm-report-${iso3}.html`; a.click();
    }
    setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ── Facility biography timeline ───────────────────────────────────────────────

/**
 * Renders a horizontal SVG timeline for a facility's declaration history.
 * Each year is marked with a dot; notable events (BSL-4 gained, area change) are annotated.
 * @param {Array} yearRecords  — year_records array from /api/entity/{id} (DESC order)
 * @returns {string} HTML string
 */
function renderTimelineTab(yearRecords) {
    if (!yearRecords || yearRecords.length < 2) {
        return '<div class="text-muted p-3" style="font-size:13px">Not enough data for a timeline (need ≥ 2 years).</div>';
    }

    // Work in ascending order for layout
    const asc = [...yearRecords].reverse();
    const years = asc.map(r => r.year);
    const minY = years[0], maxY = years[years.length - 1];
    const span = maxY - minY || 1;

    const W = 560, H = 100;
    const padL = 30, padR = 30, midY = 42;
    const innerW = W - padL - padR;

    const xOf = y => padL + ((y - minY) / span) * innerW;

    // Build event list from year-on-year diffs
    const events = [];
    for (let i = 1; i < asc.length; i++) {
        const prev = asc[i - 1], curr = asc[i];
        if (curr.year - prev.year > 4) continue;
        if (!prev.has_bsl4 && curr.has_bsl4)  events.push({ year: curr.year, label: '⬆ BSL-4', color: '#c0392b' });
        if (prev.has_bsl4  && !curr.has_bsl4) events.push({ year: curr.year, label: '⬇ BSL-4', color: '#888' });
        const p4 = prev.bsl4_area_m2, c4 = curr.bsl4_area_m2;
        if (p4 && c4 && Math.abs((c4-p4)/p4) >= 0.25) {
            events.push({ year: curr.year, label: `${c4>p4?'▲':'▼'} ${Math.abs(((c4-p4)/p4)*100).toFixed(0)}% BSL-4`, color: '#e67e22' });
        }
    }

    // Year labels every 5 years or all years if span ≤ 10
    const labelYears = span <= 10 ? years : years.filter(y => y % 5 === 0 || y === minY || y === maxY);

    // Draw the baseline, year dots, event markers
    const baseline = `<line x1="${padL}" y1="${midY}" x2="${W-padR}" y2="${midY}" stroke="#3a3a5a" stroke-width="2"/>`;

    const dots = asc.map(r => {
        const x     = xOf(r.year).toFixed(1);
        const fill  = r.has_bsl4 ? '#c0392b' : '#4a8ad4';
        return `<circle cx="${x}" cy="${midY}" r="5" fill="${fill}" stroke="#1e2130" stroke-width="1.5" data-year="${r.year}" style="cursor:pointer"/>`;
    }).join('');

    const yearLabels = labelYears.map(y => {
        const x = xOf(y).toFixed(1);
        return `<text x="${x}" y="${midY+20}" text-anchor="middle" class="tl-year-label">${y}</text>`;
    }).join('');

    // Event annotation: alternate above/below to avoid overlap
    const eventSvg = events.map((ev, i) => {
        const x   = xOf(ev.year).toFixed(1);
        const up  = i % 2 === 0;
        const lineY1 = up ? midY - 6 : midY + 6;
        const lineY2 = up ? midY - 22 : midY + 22;
        const textY  = up ? midY - 26 : midY + 30;
        return `<line x1="${x}" y1="${lineY1}" x2="${x}" y2="${lineY2}" stroke="${ev.color}" stroke-width="1.2" stroke-dasharray="2,2"/>
            <text x="${x}" y="${textY}" text-anchor="middle" font-size="8" fill="${ev.color}">${ev.label}</text>`;
    }).join('');

    // Tooltip on dot hover — use a global div
    let tlTip = document.getElementById('tl-tooltip');
    if (!tlTip) {
        tlTip = document.createElement('div');
        tlTip.id = 'tl-tooltip';
        tlTip.className = 'tl-tooltip';
        document.body.appendChild(tlTip);
    }

    // Build year info lookup for JS tooltip
    const recMap = JSON.stringify(Object.fromEntries(asc.map(r => [r.year, {
        cont: r.highest_containment || 'N/A',
        b4:   r.bsl4_area_m2 || null,
        p:    r.personnel_total || null,
    }])));

    const svg = `<svg class="timeline-svg" viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px"
            xmlns="http://www.w3.org/2000/svg"
            data-recs='${recMap.replace(/'/g, '&#39;')}'>
        ${baseline}${eventSvg}${dots}${yearLabels}
    </svg>`;

    // Legend
    const legend = `<div style="display:flex;gap:14px;margin-top:6px;font-size:11px;color:#8090b0">
        <span><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#c0392b;margin-right:3px;vertical-align:middle"></span>BSL-4 year</span>
        <span><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#4a8ad4;margin-right:3px;vertical-align:middle"></span>Non-BSL-4 year</span>
    </div>`;

    return `<div class="timeline-wrap">${svg}${legend}</div>
        <p style="font-size:11px;color:#8899c8;margin-top:8px">Hover dots for year details. Annotated events: BSL-4 gained/lost, large area changes.</p>`;
}

// Dot hover handler (called from SVG mouseover on circles)
// Attaches hover listeners to timeline SVG dots and hides tooltip on mouse leave
function initTimelineHover(container) {
    const svg = container.querySelector('svg.timeline-svg');
    if (!svg) return;
    const tip = document.getElementById('tl-tooltip');
    if (!tip) return;
    svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    const recs = JSON.parse(svg.dataset.recs || '{}');

    svg.querySelectorAll('circle').forEach((dot, i) => {
        dot.style.cursor = 'pointer';
        dot.addEventListener('mouseover', e => {
            const year = parseInt(dot.getAttribute('data-year') || '0') || null;
            if (!year) return;
            const r = recs[year];
            if (!r) return;
            tip.innerHTML = `<strong>${year}</strong><br>
                Containment: ${r.cont}<br>
                ${r.b4 ? `BSL-4 area: ${r.b4} m²<br>` : ''}
                ${r.p  ? `Personnel: ${r.p}<br>` : ''}`;
            tip.style.display = 'block';
            tip.style.left = (e.clientX + 12) + 'px';
            tip.style.top  = (e.clientY - 10) + 'px';
        });
        dot.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    });
}

// ── Static element listeners (replaces inline onchange/oninput/onkeydown) ──

function initStaticListeners() {
    // Layer checkboxes
    ['A1', 'A2', 'G'].forEach(layer => {
        const el = document.getElementById(`layer-${layer}`);
        if (el) el.addEventListener('change', onFilterChange);
    });
    // BSL checkboxes
    document.querySelectorAll('input[name="bsl"]').forEach(cb =>
        cb.addEventListener('change', onFilterChange)
    );
    // All-years checkbox
    const allYears = document.getElementById('all-years');
    if (allYears) allYears.addEventListener('change', onAllYearsToggle);
    // Year input
    const yearInput = document.getElementById('year-input');
    if (yearInput) {
        yearInput.addEventListener('input', () => onYearInput(yearInput.value));
        yearInput.addEventListener('change', onFilterChange);
    }
    // Year slider
    const yearSlider = document.getElementById('year-slider');
    if (yearSlider) {
        yearSlider.addEventListener('input', () => onYearSlider(yearSlider.value));
        yearSlider.addEventListener('change', onFilterChange);
    }
    // Hide low-confidence geocodes
    const hideLow = document.getElementById('hide-low');
    if (hideLow) hideLow.addEventListener('change', onFilterChange);
    // Lapsed toggle
    const lapsedToggle = document.getElementById('lapsed-toggle');
    if (lapsedToggle) lapsedToggle.addEventListener('change', onLapsedToggle);
    // Lapsed threshold
    const lapsedYears = document.getElementById('lapsed-years');
    if (lapsedYears) lapsedYears.addEventListener('input', () => onLapsedThresholdChange(lapsedYears.value));
    // Choropleth form selector
    const choroSelect = document.getElementById('choro-form-select');
    if (choroSelect) choroSelect.addEventListener('change', onChoroFormChange);
    // AI query input
    const aiInput = document.getElementById('ai-query-input');
    if (aiInput) aiInput.addEventListener('keydown', e => { if (e.key === 'Enter') runAIQuery(); });
    // Compare country selects
    const cmpA = document.getElementById('cmp-country-a');
    const cmpB = document.getElementById('cmp-country-b');
    if (cmpA) cmpA.addEventListener('change', onCompareSelect);
    if (cmpB) cmpB.addEventListener('change', onCompareSelect);
}

// ── Centralized event delegation (replaces inline onclick attributes) ────────

function initEventDelegation() {
    document.addEventListener('click', e => {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        e.preventDefault();
        const action = el.dataset.action;
        switch (action) {
            // ── Navigation ──
            case 'select-country':
                selectCountry(el.dataset.iso3);
                break;
            case 'select-country-close-modal': {
                const modalEl = document.getElementById(el.dataset.modalId || 'global-table-modal');
                bootstrap.Modal.getInstance(modalEl)?.hide();
                selectCountry(el.dataset.iso3);
                break;
            }
            case 'show-entity':
                showEntityModal(el.dataset.entityId);
                break;
            case 'show-defence-entity':
                showDefenceEntityModal(el.dataset.entityId);
                break;
            case 'show-vaccine-entity':
                showVaccineEntityModal(el.dataset.entityId);
                break;
            case 'select-search-result':
                selectSearchResult(el.dataset.entityId, el.dataset.iso3, el.dataset.layer);
                break;

            // ── Theme ──
            case 'toggle-theme':
                toggleTheme();
                break;

            // ── Sidebar / panels ──
            case 'toggle-sidebar':
                toggleSidebar();
                break;
            case 'toggle-filter-panel':
                toggleFilterPanel();
                break;
            case 'show-panel-list':
                showPanel('list');
                break;
            case 'switch-tab':
                switchDetailTab(el.dataset.tab);
                break;
            case 'switch-defence-subtab':
                switchDefenceSubtab(el.dataset.subtab);
                break;

            // ── Search / AI ──
            case 'toggle-search-mode':
                toggleSearchMode();
                break;
            case 'run-ai-query':
                runAIQuery();
                break;
            case 'apply-ai-filter':
                applyAIFilter(_aiResults);
                break;
            case 'export-ai-results':
                exportAIResults(_aiResults);
                break;

            // ── Actions / exports ──
            case 'export-csv':
                exportCSV();
                break;
            case 'copy-permalink':
                copyPermalink();
                break;
            case 'show-global-table':
                showGlobalTable();
                break;
            case 'show-trends':
                showTrends();
                break;
            case 'show-compare':
                showCompare();
                break;
            case 'show-review-queue':
                showReviewQueue();
                break;
            case 'toggle-year-play':
                toggleYearPlay();
                break;
            case 'export-country-report':
                exportCountryReport(el.dataset.iso3);
                break;
            case 'export-comparison':
                exportComparison(el.dataset.iso3A, el.dataset.iso3B);
                break;

            // ── Entity modal tabs ──
            case 'switch-entity-tab':
                switchEntityTab(el, el.dataset.tab);
                break;

            // ── Flag / review ──
            case 'flag-facility':
                flagFacility(el.dataset.entityId, parseInt(el.dataset.year));
                break;
            case 'unflag-facility':
                unflagFacility(el.dataset.entityId, parseInt(el.dataset.year));
                break;
            case 'rq-unflag':
                rqUnflag(el.dataset.entityId, parseInt(el.dataset.year), el);
                break;
            case 'review-show-entity':
                bootstrap.Modal.getInstance(document.getElementById('review-modal'))?.hide();
                showEntityModal(el.dataset.entityId);
                break;

            // ── Trends tabs ──
            case 'switch-trends-tab':
                switchTrendsTab(el.dataset.tab);
                break;
            case 'apply-organism-filter':
                applyOrganismFilter(el.dataset.term);
                break;

            // ── Global table ──
            case 'sort-global-table':
                sortGlobalTable(el.dataset.col);
                break;

            // ── Legend ──
            case 'toggle-legend':
                toggleLegend(el);
                break;

            // ── Filter chip clears ──
            case 'clear-ai-filter':
                clearAIFilter();
                break;
            case 'clear-lapsed-chip': {
                const lt = document.getElementById('lapsed-toggle');
                if (lt) lt.checked = false;
                onLapsedToggle();
                break;
            }
            case 'clear-year-chip': {
                const ay = document.getElementById('all-years');
                if (ay) ay.checked = true;
                onAllYearsToggle();
                break;
            }
            case 'clear-hide-low-chip': {
                const hl = document.getElementById('hide-low');
                if (hl) hl.checked = false;
                onFilterChange();
                break;
            }
            case 'clear-all-filters':
                clearAllFilters();
                break;
        }
    });
}

// ── Utilities ──────────────────────────────────────────────────────────────

function esc(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
