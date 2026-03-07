'use strict';

// ── Constants ──────────────────────────────────────────────────────────────

// Countries whose CBM data is submitted to the ISU but not publicly available
const RESTRICTED = new Set(['CHN', 'FRA', 'RUS', 'IND']);

// ── State ──────────────────────────────────────────────────────────────────

const STATE = {
    layers:  { A1: true, A2: true, G: true },
    bsl:     { 'BSL-4': true, 'BSL-3': true, 'BSL-2': true, 'BSL-1': true, unknown: true },
    year:    null,          // null = all years
    hideLow: false,
};

// Raw GeoJSON data keyed by layer
const DATA = { A1: null, A2: null, G: null };

// Leaflet MarkerClusterGroup per layer
const CLUSTERS = {};

let map;
let choroLayer    = null;
let complianceRates = {};   // iso3 → {a1_rate, submission_count}
let entityModal   = null;
let filterCollapsed = false;
let searchTimer   = null;

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
        renderCountryList(countries);

        DATA.A1 = a1;
        DATA.A2 = a2;
        DATA.G  = vaccines;

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
});

// ── API ────────────────────────────────────────────────────────────────────

async function api(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
    return r.json();
}

// ── Stats bar ──────────────────────────────────────────────────────────────

function renderStats(s) {
    document.getElementById('stats-bar').innerHTML =
        `${s.total_unique_facilities.toLocaleString()} research facilities &nbsp;·&nbsp; ` +
        `${s.total_countries} countries &nbsp;·&nbsp; ` +
        `${s.total_submissions} submissions &nbsp;·&nbsp; ` +
        `${s.year_min}–${s.year_max}`;
}

// ── Map ────────────────────────────────────────────────────────────────────

function initMap() {
    map = L.map('map', { zoomControl: false }).setView([20, 0], 2);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' +
            ' contributors &copy; <a href="https://carto.com/">CARTO</a>',
        maxZoom: 19,
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
    if (layer === 'A1') {
        return { radius: 6, fillColor: bslColor(p.containment), color: '#fff', weight: 1.5, opacity: 1, fillOpacity: 0.85 };
    }
    if (layer === 'A2') {
        return { radius: 5, fillColor: '#8b1a1a', color: '#fff', weight: 1.5, opacity: 1, fillOpacity: 0.85 };
    }
    // G — vaccine
    return { radius: 5, fillColor: '#0a7a6a', color: '#fff', weight: 1.5, opacity: 1, fillOpacity: 0.85 };
}

// ── Filter logic ───────────────────────────────────────────────────────────

function matchesFilter(layer, feature) {
    const p = feature.properties;
    if (STATE.year !== null && p.year !== STATE.year) return false;
    if (STATE.hideLow && p.geocode_conf === 'low')   return false;
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

    return `<div class="fac-popup">
        <strong>${esc(p.name || 'Unnamed facility')}</strong>
        <div class="popup-loc">${esc(loc)}</div>
        ${badge} <small style="color:#888;margin-left:4px">declared ${p.year}</small>
        ${historyLink}
    </div>`;
}

// ── Filter panel UI ────────────────────────────────────────────────────────

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

    // Read geocode confidence
    STATE.hideLow = document.getElementById('hide-low').checked;

    applyFilters();
}

function onAllYearsToggle() {
    const allChecked = document.getElementById('all-years').checked;
    const slider = document.getElementById('year-slider');
    slider.disabled = allChecked;
    if (allChecked) {
        STATE.year = null;
        document.getElementById('year-display').textContent = 'ALL';
    } else {
        STATE.year = parseInt(slider.value);
        document.getElementById('year-display').textContent = STATE.year;
    }
    applyFilters();
}

function onYearInput(val) {
    document.getElementById('year-display').textContent = val;
    STATE.year = parseInt(val);
}

function initYearSlider(minYear, maxYear) {
    const slider = document.getElementById('year-slider');
    slider.min   = minYear;
    slider.max   = maxYear;
    slider.value = maxYear;
    document.getElementById('yr-min').textContent = minYear;
    document.getElementById('yr-max').textContent = maxYear;
}

// ── CSV Export ─────────────────────────────────────────────────────────────

function exportCSV() {
    const header = ['layer', 'id', 'name', 'country_iso3', 'country_name', 'year',
                    'containment', 'city', 'geocode_conf', 'lat', 'lon'];
    const rows = [header];

    for (const layer of ['A1', 'A2', 'G']) {
        if (!STATE.layers[layer] || !DATA[layer]) continue;
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
                ]);
            });
    }

    const csv = rows.map(r =>
        r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')
    ).join('\n');

    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = `cbm-facilities${STATE.year ? '-' + STATE.year : ''}.csv`;
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
    history.replaceState(null, '', qs ? '#' + qs : location.pathname + location.search);
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
        const slider = document.getElementById('year-slider');
        if (slider) { slider.value = STATE.year; slider.disabled = false; }
        const allYears = document.getElementById('all-years');
        if (allYears) allYears.checked = false;
        const yd = document.getElementById('year-display');
        if (yd) yd.textContent = STATE.year;
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

async function loadChoropleth() {
    try {
        const world = await fetch(
            'https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson'
        ).then(r => r.json());

        choroLayer = L.geoJSON(world, {
            style: feature => {
                const iso3 = feature.properties.ISO_A3;
                if (RESTRICTED.has(iso3)) {
                    return { fillColor: '#5c3370', fillOpacity: 0.45, weight: 0.5, color: '#999', opacity: 0.6 };
                }
                const d = complianceRates[iso3];
                return {
                    fillColor:   choroColor(d ? +d.a1_rate : null),
                    fillOpacity: d ? 0.55 : 0.07,
                    weight:      0.5,
                    color:       '#aaa',
                    opacity:     0.6,
                };
            },
            onEachFeature: (feature, layer) => {
                const iso3 = feature.properties.ISO_A3;
                const name = feature.properties.ADMIN;
                if (RESTRICTED.has(iso3)) {
                    layer.bindTooltip(
                        `<strong>${name}</strong><br><em>Restricted — submitted to ISU only, not publicly available</em>`,
                        { sticky: true }
                    );
                    return;
                }
                const d = complianceRates[iso3];
                if (!d) return;
                const pct = d.a1_rate != null ? Math.round(d.a1_rate * 100) + '%' : '—';
                layer.bindTooltip(
                    `<strong>${name}</strong><br>Form A1 rate: ${pct}<br>${d.submission_count} submissions`,
                    { sticky: true }
                );
                layer.on('click', () => selectCountry(iso3));
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
            div.innerHTML =
                `<div class="leg-title">RESEARCH (BSL LEVEL)</div>` +
                [['BSL-4','#c0392b'],['BSL-3','#e67e22'],['BSL-2','#f39c12'],['BSL-1','#27ae60'],['Unknown','#95a5a6']]
                    .map(([l,c]) => `<div>${dot(c)}${l}</div>`).join('') +
                `<div class="leg-title">OTHER LAYERS</div>` +
                `<div>${dot('#8b1a1a')}Defence (A2)</div>` +
                `<div>${dot('#0a7a6a')}Vaccine (G)</div>` +
                `<div class="leg-title">FORM A1 RATE</div>` +
                [['>80%','#08519c'],['60–80%','#2171b5'],['40–60%','#4292c6'],
                 ['20–40%','#9ecae1'],['1–20%','#deebf7'],['None','#f5f5f5']]
                    .map(([l,c]) => `<div>${sq(c)}${l}</div>`).join('') +
                `<div class="leg-title">RESTRICTED</div>` +
                `<div>${sq('#5c3370')}CHN / FRA / RUS / IND</div>`;
            return div;
        },
    });
    new LegendControl({ position: 'bottomright' }).addTo(map);
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

function renderCountryDetail(data) {
    document.getElementById('detail-loading').style.display = 'none';
    document.getElementById('detail-content').style.display = 'block';
    document.getElementById('detail-title').textContent = data.country_name;
    renderComplianceGrid(data.compliance);
    renderFacilityList(data.facilities);
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
        s === 'nothing_to_declare' ? 'td-ntd' : 'td-abs';

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
        return `
            <div class="fac-item" onclick="showEntityModal('${f.canonical_facility_id}')">
                <div class="fac-name">${esc(f.canonical_name || '[Unnamed facility]')}</div>
                <div class="fac-meta">
                    ${f.latest_containment
                        ? `<span style="color:${bslColor(f.latest_containment)}">${esc(f.latest_containment)}</span> &nbsp;·&nbsp; `
                        : ''}${yrs}
                </div>
            </div>`;
    }).join('');
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

        return `
            <div class="year-record">
                <div class="yr-head">${yr.year}
                    <small class="text-muted fw-normal ms-2">${esc(yr.document_id)}</small>
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

// ── Search ─────────────────────────────────────────────────────────────────

function initSearch() {
    const input   = document.getElementById('search-input');
    const results = document.getElementById('search-results');

    input.addEventListener('input', () => {
        clearTimeout(searchTimer);
        const q = input.value.trim();
        if (q.length < 2) { results.classList.remove('open'); return; }
        searchTimer = setTimeout(() => doSearch(q), 300);
    });

    input.addEventListener('blur', () =>
        setTimeout(() => results.classList.remove('open'), 200)
    );

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') { results.classList.remove('open'); input.blur(); }
    });
}

async function doSearch(q) {
    const results = document.getElementById('search-results');
    try {
        const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
        results.innerHTML = data.length === 0
            ? '<li style="color:#6070a0;font-size:12px">No results found</li>'
            : data.map(f =>
                `<li onclick="selectSearchResult('${f.canonical_facility_id}','${f.country_iso3}')">
                    <div>${esc(f.canonical_name || '[Unnamed]')}</div>
                    <div class="sr-meta">${esc(f.country_name || f.country_iso3)}</div>
                </li>`
              ).join('');
        results.classList.add('open');
    } catch (e) { console.error('Search error:', e); }
}

async function selectSearchResult(entityId, iso3) {
    document.getElementById('search-results').classList.remove('open');
    document.getElementById('search-input').value = '';
    await selectCountry(iso3);
    showEntityModal(entityId);
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
