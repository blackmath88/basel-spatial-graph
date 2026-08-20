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
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, © <a href="https://carto.com/attributions">CARTO</a>'
    }
  },
  layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }]
};

// The corridor line approximates a 30 m buffer around each reachable segment.
// Width is in pixels, so it scales with zoom like real metres would.
const CORRIDOR_WIDTH = ['interpolate', ['exponential', 2], ['zoom'], 12, 2.5, 17, 80];

const EMPTY = { type: 'FeatureCollection', features: [] };
const map = new maplibregl.Map({ container: 'map', style: BASEMAP, center: [7.5895, 47.557], zoom: 13.4 });
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }));

let minutes = 15, origin = null, health = null, showStraightLine = false;

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

function fc(rows) {
  return { type: 'FeatureCollection', features: rows.map(r => ({ type: 'Feature', geometry: r.geometry, properties: { id: r.id, name: r.name, type: r.type } })) };
}

async function addEntityLayer(kind, style, color) {
  const rows = await get('/entities/' + kind);
  map.addSource(kind, { type: 'geojson', data: fc(rows) });
  if (style === 'fill') {
    map.addLayer({ id: kind, type: 'line', source: kind, paint: { 'line-color': color, 'line-width': 1, 'line-opacity': .35 } });
  } else {
    map.addLayer({
      id: kind, type: 'circle', source: kind,
      paint: {
        'circle-radius': style === 'school' ? 6 : 3,
        'circle-color': color,
        'circle-opacity': style === 'school' ? .95 : .5,
        'circle-stroke-width': style === 'school' ? 1 : 0,
        'circle-stroke-color': '#0d0f12'
      }
    });
  }
  map.on('click', kind, e => { e.originalEvent.cancelBubble = true; inspect(e.features[0].properties.id); });
}

map.on('load', async () => {
  map.addSource('accessibility', { type: 'geojson', data: EMPTY });
  map.addSource('origin', { type: 'geojson', data: EMPTY });

  // Straight-line comparison sits below the network so it can never hide it.
  map.addLayer({
    id: 'straight-line', type: 'line', source: 'accessibility',
    filter: ['==', ['get', 'kind'], 'straight_line_radius'],
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
    paint: { 'line-color': '#5cb3ff', 'line-width': 2, 'line-opacity': .95 }
  });

  await addEntityLayer('areas', 'fill', '#8a9bff');
  await addEntityLayer('accidents', 'accident', '#ff5e5e');
  await addEntityLayer('schools', 'school', '#ffca4b');

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

  health = await get('/health');
  renderBadges(health);
  if (health.map) map.jumpTo({ center: health.map.center, zoom: health.map.zoom });
});

function renderBadges(h) {
  const set = (id, label, mode, reason) => {
    const el = $(id);
    el.textContent = `${label}: ${mode}`;
    el.className = 'badge ' + (mode === 'live' ? 'live' : 'fixture');
    el.title = reason || '';
  };
  set('badge-streets', 'streets', h.streets.mode, h.streets.fallback_reason);
  set('badge-entities', 'entities', h.entities.mode, h.entities.fallback_reason);
}

map.on('click', e => {
  if (e.originalEvent.cancelBubble) return;
  origin = { lon: e.lngLat.lng, lat: e.lngLat.lat };
  calculate();
});

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

$('straight-line').onchange = event => {
  showStraightLine = event.target.checked;
  map.setLayoutProperty('straight-line', 'visibility', showStraightLine ? 'visible' : 'none');
};
map.on('load', () => map.setLayoutProperty('straight-line', 'visibility', 'none'));

async function calculate() {
  const box = $('access');
  box.innerHTML = '<b>Walking accessibility</b><p class="muted">Routing through the network…</p>';
  map.getSource('origin').setData({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [origin.lon, origin.lat] }, properties: { role: 'click' } }]
  });
  try {
    const result = await get(`/accessibility/walk?lat=${origin.lat}&lon=${origin.lon}&minutes=${minutes}`);
    map.getSource('accessibility').setData(result.geometry);
    map.getSource('origin').setData({
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: { type: 'Point', coordinates: [result.snapped_origin.lon, result.snapped_origin.lat] }, properties: { role: 'snapped' } },
        { type: 'Feature', geometry: { type: 'Point', coordinates: [result.origin.lon, result.origin.lat] }, properties: { role: 'click' } }
      ]
    });
    box.innerHTML = renderResult(result);
  } catch (error) {
    map.getSource('accessibility').setData(EMPTY);
    box.innerHTML = `<b>Walking accessibility</b><p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderResult(r) {
  const km = (r.network.reachable_edge_length_m / 1000).toFixed(1);
  const schools = r.reachable_entities.schools.map(s =>
    `<div class="school" onclick="inspect('${escapeAttr(s.id)}')"><b>${escapeHtml(s.name)}</b><br>
     <span class="muted">${s.travel_time_minutes} min · ${num(s.network_distance_m)} m along the network</span></div>`
  ).join('') || '<p class="muted">No schools within this budget.</p>';
  const notes = (r.notes || []).map(n => `<p class="muted">⚠︎ ${escapeHtml(n)}</p>`).join('');
  const streetsMode = r.provenance.mode === 'live' ? 'LIVE' : 'FIXTURE';
  const entitiesMode = health && health.entities.mode === 'live' ? 'LIVE' : 'FIXTURE';
  return `<b>Walking accessibility</b>
    <div class="metric"><span>Origin</span><b>${r.origin.lat.toFixed(5)}, ${r.origin.lon.toFixed(5)}</b></div>
    <div class="metric"><span>Snapped to network</span><b>${num(r.snapped_origin.snap_distance_m)} m</b></div>
    <div class="metric"><span>Time</span><b>${r.minutes} min</b></div>
    <div class="metric"><span>Speed</span><b>${r.walking_speed_kmh} km/h</b></div>
    <div class="metric"><span>Distance budget</span><b>${num(r.network.distance_budget_m)} m</b></div>
    <div class="metric"><span>Reachable nodes</span><b>${num(r.network.reachable_node_count)}</b></div>
    <div class="metric"><span>Reachable edges</span><b>${num(r.network.reachable_edge_count)}</b></div>
    <div class="metric"><span>Reachable network length</span><b>${km} km</b></div>
    <div class="metric"><span>Reachable schools</span><b>${r.reachable_entities.school_count}</b></div>
    <div class="metric"><span>Accidents in reach</span><b>${num(r.reachable_entities.accident_count)}</b></div>
    <div class="metric"><span>Neighbourhoods touched</span><b>${r.reachable_entities.areas.length}</b></div>
    <div class="metric"><span>Data · streets</span><b>${streetsMode} · ${escapeHtml(r.provenance.network_source)}</b></div>
    <div class="metric"><span>Data · entities</span><b>${entitiesMode}${health ? ' · ' + escapeHtml(health.entities.source) : ''}</b></div>
    ${notes}
    <p class="muted" style="margin:10px 0 4px">Reachable schools</p>${schools}`;
}

async function inspect(id) {
  const graph = await get('/graph/neighbors/' + encodeURIComponent(id));
  const node = graph.nodes.find(n => n.id === id);
  const edges = graph.edges.map(e =>
    `<div class="edge"><b>${escapeHtml(e.type)}</b><br><span class="muted">${escapeHtml(e.source)} → ${escapeHtml(e.target)}${e.distance_m ? ` · ${e.distance_m} m` : ''}</span></div>`
  ).join('') || '<span class="muted">No graph relations</span>';
  $('inspect').innerHTML = `<b>${escapeHtml(node.name || node.id)}</b>
    <p class="muted">${escapeHtml(node.type)} · ${escapeHtml(node.id)}</p>${edges}
    <details><summary class="muted">Provenance &amp; properties</summary>
    <pre>${escapeHtml(JSON.stringify({ provenance: node.provenance, properties: node.properties }, null, 2))}</pre></details>`;
  if (node.geometry?.type === 'Point') map.easeTo({ center: node.geometry.coordinates, zoom: Math.max(map.getZoom(), 15) });
}
