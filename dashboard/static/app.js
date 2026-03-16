'use strict';

// ── Constants ──────────────────────────────────────────────────────────────

// Countries whose CBM data is submitted to the ISU but not publicly available
const RESTRICTED = new Set(['CHN', 'FRA', 'RUS', 'IND']);
// This GeoJSON dataset gives France ISO code '-99'; match by name as fallback
const RESTRICTED_NAMES = new Set(['France']);

// BWC membership status (as of 2025: 189 states parties)
const BWC_SIGNATORIES = new Set(['EGY', 'HTI', 'SOM', 'SYR']);
const BWC_NON_PARTIES = new Set(['TCD', 'COM', 'DJI', 'ERI', 'ISR', 'FSM', 'NAM', 'SSD', 'TUV']);

// ── State ──────────────────────────────────────────────────────────────────

const STATE = {
    layers:  { A1: true, A2: true, G: true },
    bsl:     { 'BSL-4': true, 'BSL-3': true, 'BSL-2': true, 'BSL-1': true, unknown: true },
    year:    null,          // null = all years
    hideLow: false,
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
let _countriesData  = [];   // full /api/countries response, for global table
let _playInterval   = null; // year animation interval
let _hashTimer      = null; // debounce for history.replaceState
let _tableSort      = { col: 'submission_count', dir: 'desc' };

// ── Bootstrap ──────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    initClusters();
    entityModal = new bootstrap.Modal(document.getElementById('entity-modal'));
    initSearch();
    restoreFromHash();

    try {
        const [stats, countries, a1, a2, vaccines, compliance] = await Promise.all([
            api('/api/stats'),
            api('/api/countries'),
            api('/api/map/facilities'),
            api('/api/map/defence'),
            api('/api/map/vaccines'),
            api('/api/map/compliance'),
        ]);

        renderStats(stats);
        initYearSlider(stats.year_min, stats.year_max);

        compliance.forEach(c => { complianceRates[c.country_iso3] = c; });
        _countriesData = countries;
        renderCountryList(countries);

        DATA.A1 = a1;
        DATA.A2 = a2;
        DATA.G  = vaccines;

        computeLatestYears();
        applyFilters();
        addLegend();
        loadChoropleth();
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

function initMap() {
    map = L.map('map', {
        zoomControl: false,
        minZoom: 2,
        maxBounds: [[-90, -180], [90, 180]],
        maxBoundsViscosity: 1.0,
    }).setView([20, 0], 2);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' +
            ' contributors &copy; <a href="https://carto.com/">CARTO</a>',
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

function matchesFilter(layer, feature) {
    const p = feature.properties;
    if (STATE.year !== null) {
        if (p.year !== STATE.year) return false;
    } else {
        // Default: show only each country's latest submission year
        const latest = LATEST_YEAR[layer]?.[p.country_iso3];
        if (latest && p.year !== latest) return false;
    }
    if (STATE.hideLow && p.geocode_conf === 'low') return false;
    if (layer === 'A1' && !STATE.bsl[normalizeBsl(p.containment)]) return false;
    return true;
}

function applyFilters() {
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
                },
            }
        ).getLayers();

        CLUSTERS[layer].addLayers(leafletLayers);
    }

    updateHash();
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
        ? `<br><a class="popup-link" href="#" onclick="showEntityModal('${p.id}');return false;">Full history →</a>`
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
    if (rate > 0.2)   return '#9ecae1';
    if (rate > 0)     return '#deebf7';
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
        const isRestricted = RESTRICTED.has(iso3) || RESTRICTED_NAMES.has(name);

        if (!isRestricted) {
            const d = rates[iso3];
            let newStyle;
            if (d) {
                newStyle = { fillColor: choroColor(+d.a1_rate), fillOpacity: 0.55, weight: 0.5, color: '#aaa', opacity: 0.6 };
            } else if (BWC_NON_PARTIES.has(iso3)) {
                newStyle = { fillColor: 'url(#country-hatch)', fillOpacity: 1, weight: 0.5, color: '#bbb', opacity: 0.5 };
            } else if (BWC_SIGNATORIES.has(iso3)) {
                newStyle = { fillColor: '#d4b870', fillOpacity: 0.35, weight: 0.6, color: '#b09040', opacity: 0.6 };
            } else {
                newStyle = { fillColor: '#b8bdd0', fillOpacity: 0.40, weight: 0.5, color: '#8890a0', opacity: 0.6 };
            }
            layer.setStyle(newStyle);

            // Update tooltip
            const d2 = rates[iso3];
            let bwcStatus;
            if (d2) {
                bwcStatus = `${d2.submission_count} public submission${d2.submission_count !== 1 ? 's' : ''}`;
            } else if (BWC_NON_PARTIES.has(iso3)) {
                bwcStatus = 'Not a BWC member';
            } else if (BWC_SIGNATORIES.has(iso3)) {
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
        newRates.forEach(c => { rateMap[c.country_iso3] = c; });
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
                const isRestricted = RESTRICTED.has(iso3) || RESTRICTED_NAMES.has(name);
                if (isRestricted) {
                    return { fillColor: '#5c3370', fillOpacity: 0.45, weight: 0.5, color: '#999', opacity: 0.6 };
                }
                const d = complianceRates[iso3];
                if (d) {
                    return {
                        fillColor:   choroColor(+d.a1_rate),
                        fillOpacity: 0.55,
                        weight:      0.5,
                        color:       '#aaa',
                        opacity:     0.6,
                    };
                }
                // No CBM data — style by BWC membership
                if (BWC_NON_PARTIES.has(iso3)) {
                    return { fillColor: 'url(#country-hatch)', fillOpacity: 1, weight: 0.5, color: '#bbb', opacity: 0.5 };
                }
                if (BWC_SIGNATORIES.has(iso3)) {
                    return { fillColor: '#d4b870', fillOpacity: 0.35, weight: 0.6, color: '#b09040', opacity: 0.6 };
                }
                // BWC state party but no public CBM data
                return { fillColor: '#b8bdd0', fillOpacity: 0.40, weight: 0.5, color: '#8890a0', opacity: 0.6 };
            },
            onEachFeature: (feature, layer) => {
                const iso3 = feature.properties['ISO3166-1-Alpha-3'];
                const name = feature.properties.name;
                const isRestricted = RESTRICTED.has(iso3) || RESTRICTED_NAMES.has(name);
                const d = complianceRates[iso3];

                let bwcStatus;
                if (isRestricted) {
                    bwcStatus = 'Submitted CBM (restricted — not public)';
                } else if (d) {
                    bwcStatus = `${d.submission_count} public submission${d.submission_count !== 1 ? 's' : ''}`;
                } else if (BWC_NON_PARTIES.has(iso3)) {
                    bwcStatus = 'Not a BWC member';
                } else if (BWC_SIGNATORIES.has(iso3)) {
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
                    <button class="leg-toggle" onclick="toggleLegend(this)" title="Hide legend">▼</button>
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
                 ['20–40%','#9ecae1'],['1–20%','#deebf7']]
                    .map(([l,c]) => `<div>${sq(c)}${l}</div>`).join('') +
                `<div class="leg-title">BWC MEMBERSHIP</div>` +
                `<div>${sq('#5c3370')}Restricted (CHN/FRA/RUS/IND)</div>` +
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
    const html = countries.map(c => `
        <div class="country-item" data-iso3="${c.country_iso3}"
             onclick="selectCountry('${c.country_iso3}')">
            <div class="country-name">${esc(c.country_name || c.country_iso3)}</div>
            <div class="country-meta">
                ${c.submission_count} submission${c.submission_count !== 1 ? 's' : ''}
                &nbsp;·&nbsp; ${c.facility_count} facilit${c.facility_count !== 1 ? 'ies' : 'y'}
                ${c.bsl4_count ? `&nbsp;·&nbsp; <span style="color:#c0392b">${c.bsl4_count} BSL-4</span>` : ''}
            </div>
        </div>`
    ).join('');
    document.getElementById('country-list').innerHTML =
        html || '<div class="side-placeholder">No data</div>';
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

    // Subtitle: submission count + A1 rate
    const cr = complianceRates[data.country_iso3 || _currentIso3];
    const sub = document.getElementById('detail-subtitle');
    if (sub && cr) {
        const rate = cr.a1_rate != null ? ` · A1 rate ${Math.round(cr.a1_rate * 100)}%` : '';
        sub.textContent = `${cr.submission_count} submission${cr.submission_count !== 1 ? 's' : ''}${rate}`;
    } else if (sub) { sub.textContent = ''; }

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
    document.querySelectorAll('.dtab').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === name)
    );
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
        el.innerHTML = '<div style="color:#4a5280;font-size:12px">No compliance data</div>';
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
        `<table>` +
        `<colgroup><col class="yr-col">${FORMS.map(() => `<col class="frm-col">`).join('')}</colgroup>` +
        `<thead><tr><th class="yr-col">Year</th>${FORMS.map(f => `<th>${f}</th>`).join('')}</tr></thead>` +
        `<tbody>`;

    years.forEach(yr => {
        html += `<tr><td class="yr-col">${yr}</td>`;
        FORMS.forEach(f => {
            const s = byYear[yr][f];
            html += `<td class="${tdClass(s)}" title="${f}: ${s ? s.replace(/_/g,' ') : 'absent'}"></td>`;
        });
        html += '</tr>';
    });

    el.innerHTML = html + '</tbody></table>';
}

// ── Facility list in sidebar ───────────────────────────────────────────────

function renderFacilityList(facilities) {
    const el = document.getElementById('facility-list');
    if (!facilities || facilities.length === 0) {
        el.innerHTML = '<div style="color:#4a5280;font-size:12px;padding:8px 0">No declared research facilities</div>';
        return;
    }
    el.innerHTML = facilities.map(f => {
        const yrs = f.years_declared
            ? `${f.years_declared.length} year${f.years_declared.length !== 1 ? 's' : ''}`
            : '';
        // FEATURE 7: source link in facility list
        const sourceLink = f.latest_source_url
            ? `<a href="${esc(f.latest_source_url)}" target="_blank" class="popup-link" style="font-size:10px;margin-left:6px">source ↗</a>`
            : '';
        // FEATURE 2: agents second meta line (truncated to 60 chars)
        const agentsMeta = f.agents_summary
            ? `<div class="fac-meta" style="color:#5a6a50">${esc(f.agents_summary.slice(0, 60))}${f.agents_summary.length > 60 ? '…' : ''}</div>`
            : '';
        return `
            <div class="fac-item" onclick="showEntityModal('${f.canonical_facility_id}')">
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
    document.querySelectorAll('.dsubtab').forEach(b =>
        b.classList.toggle('active', b.dataset.subtab === name)
    );
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
                    <span style="color:#404870;font-weight:400;margin-left:6px">${p.year}</span></div>
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
            return `<div class="fac-item" onclick="showDefenceEntityModal('${esc(e.canonical_id)}')">
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

    html += (data.year_records || []).map(yr => {
        const bslParts = [];
        if (yr.bsl4_area_m2) bslParts.push(`BSL-4: ${yr.bsl4_area_m2} m²`);
        if (yr.bsl3_area_m2) bslParts.push(`BSL-3: ${yr.bsl3_area_m2} m²`);
        if (yr.bsl2_area_m2) bslParts.push(`BSL-2: ${yr.bsl2_area_m2} m²`);
        const kvs = [
            ['Facility name',       yr.facility_name],
            ['City',                yr.city],
            ['Address',             yr.address],
            ['Containment',         bslParts.join(', ') || null],
            ['Personnel (total)',    yr.personnel_total],
            ['Personnel (mil.)',     yr.personnel_military],
            ['Personnel (civ.)',     yr.personnel_civilian],
            ['MoD funded',          yr.mod_funded != null ? (yr.mod_funded ? 'Yes' : 'No') : null],
            ['Work description',    yr.work_description],
            ['Funding source',      yr.funding_source],
        ].filter(([, v]) => v != null && v !== '');

        return `
            <div class="year-record">
                <div class="yr-head">${yr.year}
                    ${yr.source_url
                        ? `<a href="${esc(yr.source_url)}" target="_blank" class="popup-link" style="font-size:11px;margin-left:8px">source ↗</a>`
                        : ''}
                    ${yr.confidence != null ? `<small class="text-muted fw-normal ms-2">confidence ${Math.round(yr.confidence * 100)}%</small>` : ''}
                    ${yr.geocode_confidence ? `<small class="text-muted fw-normal ms-2">geocode: ${yr.geocode_confidence}</small>` : ''}
                </div>
                <dl class="yr-kv">
                    ${kvs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join('')}
                </dl>
            </div>`;
    }).join('') || '<div class="text-muted">No year records found.</div>';

    document.getElementById('modal-body').innerHTML = html;
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
                return `<div class="vac-item">
                    <div class="vac-name">${esc(vf.canonical_name || '[Unnamed]')}</div>
                    <div class="vac-meta">${yStr}${diseases ? ' · ' + esc(diseases.slice(0,60)) : ''}</div>
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
        // Show most recent year only by default; user can see all years
        const latest = data[0];
        let html = `<div style="padding:8px 16px 4px;color:#4a5280;font-size:10px;font-weight:700;letter-spacing:0.08em">FORM E — ${data.length} YEAR${data.length !== 1 ? 'S' : ''}</div>`;
        data.forEach(rec => {
            const laws = rec.key_laws && Array.isArray(rec.key_laws) ? rec.key_laws : [];
            html += `<div class="leg-year-block">
                <div class="leg-year-head">${rec.year}
                    ${rec.source_url ? `<a href="${esc(rec.source_url)}" target="_blank" class="popup-link" style="font-size:10px;margin-left:8px">source ↗</a>` : ''}
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
        el.innerHTML = `<div style="padding:8px 16px 4px;color:#4a5280;font-size:10px;font-weight:700;letter-spacing:0.08em">FORM F — PAST PROGRAMMES</div>` +
            data.map(rec => {
                const hasBadge = (flag, cls, label) =>
                    `<span class="hist-badge ${flag ? cls : 'hist-none'}">${label}: ${flag ? 'Yes' : 'No'}</span>`;
                return `<div class="hist-item">
                    <div class="hist-year">${rec.year}
                        ${rec.source_url ? `<a href="${esc(rec.source_url)}" target="_blank" class="popup-link" style="font-size:10px;margin-left:8px">source ↗</a>` : ''}
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

    let html = `
        <div class="text-muted small mb-3">
            <strong>${esc(data.country_name || data.country_iso3)}</strong>
            &nbsp;·&nbsp; ID: <code>${esc(data.canonical_facility_id)}</code>
            ${data.latest_containment
                ? `&nbsp;·&nbsp; <span style="color:${bslColor(data.latest_containment)}">${esc(data.latest_containment)}</span>`
                : ''}
        </div>`;

    if (data.all_names && data.all_names.length > 1) {
        html += `<div class="mb-3"><small class="text-muted"><strong>Also known as:</strong> ${data.all_names.map(esc).join('; ')}</small></div>`;
    }

    html += (data.year_records || []).map(yr => {
        const bsl4 = yr.has_bsl4 != null
            ? (yr.has_bsl4 ? `Yes${yr.bsl4_area_m2 ? ` (${yr.bsl4_area_m2} m²)` : ''}` : 'No') : null;
        const bsl3 = yr.has_bsl3 != null
            ? (yr.has_bsl3 ? `Yes${yr.bsl3_area_m2 ? ` (${yr.bsl3_area_m2} m²)` : ''}` : 'No') : null;
        const kvs = [
            ['Facility name',       yr.facility_name],
            ['Organisation',        yr.responsible_org],
            ['City',                yr.city],
            ['Address',             yr.address],
            ['BSL-4 unit',          bsl4],
            ['BSL-3 unit',          bsl3],
            ['Highest containment', yr.highest_containment],
            ['MoD funded',          yr.mod_funded != null ? (yr.mod_funded ? 'Yes' : 'No') : null],
            ['Agents / activities', yr.agents_summary],
        ].filter(([, v]) => v);

        // FEATURE 8: flag button / badge per year-record
        const flagUI = yr.flagged_for_review
            ? `<span class="flag-badge">🚩 Flagged${yr.flag_note ? ': ' + esc(yr.flag_note) : ''}</span>
               <button class="flag-btn ms-2" onclick="unflagFacility('${esc(data.canonical_facility_id)}',${yr.year})">Unflag</button>`
            : `<button class="flag-btn" onclick="flagFacility('${esc(data.canonical_facility_id)}',${yr.year})">Flag for review</button>`;

        return `
            <div class="year-record">
                <div class="yr-head">${yr.year}
                    ${yr.source_url
                        ? `<a href="${esc(yr.source_url)}" target="_blank" class="popup-link" style="font-size:11px;margin-left:8px">source ↗</a>`
                        : `<small class="text-muted fw-normal ms-2">${esc(yr.document_id)}</small>`}
                    ${yr.confidence != null ? `<small class="text-muted fw-normal ms-2">confidence ${Math.round(yr.confidence * 100)}%</small>` : ''}
                    ${yr.geocode_confidence ? `<small class="text-muted fw-normal ms-2">geocode: ${yr.geocode_confidence}</small>` : ''}
                    ${flagUI}
                </div>
                <dl class="yr-kv">
                    ${kvs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join('')}
                </dl>
            </div>`;
    }).join('') || '<div class="text-muted">No year records found.</div>';

    document.getElementById('modal-body').innerHTML = html;
}

// ── Search ─────────────────────────────────────────────────────────────────

function initSearch() {
    const input   = document.getElementById('search-input');
    const results = document.getElementById('search-results');

    input.addEventListener('input', () => {
        clearTimeout(searchTimer);
        const q = input.value.trim();
        if (q.length < 2) { results.classList.remove('open'); input.classList.remove('searching'); return; }
        input.classList.add('searching');
        searchTimer = setTimeout(() => doSearch(q), 300);
    });

    input.addEventListener('keydown', e => {
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

    input.addEventListener('blur', () =>
        setTimeout(() => results.classList.remove('open'), 200)
    );

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') { results.classList.remove('open'); input.blur(); }
    });
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
            ? '<li style="color:#6070a0;font-size:12px;padding:10px 14px">No results found</li>'
            : data.map((f, i) => {
                const layerColor = _LAYER_COLOR[f.layer] || '#4a8ad4';
                const layerLabel = _LAYER_LABEL[f.layer] || f.layer;
                const isActivity = f.match_type === 'activity';
                const onclick = f.id
                    ? `selectSearchResult('${f.id}','${f.country_iso3}','${f.layer}')`
                    : `selectCountry('${f.country_iso3}')`;

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

                return `<li data-idx="${i}" onclick="${onclick}">
                    <div>${highlightTerm(f.name || '[Unnamed]', q)} ${tagHtml}</div>
                    <div class="sr-meta">${esc(f.country_name || f.country_iso3)}</div>
                    ${snippetHtml}
                </li>`;
              }).join('');
        results.classList.add('open');
    } catch (e) {
        input.classList.remove('searching');
        console.error('Search error:', e);
    }
}

async function selectSearchResult(entityId, iso3, layer) {
    document.getElementById('search-results').classList.remove('open');
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
        `<th class="gt-th-sort${col === sortCol ? ' gt-sorted' : ''}" onclick="sortGlobalTable('${col}')">${label}${arrow(col)}</th>`;

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
        html += `<tr class="gt-row" onclick="selectCountry('${c.country_iso3}');bootstrap.Modal.getInstance(document.getElementById('global-table-modal')).hide()">
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

// ── FEATURE 5: Longitudinal trends chart ──────────────────────────────────────

async function showTrends() {
    const modalEl = document.getElementById('trends-modal');
    if (!modalEl) return;
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    document.getElementById('trends-body').innerHTML =
        '<div class="text-center py-4 text-muted">Loading…</div>';
    modal.show();
    try {
        const d = await api('/api/stats/timeline');
        document.getElementById('trends-body').innerHTML = renderTrendsChart(d);
    } catch (e) {
        document.getElementById('trends-body').innerHTML =
            '<div class="text-danger">Failed to load timeline data.</div>';
    }
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

// ── FEATURE 8: Flag for review ─────────────────────────────────────────────────

async function flagFacility(entityId, year) {
    const note = prompt(`Add a note for flagging ${entityId} (${year}):`, '') ?? '';
    if (note === null) return; // user cancelled
    try {
        await fetch(`/api/entity/${encodeURIComponent(entityId)}/flag/${year}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flag: true, note: note || null }),
        });
        // Re-fetch and re-render the entity modal
        showEntityModal(entityId);
    } catch (e) {
        alert('Failed to flag record: ' + e.message);
    }
}

async function unflagFacility(entityId, year) {
    try {
        await fetch(`/api/entity/${encodeURIComponent(entityId)}/flag/${year}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flag: false, note: null }),
        });
        showEntityModal(entityId);
    } catch (e) {
        alert('Failed to unflag record: ' + e.message);
    }
}

// ── Utilities ──────────────────────────────────────────────────────────────

function esc(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
