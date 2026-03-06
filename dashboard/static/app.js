'use strict';

// ── State ──────────────────────────────────────────────────────────────────
let map;
let facilityLayer = null;
let choroLayer    = null;
let complianceRates = {};   // iso3 → {a1_rate, submission_count}
let entityModal   = null;
let searchTimer   = null;

// ── Bootstrap ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    entityModal = new bootstrap.Modal(document.getElementById('entity-modal'));
    initSearch();

    try {
        const [stats, countries, facilities, compliance] = await Promise.all([
            api('/api/stats'),
            api('/api/countries'),
            api('/api/map/facilities'),
            api('/api/map/compliance'),
        ]);

        renderStats(stats);
        compliance.forEach(c => { complianceRates[c.country_iso3] = c; });
        renderCountryList(countries);
        renderFacilityLayer(facilities);
        loadChoropleth();   // async, non-blocking
        addLegend();
    } catch (e) {
        console.error('Initialisation failed:', e);
        document.getElementById('country-list').innerHTML =
            '<div class="side-placeholder" style="color:#c0392b">Failed to load data — is the API running?</div>';
    }
});

// ── API helper ─────────────────────────────────────────────────────────────
async function api(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
    return r.json();
}

// ── Stats bar ──────────────────────────────────────────────────────────────
function renderStats(s) {
    document.getElementById('stats-bar').innerHTML =
        `${s.total_unique_facilities.toLocaleString()} facilities &nbsp;·&nbsp; ` +
        `${s.total_countries} countries &nbsp;·&nbsp; ` +
        `${s.total_submissions} submissions &nbsp;·&nbsp; ` +
        `${s.year_min}–${s.year_max}`;
}

// ── Map initialisation ─────────────────────────────────────────────────────
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

// ── Containment colour helpers ─────────────────────────────────────────────
function bslColor(containment) {
    if (!containment) return '#95a5a6';
    const u = containment.toUpperCase();
    if (u.includes('4')) return '#c0392b';
    if (u.includes('3')) return '#e67e22';
    if (u.includes('2')) return '#f39c12';
    if (u.includes('1')) return '#27ae60';
    return '#95a5a6';
}

// ── Facility markers ───────────────────────────────────────────────────────
function renderFacilityLayer(geojson) {
    facilityLayer = L.geoJSON(geojson, {
        pointToLayer: (feature, latlng) =>
            L.circleMarker(latlng, {
                radius:      6,
                fillColor:   bslColor(feature.properties.containment),
                color:       '#fff',
                weight:      1.5,
                opacity:     1,
                fillOpacity: 0.85,
            }),
        onEachFeature: (feature, layer) => {
            const p = feature.properties;
            const loc = [p.city, p.country_name].filter(Boolean).join(', ');
            layer.bindPopup(
                `<div class="fac-popup">
                    <strong>${esc(p.name || 'Unnamed facility')}</strong>
                    <div class="popup-loc">${esc(loc)}</div>
                    <span style="display:inline-block;padding:2px 7px;border-radius:4px;
                                 background:${bslColor(p.containment)};color:#fff;font-size:11px">
                        ${esc(p.containment || 'Unknown')}
                    </span>
                    <small style="color:#888;margin-left:6px">last declared ${p.year}</small>
                    <br>
                    <a class="popup-link" href="#"
                       onclick="showEntityModal('${p.id}');return false;">Full history →</a>
                </div>`,
                { maxWidth: 280 }
            );
        },
    }).addTo(map);
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
                const d = complianceRates[feature.properties.ISO_A3];
                return {
                    fillColor:   choroColor(d ? +d.a1_rate : null),
                    fillOpacity: d ? 0.55 : 0.07,
                    weight:      0.5,
                    color:       '#aaa',
                    opacity:     0.6,
                };
            },
            onEachFeature: (feature, layer) => {
                const d = complianceRates[feature.properties.ISO_A3];
                if (!d) return;
                const pct = d.a1_rate != null
                    ? Math.round(d.a1_rate * 100) + '%'
                    : '—';
                layer.bindTooltip(
                    `<strong>${feature.properties.ADMIN}</strong><br>` +
                    `Form A1 rate: ${pct} &nbsp;(${d.submission_count} submissions)`,
                    { sticky: true }
                );
                layer.on('click', () => selectCountry(feature.properties.ISO_A3));
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
            const dot  = (color) => `<span class="legend-dot"  style="background:${color}"></span>`;
            const sq   = (color) => `<span class="legend-sq"   style="background:${color}"></span>`;

            div.innerHTML =
                `<div class="leg-title">FACILITY (BSL LEVEL)</div>` +
                [['BSL-4', '#c0392b'], ['BSL-3', '#e67e22'],
                 ['BSL-2', '#f39c12'], ['BSL-1', '#27ae60'],
                 ['Unknown', '#95a5a6']]
                    .map(([l, c]) => `<div>${dot(c)}${l}</div>`)
                    .join('') +
                `<div class="leg-title" style="margin-top:8px">FORM A1 RATE</div>` +
                [['>80%', '#08519c'], ['60–80%', '#2171b5'],
                 ['40–60%', '#4292c6'], ['20–40%', '#9ecae1'],
                 ['1–20%',  '#deebf7'], ['None', '#f5f5f5']]
                    .map(([l, c]) => `<div>${sq(c)}${l}</div>`)
                    .join('');
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
                &nbsp;·&nbsp;
                ${c.facility_count} facilit${c.facility_count !== 1 ? 'ies' : 'y'}
                ${c.bsl4_count
                    ? `&nbsp;·&nbsp; <span style="color:#c0392b">${c.bsl4_count} BSL-4</span>`
                    : ''}
            </div>
        </div>`
    ).join('');

    document.getElementById('country-list').innerHTML = html || '<div class="side-placeholder">No data</div>';
}

// ── Country selection ──────────────────────────────────────────────────────
async function selectCountry(iso3) {
    // Highlight list item
    document.querySelectorAll('.country-item').forEach(el =>
        el.classList.toggle('active', el.dataset.iso3 === iso3)
    );

    showPanel('detail');
    document.getElementById('detail-title').textContent = iso3;
    document.getElementById('detail-loading').style.display  = 'block';
    document.getElementById('detail-content').style.display  = 'none';

    try {
        const data = await api(`/api/country/${iso3}`);
        renderCountryDetail(data);

        // Zoom map to this country's geocoded facilities
        if (facilityLayer) {
            const pts = [];
            facilityLayer.eachLayer(layer => {
                if (layer.feature.properties.country_iso3 === iso3) {
                    pts.push(layer.getLatLng());
                }
            });
            if (pts.length > 0) {
                map.fitBounds(L.latLngBounds(pts), { padding: [60, 60], maxZoom: 8 });
            }
        }
    } catch (e) {
        document.getElementById('detail-loading').innerHTML =
            `<span style="color:#c0392b">Error loading data.</span>`;
        console.error(e);
    }
}

function renderCountryDetail(data) {
    document.getElementById('detail-loading').style.display  = 'none';
    document.getElementById('detail-content').style.display  = 'block';
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

    // Group by year
    const byYear = {};
    compliance.forEach(r => {
        if (!byYear[r.year]) byYear[r.year] = {};
        byYear[r.year][r.form] = r.status;
    });
    const years = Object.keys(byYear).map(Number).sort((a, b) => b - a);

    const badge = s =>
        s === 'substantive'      ? '<span class="badge-sub" title="Substantive">●</span>'   :
        s === 'nothing_to_declare' ? '<span class="badge-ntd" title="Nothing to declare">○</span>' :
                                   '<span class="badge-abs" title="Absent">·</span>';

    let html =
        `<table><thead><tr>` +
        `<th class="yr-col">Year</th>` +
        FORMS.map(f => `<th>${f}</th>`).join('') +
        `</tr></thead><tbody>`;

    years.forEach(yr => {
        html += `<tr><td class="yr-col">${yr}</td>`;
        FORMS.forEach(f => {
            const s = byYear[yr][f];
            html += `<td>${s ? badge(s) : ''}</td>`;
        });
        html += '</tr>';
    });

    html += '</tbody></table>';
    el.innerHTML = html;
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
                        ? `<span style="color:${bslColor(f.latest_containment)}">${esc(f.latest_containment)}</span>
                           &nbsp;·&nbsp; `
                        : ''}
                    ${yrs}
                </div>
            </div>`;
    }).join('');
}

// ── Entity modal ───────────────────────────────────────────────────────────
async function showEntityModal(entityId) {
    map.closePopup();
    document.getElementById('modal-title').textContent = 'Loading…';
    document.getElementById('modal-body').innerHTML =
        '<div class="text-center py-4 text-muted">Loading…</div>';
    entityModal.show();

    try {
        const data = await api(`/api/entity/${entityId}`);
        renderEntityModal(data);
    } catch (e) {
        document.getElementById('modal-body').innerHTML =
            '<div class="text-danger">Error loading facility data.</div>';
        console.error(e);
    }
}

function renderEntityModal(data) {
    document.getElementById('modal-title').textContent =
        data.canonical_name || '[Unnamed facility]';

    let html = `
        <div class="text-muted small mb-3">
            <strong>${esc(data.country_name || data.country_iso3)}</strong>
            &nbsp;·&nbsp; ID: <code>${esc(data.canonical_facility_id)}</code>
            ${data.latest_containment
                ? `&nbsp;·&nbsp; <span style="color:${bslColor(data.latest_containment)}">${esc(data.latest_containment)}</span>`
                : ''}
        </div>`;

    if (data.all_names && data.all_names.length > 1) {
        html += `<div class="mb-3">
            <small class="text-muted">
                <strong>Also known as:</strong> ${data.all_names.map(esc).join('; ')}
            </small>
        </div>`;
    }

    if (!data.year_records || data.year_records.length === 0) {
        html += '<div class="text-muted">No year records found.</div>';
    } else {
        html += data.year_records.map(yr => {
            const bsl4 = yr.has_bsl4 != null
                ? (yr.has_bsl4 ? `Yes${yr.bsl4_area_m2 ? ` (${yr.bsl4_area_m2} m²)` : ''}` : 'No')
                : null;
            const bsl3 = yr.has_bsl3 != null
                ? (yr.has_bsl3 ? `Yes${yr.bsl3_area_m2 ? ` (${yr.bsl3_area_m2} m²)` : ''}` : 'No')
                : null;

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

            const conf = yr.confidence != null
                ? `<small class="text-muted fw-normal ms-2">confidence ${Math.round(yr.confidence * 100)}%</small>`
                : '';
            const gc = yr.geocode_confidence
                ? `<small class="text-muted fw-normal ms-2">geocode: ${yr.geocode_confidence}</small>`
                : '';

            return `
                <div class="year-record">
                    <div class="yr-head">
                        ${yr.year}
                        <small class="text-muted fw-normal ms-2">
                            <a href="https://bwc-cbm.un.org" target="_blank" style="color:#999">${esc(yr.document_id)}</a>
                        </small>
                        ${conf}${gc}
                    </div>
                    <dl class="yr-kv">
                        ${kvs.map(([k, v]) =>
                            `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`
                        ).join('')}
                    </dl>
                </div>`;
        }).join('');
    }

    document.getElementById('modal-body').innerHTML = html;
}

// ── Search ─────────────────────────────────────────────────────────────────
function initSearch() {
    const input   = document.getElementById('search-input');
    const results = document.getElementById('search-results');

    input.addEventListener('input', () => {
        clearTimeout(searchTimer);
        const q = input.value.trim();
        if (q.length < 2) {
            results.classList.remove('open');
            return;
        }
        searchTimer = setTimeout(() => doSearch(q), 300);
    });

    // Close on blur (delayed so click on result fires first)
    input.addEventListener('blur', () =>
        setTimeout(() => results.classList.remove('open'), 200)
    );

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            results.classList.remove('open');
            input.blur();
        }
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
    } catch (e) {
        console.error('Search error:', e);
    }
}

async function selectSearchResult(entityId, iso3) {
    document.getElementById('search-results').classList.remove('open');
    document.getElementById('search-input').value = '';
    await selectCountry(iso3);
    showEntityModal(entityId);
}

// ── Utility ────────────────────────────────────────────────────────────────
function esc(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
