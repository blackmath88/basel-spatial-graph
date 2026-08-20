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
let origin = null;
let health = null;
let categories = [];            // [{category,label,color,essential,count}]
let enabled = new Set();        // categories currently drawn
let lastResult = null;
let reachableIds = [];

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
    paint: { 'line-color': '#3d8bfd', 'line-opacity': .18, 'line-width': CORRIDOR_WIDTH, 'line-blur': 2 }
  });
  map.addLayer({
    id: 'access-streets', type: 'line', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'reachable_edge'],
    paint: { 'line-color': '#5cb3ff', 'line-width': 1.8, 'line-opacity': .85 }
  });

  await addEntityLayer('areas', 'fill', '#8a9bff');
  await addEntityLayer('accidents', 'accident', '#8b3a3a');
  map.setLayoutProperty('accidents', 'visibility', 'none');

  health = await get('/health');
  categories = health.categories || [];
  enabled = new Set(categories.filter(c => c.essential).map(c => c.category));

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
    filter: ['==', ['get', 'kind'], 'route'],
    paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-opacity': .95 }
  });
  map.addLayer({
    id: 'route-connector', type: 'line', source: 'route',
    filter: ['==', ['get', 'kind'], 'route_connector'],
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
  const set = (id, label, mode, reason) => {
    const el = $(id);
    el.textContent = `${label}: ${mode}`;
    el.className = 'badge ' + (mode === 'live' ? 'live' : 'fixture');
    el.title = reason || '';
  };
  set('badge-streets', 'streets', h.streets.mode, h.streets.fallback_reason);
  set('badge-services', 'services', h.services.mode, h.services.fallback_reason);
  set('badge-entities', 'entities', h.entities.mode, h.entities.fallback_reason);
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
  $('profile').innerHTML = `<h2>${minutes} minutes from here</h2><p class="sub">Routing through the network…</p>`;
  $('service-detail').innerHTML = '';
  map.getSource('route').setData(EMPTY);
  map.getSource('origin').setData({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [origin.lon, origin.lat] }, properties: { role: 'click' } }]
  });
  try {
    const result = await get(`/accessibility/walk?lat=${origin.lat}&lon=${origin.lon}&minutes=${minutes}`);
    lastResult = result;
    reachableIds = Object.values(result.reachable_services).flatMap(row => row.items.map(i => i.id));
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

  $('profile').innerHTML = `
    <h2>${result.minutes} minutes from here</h2>
    <p class="sub">${result.origin.lat.toFixed(5)}, ${result.origin.lon.toFixed(5)} ·
       ${result.walking_speed_kmh} km/h · along the walking network</p>
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
  const km = (result.network.reachable_edge_length_m / 1000).toFixed(1);
  const detours = result.euclidean_vs_network.map(row =>
    `<div class="metric"><span>${escapeHtml(row.label)} detour</span><b>${row.network_detour_factor}×</b></div>`).join('');
  const notes = (result.notes || []).map(n => `<p class="muted small">⚠︎ ${escapeHtml(n)}</p>`).join('');
  $('network-card').innerHTML = `
    <div class="metric"><span>Snapped to network</span><b>${num(result.snapped_origin.snap_distance_m)} m</b></div>
    <div class="metric"><span>Distance budget</span><b>${num(result.network.distance_budget_m)} m</b></div>
    <div class="metric"><span>Reachable nodes</span><b>${num(result.network.reachable_node_count)}</b></div>
    <div class="metric"><span>Reachable edges</span><b>${num(result.network.reachable_edge_count)}</b></div>
    <div class="metric"><span>Reachable streets</span><b>${km} km</b></div>
    <div class="metric"><span>Neighbourhoods touched</span><b>${result.reachable_entities.areas.length}</b></div>
    <div class="metric"><span>Accidents in reach</span><b>${num(result.reachable_entities.accident_count)}</b></div>
    ${detours}${notes}`;
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
    ? Object.values(lastResult.reachable_services).flatMap(r => r.items).find(i => i.id === serviceId)
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
  const timing = reached
    ? `<div class="metric"><span>Walking time</span><b>${reached.walking_time_minutes} min</b></div>
       <div class="metric"><span>Network distance</span><b>${num(reached.walking_distance_m)} m</b></div>`
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
  </div>`;

  if (!origin) return;
  try {
    const route = await get(`/accessibility/walk/route?lat=${origin.lat}&lon=${origin.lon}&service_id=${encodeURIComponent(serviceId)}`);
    map.getSource('route').setData(route.geometry);
  } catch (error) {
    map.getSource('route').setData(EMPTY);
  }
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
