// Basemap: real OSM-derived raster tiles, so the network can be checked against
// actual Basel geography. Swap the tile URL here to use a different provider.
const BASEMAP = {
  version: 8,
  sources: {
    basemap: {
      type: 'raster',
      tiles: ['a', 'b', 'c'].map(s => `https://${s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png`),
      tileSize: 256,
      maxzoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, © <a href="https://carto.com/attributions">CARTO</a>, services © <a href="https://data.bs.ch/">data.bs.ch</a>'
    }
  },
  layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }]
};

// The corridor line approximates a 30 m buffer around each reachable segment.
// Width is in pixels, so it scales with zoom like real metres would.
const CORRIDOR_WIDTH = ['interpolate', ['exponential', 2], ['zoom'], 12, 2.5, 17, 80];
const EMPTY = { type: 'FeatureCollection', features: [] };

const map = new maplibregl.Map({ container: 'map', style: BASEMAP, center: [7.5895, 47.557], zoom: 13 });
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }));

let minutes = 15;
let mode = 'walk';
let origin = null;
let health = null;
let categories = [];            // [{category,label,color,essential,count}]
let modes = [];                 // [{mode,label,color,available,default_speed_kmh}]
let enabled = new Set();        // categories currently drawn
let lastResult = null;
let reachableIds = [];

const MODE_COLORS = { walk: '#5cb3ff', bike: '#4ee6c0', transit: '#c792ea' };
const NETWORK_LEGEND = {
  walk: 'reachable walking network',
  bike: 'reachable cycling network',
  transit: 'walking reach · transit rides taken'
};
const currentColor = () => MODE_COLORS[mode] || MODE_COLORS.walk;
const departureValue = () => ($('departure').value || '').trim() || undefined;

const $ = id => document.getElementById(id);
const get = async url => {
  const response = await fetch(url);
  const body = await response.json().catch(() => ({ message: response.statusText }));
  if (!response.ok) throw new Error(body.message || body.detail || 'Request failed');
  return body;
};
const escapeHtml = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const escapeAttr = s => String(s ?? '').replace(/['\\]/g, '');
const num = n => Number(n).toLocaleString('en-US');

function colorExpression() {
  const stops = [];
  categories.forEach(c => stops.push(c.category, c.color));
  return ['match', ['get', 'category'], ...stops, '#8d98a5'];
}
const categoryFilter = () => ['in', ['get', 'category'], ['literal', [...enabled]]];
const reachableFilter = () => ['all', categoryFilter(), ['in', ['get', 'id'], ['literal', reachableIds]]];
const dimFilter = () => ['all', categoryFilter(), ['!', ['in', ['get', 'id'], ['literal', reachableIds]]]];

function refreshServiceLayers() {
  if (!map.getLayer('services-dim')) return;
  map.setFilter('services-dim', dimFilter());
  map.setFilter('services-reachable', reachableFilter());
}

// --- map setup ---------------------------------------------------------------
map.on('load', async () => {
  map.addSource('accessibility', { type: 'geojson', data: EMPTY });
  map.addSource('route', { type: 'geojson', data: EMPTY });
  map.addSource('origin', { type: 'geojson', data: EMPTY });

  map.addLayer({
    id: 'straight-line', type: 'line', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'straight_line_radius'],
    layout: { visibility: 'none' },
    paint: { 'line-color': '#ffb454', 'line-width': 1.5, 'line-dasharray': [3, 3], 'line-opacity': .9 }
  });
  map.addLayer({
    id: 'access-corridor', type: 'line', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'reachable_edge'],
    paint: { 'line-color': MODE_COLORS.walk, 'line-opacity': .16, 'line-width': CORRIDOR_WIDTH, 'line-blur': 2 }
  });
  map.addLayer({
    id: 'access-streets', type: 'line', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'reachable_edge'],
    paint: { 'line-color': MODE_COLORS.walk, 'line-width': 1.8, 'line-opacity': .85 }
  });
  // Transit: only the rides actually taken, and the stops they reach.
  map.addLayer({
    id: 'transit-segments', type: 'line', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'transit_segment'],
    paint: { 'line-color': MODE_COLORS.transit, 'line-width': 2.6, 'line-opacity': .85 }
  });
  map.addLayer({
    id: 'transit-stops', type: 'circle', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'transit_stop'],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2.5, 16, 5],
      'circle-color': '#0d0f12', 'circle-stroke-width': 2,
      'circle-stroke-color': MODE_COLORS.transit
    }
  });

  await addEntityLayer('areas', 'fill', '#8a9bff');
  await addEntityLayer('accidents', 'accident', '#8b3a3a');
  map.setLayoutProperty('accidents', 'visibility', 'none');

  health = await get('/health');
  categories = health.categories || [];
  modes = (health.modes || []).filter(m => m.available);
  enabled = new Set(categories.filter(c => c.essential).map(c => c.category));
  renderModes();
  initDeparture();

  const services = await get('/services/geojson');
  map.addSource('services', { type: 'geojson', data: services });
  map.addLayer({
    id: 'services-dim', type: 'circle', source: 'services', filter: dimFilter(),
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 1.6, 16, 3.5],
      'circle-color': colorExpression(), 'circle-opacity': .3
    }
  });
  map.addLayer({
    id: 'services-reachable', type: 'circle', source: 'services', filter: reachableFilter(),
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 3.5, 16, 7],
      'circle-color': colorExpression(), 'circle-opacity': .95,
      'circle-stroke-width': 1.2, 'circle-stroke-color': '#0d0f12'
    }
  });

  // Route to a selected service, drawn above the reachable network.
  map.addLayer({
    id: 'route-line', type: 'line', source: 'route',
    filter: ['in', ['get', 'kind'], ['literal', ['route', 'walk_leg', 'walk_leg_final']]],
    paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-opacity': .95 }
  });
  map.addLayer({
    id: 'route-transit', type: 'line', source: 'route',
    filter: ['==', ['get', 'kind'], 'transit_leg'],
    paint: { 'line-color': MODE_COLORS.transit, 'line-width': 4, 'line-opacity': .95 }
  });
  map.addLayer({
    id: 'route-connector', type: 'line', source: 'route',
    filter: ['in', ['get', 'kind'], ['literal', ['route_connector', 'transfer_leg']]],
    paint: { 'line-color': '#ffffff', 'line-width': 1.5, 'line-dasharray': [2, 2], 'line-opacity': .7 }
  });

  map.addLayer({
    id: 'origin-snapped', type: 'circle', source: 'origin',
    filter: ['==', ['get', 'role'], 'snapped'],
    paint: { 'circle-radius': 5, 'circle-color': '#5cb3ff', 'circle-stroke-width': 2, 'circle-stroke-color': '#0d0f12' }
  });
  map.addLayer({
    id: 'origin-click', type: 'circle', source: 'origin',
    filter: ['==', ['get', 'role'], 'click'],
    paint: { 'circle-radius': 7, 'circle-color': '#fff', 'circle-stroke-color': '#2368d8', 'circle-stroke-width': 3 }
  });

  ['services-reachable', 'services-dim'].forEach(layer => {
    map.on('click', layer, event => selectService(event.features[0].properties.id));
    map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
  });

  renderBadges(health);
  renderToggles();
  renderDataCard();
  if (health.map) map.jumpTo({ center: health.map.center, zoom: health.map.zoom });
});

async function addEntityLayer(kind, style, color) {
  const rows = await get('/entities/' + kind);
  const data = {
    type: 'FeatureCollection',
    features: rows.map(r => ({ type: 'Feature', geometry: r.geometry, properties: { id: r.id, name: r.name, type: r.type } }))
  };
  map.addSource(kind, { type: 'geojson', data });
  if (style === 'fill') {
    map.addLayer({ id: kind, type: 'line', source: kind, paint: { 'line-color': color, 'line-width': 1, 'line-opacity': .3 } });
  } else {
    map.addLayer({ id: kind, type: 'circle', source: kind, paint: { 'circle-radius': 2.5, 'circle-color': color, 'circle-opacity': .6 } });
  }
  map.on('click', kind, e => inspect(e.features[0].properties.id));
}

// --- controls ----------------------------------------------------------------
function renderBadges(h) {
  // Three states, not two: real-but-frozen is neither `live` nor `fixture`.
  const set = (id, label, block) => {
    const el = $(id);
    const state = (block.data_state || {}).state || block.mode;
    const text = state === 'frozen' ? 'frozen' : state === 'local' ? 'live' : state;
    el.textContent = `${label}: ${text}`;
    el.className = 'badge ' + (state === 'fixture' ? 'fixture' : state === 'frozen' ? 'frozen' : 'live');
    el.title = block.fallback_reason || (block.data_state || {}).explanation || '';
  };
  set('badge-streets', 'streets', h.streets);
  set('badge-bike', 'bike', h.bike);
  set('badge-transit', 'transit', h.transit);
  set('badge-services', 'services', h.services);
  const snapshot = h.snapshot || {};
  const note = $('badge-snapshot');
  if (note) {
    note.textContent = snapshot.is_frozen_snapshot
      ? `snapshot: ${snapshot.snapshot_id || 'frozen'}` : `data: ${snapshot.label || 'local'}`;
    note.className = 'badge ' + (snapshot.is_frozen_snapshot ? 'frozen' : '');
    note.title = [snapshot.note, snapshot.valid_until
      ? `Timetable valid to ${snapshot.valid_until}.` : '',
      `Refresh: ${snapshot.refresh_command || 'python -m app.prepare_data'}`]
      .filter(Boolean).join(' ');
  }
}

function renderModes() {
  $('mode-controls').innerHTML = modes.map(m =>
    `<button data-mode="${escapeAttr(m.mode)}" style="--mode:${escapeAttr(m.color)}"
             class="${m.mode === mode ? 'active' : ''}"
             title="${escapeHtml(m.label)}${m.default_speed_kmh ? ` · ${m.default_speed_kmh} km/h` : ''}">
       ${escapeHtml(m.label)}</button>`).join('');
  document.querySelectorAll('[data-mode]').forEach(button => button.onclick = () => {
    mode = button.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach(other =>
      other.classList.toggle('active', other === button));
    applyModeStyling();
    if (origin) calculate();
  });
  applyModeStyling();
}

function applyModeStyling() {
  const color = currentColor();
  ['access-corridor', 'access-streets'].forEach(layer => {
    if (map.getLayer(layer)) map.setPaintProperty(layer, 'line-color', color);
  });
  const swatch = $('network-swatch');
  if (swatch) swatch.style.borderTopColor = color;
  $('network-legend').textContent = NETWORK_LEGEND[mode] || NETWORK_LEGEND.walk;
  $('departure-row').hidden = mode !== 'transit';
}

function initDeparture() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  $('departure').value = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  $('departure').onchange = () => { if (origin && mode === 'transit') calculate(); };
}

function renderToggles() {
  $('toggles').innerHTML = categories.map(c => `
    <label>
      <input type="checkbox" data-category="${escapeAttr(c.category)}" ${enabled.has(c.category) ? 'checked' : ''}>
      <i class="dot" style="background:${escapeAttr(c.color)}"></i>
      <span>${escapeHtml(c.label)}</span>
      <span class="muted small">${num(c.count)}</span>
    </label>`).join('');
  document.querySelectorAll('[data-category]').forEach(box => box.onchange = () => {
    box.checked ? enabled.add(box.dataset.category) : enabled.delete(box.dataset.category);
    refreshServiceLayers();
    if (lastResult) renderProfile(lastResult);
  });
}

document.querySelectorAll('[data-min]').forEach(button => button.onclick = () => {
  minutes = Number(button.dataset.min);
  document.querySelectorAll('[data-min]').forEach(other => other.classList.toggle('active', other === button));
  if (origin) calculate();
});

document.querySelectorAll('[data-lat]').forEach(button => button.onclick = () => {
  origin = { lat: Number(button.dataset.lat), lon: Number(button.dataset.lon) };
  map.easeTo({ center: [origin.lon, origin.lat], zoom: Math.max(map.getZoom(), 13.8) });
  calculate();
});

$('straight-line').onchange = e =>
  map.setLayoutProperty('straight-line', 'visibility', e.target.checked ? 'visible' : 'none');
$('show-accidents').onchange = e =>
  map.setLayoutProperty('accidents', 'visibility', e.target.checked ? 'visible' : 'none');

// A click on a point-of-interest selects it; a click on empty map sets the
// origin. Querying the rendered features is order-independent, unlike relying
// on which listener happened to be registered first.
const BLOCKING_LAYERS = ['services-reachable', 'services-dim', 'accidents'];

map.on('click', e => {
  const layers = BLOCKING_LAYERS.filter(id => map.getLayer(id));
  if (layers.length && map.queryRenderedFeatures(e.point, { layers }).length) return;
  origin = { lon: e.lngLat.lng, lat: e.lngLat.lat };
  calculate();
});

// --- the query ---------------------------------------------------------------
async function calculate() {
  if (!map.getSource('accessibility')) return;   // map still initialising
  $('profile').innerHTML = `<h2>${minutes} minutes from here</h2><p class="sub">Routing…</p>`;
  $('service-detail').innerHTML = '';
  map.getSource('route').setData(EMPTY);
  map.getSource('origin').setData({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [origin.lon, origin.lat] }, properties: { role: 'click' } }]
  });
  try {
    const params = new URLSearchParams({ lat: origin.lat, lon: origin.lon, mode, minutes });
    if (mode === 'transit' && departureValue()) params.set('departure_time', departureValue());
    const result = await get('/accessibility?' + params);
    lastResult = result;
    // `ids` lists every reachable service; `items` only the detailed rows.
    reachableIds = Object.values(result.reachable_services)
      .flatMap(row => row.ids && row.ids.length ? row.ids : row.items.map(i => i.id));
    map.getSource('accessibility').setData(result.geometry);
    map.getSource('origin').setData({
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: { type: 'Point', coordinates: [result.snapped_origin.lon, result.snapped_origin.lat] }, properties: { role: 'snapped' } },
        { type: 'Feature', geometry: { type: 'Point', coordinates: [result.origin.lon, result.origin.lat] }, properties: { role: 'click' } }
      ]
    });
    refreshServiceLayers();
    renderProfile(result);
    renderNetworkCard(result);
    if ($('compare-details').open) renderComparison();
  } catch (error) {
    lastResult = null;
    reachableIds = [];
    refreshServiceLayers();
    map.getSource('accessibility').setData(EMPTY);
    $('profile').innerHTML = `<h2>${minutes} minutes from here</h2><p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderProfile(result) {
  const rows = categories
    .map(c => ({ meta: c, row: result.reachable_services[c.category] }))
    .filter(entry => entry.row);
  const max = Math.max(1, ...rows.filter(e => enabled.has(e.meta.category)).map(e => e.row.count));
  const list = rows.map(({ meta, row }) => {
    const on = enabled.has(meta.category);
    const width = on ? Math.round((row.count / max) * 100) : 0;
    const nearest = row.count ? `nearest ${row.nearest_minutes} min` : '—';
    return `<div class="prow ${row.count ? '' : 'empty'} ${on ? '' : 'off'}" style="color:${escapeAttr(meta.color)}"
                 onclick="focusCategory('${escapeAttr(meta.category)}')" title="${escapeHtml(row.label)}: ${row.count} reachable of ${num(row.prepared_total)} prepared">
      <i class="bar" style="width:${width}%"></i>
      <i class="dot"></i>
      <span class="nm">${escapeHtml(row.label)}</span>
      <span class="ct">${num(row.count)}</span>
      <span class="nr">${nearest}</span>
    </div>`;
  }).join('');

  const c = result.completeness;
  const marks = c.essential_categories.map(id => {
    const meta = categories.find(x => x.category === id) || { label: id };
    const ok = c.reachable_categories.includes(id);
    return `<span class="${ok ? 'yes' : 'no'}">${ok ? '✓' : '✗'} ${escapeHtml(meta.label)}</span>`;
  }).join('');

  const modeMeta = modes.find(m => m.mode === result.mode) || { label: result.mode_label || result.mode };
  const how = result.mode === 'transit'
    ? `leaving ${escapeHtml((result.departure_time || '').slice(11, 16))} · ${escapeHtml(result.service_date || '')} · max ${result.max_transfers} transfer${result.max_transfers === 1 ? '' : 's'}`
    : `${result.speed_kmh ?? result.walking_speed_kmh} km/h along the ${result.mode === 'bike' ? 'cycling' : 'walking'} network`;
  $('profile').innerHTML = `
    <h2>${result.minutes} minutes from here</h2>
    <p class="sub"><b style="color:${escapeAttr(currentColor())}">${escapeHtml(modeMeta.label)}</b> ·
       ${result.origin.lat.toFixed(5)}, ${result.origin.lon.toFixed(5)} · ${how}</p>
    ${list}
    <div style="border-top:1px solid #252b31;margin:12px 0 10px"></div>
    <div class="complete">${marks}</div>
    <div class="score"><b>${c.reachable_count} / ${c.total}</b> essential categories reachable</div>
    <details style="margin-top:8px">
      <summary class="small">${escapeHtml(c.label)} — what this means</summary>
      <p class="muted small" style="margin:6px 0 0">${escapeHtml(c.definition)}</p>
    </details>`;
}

function renderNetworkCard(result) {
  const p = result.provenance || {};
  const rows = [
    ['Mode', escapeHtml(result.mode_label || result.mode)],
    ['Snapped to network', `${num(result.snapped_origin.snap_distance_m)} m`],
    ['Routing', escapeHtml(p.routing_method || '')],
    ['Network source', escapeHtml(p.network_source || '')],
  ];
  if (result.mode === 'transit') {
    const t = result.transit || {};
    rows.push(
      ['Departure', escapeHtml((result.departure_time || '').replace('T', ' '))],
      ['Service date', escapeHtml(result.service_date || '')],
      ['Max transfers', result.max_transfers],
      ['Stops in walking range', num(t.stops_in_walking_range || 0)],
      ['Stops reached', num(t.stops_reached || 0)],
      ['Reached by vehicle', num(t.stops_reached_by_vehicle || 0)],
      ['Routes used', (t.routes_used || []).length],
      ['Walk-only nodes', num(result.network.walk_only_node_count || 0)],
      ['Timetable', escapeHtml((p.transit || {}).feed_version || (p.transit || {}).source || '')],
    );
  } else {
    rows.push(
      ['Speed', `${result.speed_kmh ?? result.walking_speed_kmh} km/h`],
      ['Distance budget', `${num(result.network.distance_budget_m)} m`],
      ['Reachable nodes', num(result.network.reachable_node_count)],
      ['Reachable edges', num(result.network.reachable_edge_count)],
      ['Reachable network', `${(result.network.reachable_edge_length_m / 1000).toFixed(1)} km`],
    );
    if (result.reachable_entities) {
      rows.push(['Neighbourhoods touched', result.reachable_entities.areas.length],
                ['Accidents in reach', num(result.reachable_entities.accident_count)]);
    }
  }
  const detours = (result.euclidean_vs_network || []).map(row =>
    `<div class="metric"><span>${escapeHtml(row.label)} detour</span><b>${row.network_detour_factor}×</b></div>`).join('');
  const routes = result.mode === 'transit' && (result.transit || {}).routes_used
    ? `<p class="muted small" style="margin:8px 0 0">${result.transit.routes_used.map(r => escapeHtml(r.label)).join(' · ')}</p>`
    : '';
  const notes = (result.notes || []).map(n => `<p class="muted small">⚠︎ ${escapeHtml(n)}</p>`).join('');
  $('network-card').innerHTML =
    rows.map(([k, v]) => `<div class="metric"><span>${k}</span><b>${v}</b></div>`).join('') +
    detours + routes + notes;
}

// --- mode comparison ---------------------------------------------------------
$('compare-details').addEventListener('toggle', () => {
  if ($('compare-details').open) renderComparison();
});

async function renderComparison() {
  if (!origin) return;
  const card = $('compare-card');
  card.innerHTML = '<p class="muted small">Comparing…</p>';
  try {
    const params = new URLSearchParams({ lat: origin.lat, lon: origin.lon, minutes });
    if (departureValue()) params.set('departure_time', departureValue());
    const data = await get('/accessibility/compare?' + params);
    const keys = Object.keys(data.modes);
    const head = keys.map(k => `<th style="color:${escapeAttr(data.modes[k].color)}">${escapeHtml(data.modes[k].label)}</th>`).join('');
    const body = categories.filter(c => c.essential).map(c => {
      const cells = keys.map(k => `<td>${num(data.table[c.category]?.[k] ?? 0)}</td>`).join('');
      return `<tr><td>${escapeHtml(c.label)}</td>${cells}</tr>`;
    }).join('');
    const complete = keys.map(k =>
      `<td>${data.modes[k].completeness.reachable_count}/${data.modes[k].completeness.total}</td>`).join('');
    card.innerHTML = `<table class="compare"><thead><tr><th>${minutes} min</th>${head}</tr></thead>
      <tbody>${body}<tr class="total"><td>categories reachable</td>${complete}</tr></tbody></table>
      <p class="muted small" style="margin:8px 0 0">Counts of reachable services per essential category.
      Transit leaves at ${escapeHtml((data.modes.transit || {}).departure_time?.slice(11, 16) || '—')}.</p>`;
  } catch (error) {
    card.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

async function renderDataCard() {
  try {
    const status = await get('/data/status');
    const rows = Object.entries(status.services.by_category || {}).map(([name, row]) =>
      `<div class="metric"><span>${escapeHtml(name)}</span><b>${num(row.count)} <span class="muted small">${escapeHtml(row.sources.join(', '))}</span></b></div>`).join('');
    const warnings = (status.warnings || []).map(w => `<div class="muted small">• ${escapeHtml(w)}</div>`).join('');
    $('data-card').innerHTML = `
      <div class="metric"><span>Walking network</span><b>${escapeHtml(status.network.mode)} · ${escapeHtml(status.network.source)}</b></div>
      <div class="metric"><span>Basel entities</span><b>${escapeHtml(status.entities.mode)} · ${escapeHtml(status.entities.source)}</b></div>
      <div class="metric"><span>Services</span><b>${escapeHtml(status.services.mode)} · ${num(status.services.total)}</b></div>
      ${rows}
      <details style="margin-top:8px"><summary class="small">${status.warning_count} data-quality warning(s)</summary>
      <div style="margin-top:6px">${warnings || '<span class="muted small">none</span>'}</div></details>
      <p class="muted small" style="margin:8px 0 0">Prepared ${escapeHtml(status.generated_at || 'unknown')} ·
      full report at <a href="/data/status">/data/status</a></p>`;
  } catch (error) {
    $('data-card').innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

// --- selection ---------------------------------------------------------------
function focusCategory(category) {
  if (!lastResult) return;
  const row = lastResult.reachable_services[category];
  if (!row || !row.nearest_id) return;
  selectService(row.nearest_id);
}

async function selectService(serviceId) {
  const reached = lastResult
    ? Object.values(lastResult.reachable_services).flatMap(r => r.items || []).find(i => i.id === serviceId)
    : null;
  let service = reached;
  if (!service) {
    try {
      const [, category] = serviceId.split(':');
      service = await get(`/services/${category}/${encodeURIComponent(serviceId)}`);
    } catch (error) {
      $('service-detail').innerHTML = `<div class="card"><p class="error">${escapeHtml(error.message)}</p></div>`;
      return;
    }
  }
  const meta = categories.find(c => c.category === service.category) || {};
  const provenance = service.provenance || {};
  const link = provenance.source_url
    ? `<a href="${escapeAttr(provenance.source_url)}" target="_blank" rel="noopener">${escapeHtml(provenance.source)}</a>`
    : escapeHtml(provenance.source);
  const travelMinutes = reached ? (reached.travel_time_minutes ?? reached.walking_time_minutes) : null;
  const distance = reached ? (reached.travel_distance_m ?? reached.walking_distance_m) : null;
  const timing = reached
    ? `<div class="metric"><span>Travel time</span><b>${travelMinutes} min</b></div>` +
      (distance != null ? `<div class="metric"><span>Network distance</span><b>${num(distance)} m</b></div>` : '')
    : `<p class="muted small">Not within the current ${minutes}-minute budget${origin ? '' : ' (no origin selected yet)'}.</p>`;
  const attributes = Object.entries(service.attributes || {})
    .map(([k, v]) => `<div class="metric"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');

  $('service-detail').innerHTML = `<div class="card" style="border-color:${escapeAttr(meta.color || '#292f36')}">
    <b>${escapeHtml(service.display_name)}</b>
    <p class="muted small" style="margin:-4px 0 8px">${escapeHtml(service.category_label || service.category)}</p>
    ${timing}
    <div class="metric"><span>Snapped to street</span><b>${service.access.snap_distance_m ?? '—'} m <span class="muted small">${escapeHtml(service.access.quality)}</span></b></div>
    <div class="metric"><span>Source</span><b>${link}</b></div>
    <div class="metric"><span>Dataset</span><b class="small">${escapeHtml(provenance.dataset)}</b></div>
    <div class="metric"><span>Retrieved</span><b class="small">${escapeHtml(provenance.retrieved_at || '—')}</b></div>
    ${attributes}
    <div id="itinerary"></div>
  </div>`;

  if (!origin) return;
  try {
    const route = await get(routeUrl(serviceId));
    map.getSource('route').setData(route.geometry);
    if (route.journey) $('itinerary').innerHTML = renderItinerary(route);
  } catch (error) {
    map.getSource('route').setData(EMPTY);
    $('itinerary').innerHTML = `<p class="muted small">${escapeHtml(error.message)}</p>`;
  }
}

function routeUrl(serviceId) {
  const params = new URLSearchParams({ lat: origin.lat, lon: origin.lon, service_id: serviceId });
  if (mode === 'transit') {
    if (departureValue()) params.set('departure_time', departureValue());
    params.set('minutes', Math.max(60, minutes));
    return '/accessibility/transit/route?' + params;
  }
  return `/accessibility/${mode}/route?` + params;
}

// A journey a person can read: walk, board, wait, ride, exit, walk.
function renderItinerary(route) {
  const j = route.journey;
  if (!j) return '';
  if (!j.uses_transit) {
    return `<p class="muted small" style="margin:10px 0 0">${j.total_minutes} min on foot — no transit needed.</p>`;
  }
  const steps = j.steps.map(step => {
    switch (step.kind) {
      case 'walk':
        return `<div class="step"><b>Walk</b> ${step.minutes} min <span class="when">${escapeHtml(step.detail || '')}</span></div>`;
      case 'transfer_walk':
        return `<div class="step"><b>Walk</b> ${step.minutes} min <span class="when">${escapeHtml(step.from)} → ${escapeHtml(step.to)}</span></div>`;
      case 'board':
        return `<div class="step board"><b>Board</b> ${escapeHtml(step.stop)}<br>
                <span class="when">${escapeHtml(step.route)}${step.headsign ? ' → ' + escapeHtml(step.headsign) : ''} · ${escapeHtml((step.departure || '').slice(0, 5))}</span></div>`;
      case 'wait':
        return `<div class="step"><b>Wait</b> ${step.minutes} min</div>`;
      case 'ride':
        return `<div class="step ride"><b>Ride</b> ${escapeHtml(step.route)} · ${step.minutes} min <span class="when">${step.stops} stop${step.stops === 1 ? '' : 's'}</span></div>`;
      case 'exit':
        return `<div class="step exit"><b>Exit</b> ${escapeHtml(step.stop)} <span class="when">${escapeHtml((step.arrival || '').slice(0, 5))}</span></div>`;
      default:
        return '';
    }
  }).join('');
  return `<div class="metric" style="margin-top:10px"><span>Journey</span>
      <b>${j.total_minutes} min · ${j.transfers} transfer${j.transfers === 1 ? '' : 's'}</b></div>
    <div class="metric"><span>walk / wait / ride</span>
      <b>${j.walking_minutes} / ${j.waiting_minutes} / ${j.transit_minutes} min</b></div>
    <div class="itinerary">${steps}</div>`;
}

async function inspect(id) {
  const graph = await get('/graph/neighbors/' + encodeURIComponent(id));
  const node = graph.nodes.find(n => n.id === id);
  const edges = graph.edges.map(e =>
    `<div class="edge"><b>${escapeHtml(e.type)}</b><br><span class="muted small">${escapeHtml(e.source)} → ${escapeHtml(e.target)}${e.distance_m ? ` · ${e.distance_m} m` : ''}</span></div>`
  ).join('') || '<span class="muted">No graph relations</span>';
  $('inspect').innerHTML = `<b>${escapeHtml(node.name || node.id)}</b>
    <p class="muted small">${escapeHtml(node.type)} · ${escapeHtml(node.id)}</p>${edges}
    <details><summary class="small">Provenance &amp; properties</summary>
    <pre>${escapeHtml(JSON.stringify({ provenance: node.provenance, properties: node.properties }, null, 2))}</pre></details>`;
  $('inspect-details').open = true;
}
