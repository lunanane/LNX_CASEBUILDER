import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------

const state = {
  parts: [],            // part library from /api/parts
  partsById: new Map(),
  sceneName: null,
  scene: null,          // the Scene document we own and post back
  resolved: null,       // last /api/resolve response
  selection: null,      // placement id
  caseModel: null,
};

const KIND_STYLE = {
  body:     { color: 0x6f7c91, opacity: 0.92 },
  keepout:  { color: 0xff6b6b, opacity: 0.18 },
  cable:    { color: 0xb98cff, opacity: 0.22 },
  actuator: { color: 0xffc24b, opacity: 0.95 },
  display:  { color: 0x57c7ff, opacity: 0.95 },
};

const $ = (id) => document.getElementById(id);
const status = (msg, cls = '') => { const el = $('status'); el.textContent = msg; el.className = cls; };

// ---------------------------------------------------------------------------
// three.js scaffolding -- Z is up, so world coordinates match the engine 1:1
// ---------------------------------------------------------------------------

const viewport = $('viewport');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
viewport.appendChild(renderer.domElement);

const view = new THREE.Scene();
view.background = new THREE.Color(0x14161a);

const camera = new THREE.PerspectiveCamera(45, 1, 1, 5000);
camera.up.set(0, 0, 1);
camera.position.set(220, -280, 260);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.12;

view.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 1.6);
key.position.set(180, -240, 400);
view.add(key);
const fill = new THREE.DirectionalLight(0x88aaff, 0.5);
fill.position.set(-250, 200, 150);
view.add(fill);

const grid = new THREE.GridHelper(1000, 100, 0x3a4150, 0x24282f);
grid.rotation.x = Math.PI / 2;
view.add(grid);

const axes = new THREE.AxesHelper(40);
view.add(axes);

const solidsGroup = new THREE.Group();
const caseGroup = new THREE.Group();
const corridorGroup = new THREE.Group();
view.add(solidsGroup, caseGroup, corridorGroup);

const groupsByPlacement = new Map();   // id -> THREE.Group

function resize() {
  const w = viewport.clientWidth, h = viewport.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewport);

function tick() {
  controls.update();
  renderer.render(view, camera);
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------------------
// api
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${path}: ${text.slice(0, 300)}`);
  }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}

// ---------------------------------------------------------------------------
// drawing the resolved scene
// ---------------------------------------------------------------------------

function shapeFromOutline(points) {
  const shape = new THREE.Shape();
  points.forEach(([x, y], i) => (i ? shape.lineTo(x, y) : shape.moveTo(x, y)));
  return shape;
}

function buildSolids(resolved) {
  solidsGroup.clear();
  groupsByPlacement.clear();

  for (const s of resolved.solids) {
    const [, , z0, , , z1] = s.bounds;
    const depth = Math.max(z1 - z0, 0.05);
    const geom = new THREE.ExtrudeGeometry(shapeFromOutline(s.outline), {
      depth, bevelEnabled: false, curveSegments: 8,
    });
    const style = KIND_STYLE[s.kind] || KIND_STYLE.body;
    const mat = new THREE.MeshLambertMaterial({
      color: style.color,
      transparent: style.opacity < 1,
      opacity: style.opacity,
      depthWrite: style.opacity > 0.5,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.position.z = z0;
    mesh.userData = { placement: s.placement, name: s.name, kind: s.kind };

    if (s.kind === 'body' || s.kind === 'display' || s.kind === 'actuator') {
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geom, 25),
        new THREE.LineBasicMaterial({ color: 0x0f1114, transparent: true, opacity: 0.55 }));
      edges.position.z = z0;
      groupFor(s.placement).add(edges);
    }
    groupFor(s.placement).add(mesh);
  }
  highlightSelection();
}

function groupFor(id) {
  let g = groupsByPlacement.get(id);
  if (!g) {
    g = new THREE.Group();
    g.userData.placement = id;
    groupsByPlacement.set(id, g);
    solidsGroup.add(g);
  }
  return g;
}

function buildCorridors(resolved) {
  corridorGroup.clear();
  if (!$('chk-corridors').checked) return;
  for (const c of resolved.connectors) {
    const reach = c.reach ?? 15;
    const from = new THREE.Vector3(...c.at);
    const dir = new THREE.Vector3(...c.normal).normalize();
    const arrow = new THREE.ArrowHelper(dir, from, reach,
      c.external ? 0xff9a4b : 0x57c7ff, 4, 3);
    corridorGroup.add(arrow);
  }
}

function buildCase(caseModel) {
  caseGroup.clear();
  if (!caseModel || !$('chk-case').checked) return;
  for (const layer of caseModel.layers) {
    const mat = new THREE.LineBasicMaterial({ color: 0xc8a165, transparent: true, opacity: 0.5 });
    for (const ring of layer.rings) {
      const pts = ring.map(([x, y]) => new THREE.Vector3(x, y, layer.z0));
      caseGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }
  }
}

function highlightSelection() {
  for (const [id, g] of groupsByPlacement) {
    const on = id === state.selection;
    g.traverse((o) => {
      if (o.isMesh) {
        o.material.emissive?.setHex(on ? 0x2a4a66 : 0x000000);
      }
    });
  }
}

// ---------------------------------------------------------------------------
// resolve loop
// ---------------------------------------------------------------------------

let resolveTimer = null;
let resolveInFlight = false;

function scheduleResolve(delay = 140) {
  clearTimeout(resolveTimer);
  resolveTimer = setTimeout(doResolve, delay);
}

async function doResolve() {
  if (resolveInFlight) { scheduleResolve(80); return; }
  resolveInFlight = true;
  status('resolving…', 'busy');
  try {
    const resolved = await api('/api/resolve', {
      method: 'POST', body: JSON.stringify(state.scene),
    });
    state.resolved = resolved;
    buildSolids(resolved);
    buildCorridors(resolved);
    renderIssues(resolved.issues);
    renderPlacements();
    renderSelection();
    const e = resolved.extent;
    $('extent').textContent =
      `${(e.max[0] - e.min[0]).toFixed(1)} x ${(e.max[1] - e.min[1]).toFixed(1)} ` +
      `x ${(e.max[2] - e.min[2]).toFixed(1)} mm`;
    const errors = resolved.issues.filter((i) => i.level === 'error').length;
    status(errors ? `${errors} error${errors > 1 ? 's' : ''}` : 'ok',
           errors ? 'err' : 'ok');
    if ($('chk-case').checked) refreshCase();
  } catch (err) {
    status(err.message, 'err');
    console.error(err);
  } finally {
    resolveInFlight = false;
  }
}

async function refreshCase() {
  try {
    state.caseModel = await api('/api/build', {
      method: 'POST', body: JSON.stringify(state.scene),
    });
    buildCase(state.caseModel);
  } catch (err) {
    console.warn('case build failed', err);
  }
}

// ---------------------------------------------------------------------------
// panels
// ---------------------------------------------------------------------------

function renderParts() {
  const list = $('part-list');
  list.innerHTML = '';
  const byCat = new Map();
  for (const p of state.parts) {
    if (!byCat.has(p.category)) byCat.set(p.category, []);
    byCat.get(p.category).push(p);
  }
  for (const [cat, parts] of [...byCat].sort()) {
    const h = document.createElement('h2');
    h.textContent = cat;
    list.appendChild(h);
    for (const p of parts.sort((a, b) => a.id.localeCompare(b.id))) {
      const [x0, y0, x1, y1] = p.computed.bounds;
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML =
        `<div class="name">${p.name}<span class="tag ${p.computed.confidence}">` +
        `${p.computed.confidence}</span></div>` +
        `<div class="meta">${(x1 - x0).toFixed(1)} x ${(y1 - y0).toFixed(1)} ` +
        `x ${p.computed.height.toFixed(1)} mm</div>`;
      row.title = (p.notes || '').trim() + '\n\nclick to add to the scene';
      row.onclick = () => addPlacement(p.id);
      list.appendChild(row);
    }
  }
}

function renderPlacements() {
  const list = $('placement-list');
  list.innerHTML = '';
  for (const pl of state.scene.placements) {
    const part = state.partsById.get(pl.part);
    const row = document.createElement('div');
    row.className = 'row' + (pl.id === state.selection ? ' sel' : '');
    const attached = pl.parent ? ` &rarr; ${pl.parent}` : '';
    row.innerHTML =
      `<div class="name">${pl.id}${pl.locked ? ' &#128274;' : ''}</div>` +
      `<div class="meta">${part ? part.name : pl.part}${attached}</div>`;
    row.onclick = () => select(pl.id);
    list.appendChild(row);
  }
}

function renderSelection() {
  const box = $('selection');
  const pl = state.scene?.placements.find((p) => p.id === state.selection);
  if (!pl) { box.innerHTML = '<div class="note">nothing selected</div>'; return; }
  const part = state.partsById.get(pl.part);
  const frame = state.resolved?.frames?.[pl.id];
  const attached = !!pl.parent;

  box.innerHTML = `
    <div class="note">${part ? part.name : pl.part}</div>
    ${attached ? `<div class="note">mated to <b>${pl.parent}</b> via
       ${pl.parent_mate} &harr; ${pl.mate}</div>` : ''}
    <div class="field"><label>x</label><input id="f-x" type="number" step="0.5"
      value="${(attached ? frame?.pos[0] ?? 0 : pl.pos[0]).toFixed(2)}" ${attached ? 'disabled' : ''}></div>
    <div class="field"><label>y</label><input id="f-y" type="number" step="0.5"
      value="${(attached ? frame?.pos[1] ?? 0 : pl.pos[1]).toFixed(2)}" ${attached ? 'disabled' : ''}></div>
    <div class="field"><label>z</label><input id="f-z" type="number" step="0.5"
      value="${(attached ? frame?.pos[2] ?? 0 : pl.pos[2]).toFixed(2)}" ${attached ? 'disabled' : ''}></div>
    <div class="field"><label>rot</label><input id="f-r" type="number" step="90"
      value="${pl.rot_z ?? 0}"></div>
    ${attached ? `<div class="field"><label>gap</label>
      <input id="f-g" type="number" step="0.5" value="${pl.mate_gap ?? 0}"></div>` : ''}
    <div class="note">${pl.locked ? 'locked -- unlock in the YAML to move it' : ''}</div>
  `;
  const bind = (id, fn) => {
    const el = $(id);
    if (el) el.onchange = () => { fn(parseFloat(el.value) || 0); scheduleResolve(0); };
  };
  bind('f-x', (v) => (pl.pos[0] = v));
  bind('f-y', (v) => (pl.pos[1] = v));
  bind('f-z', (v) => (pl.pos[2] = v));
  bind('f-r', (v) => (pl.rot_z = v));
  bind('f-g', (v) => (pl.mate_gap = v));
}

function renderIssues(issues) {
  const list = $('issue-list');
  list.innerHTML = '';
  const errors = issues.filter((i) => i.level === 'error').length;
  const warns = issues.filter((i) => i.level === 'warning').length;
  $('issues-heading').textContent = `issues — ${errors} error${errors === 1 ? '' : 's'}, ${warns} warning${warns === 1 ? '' : 's'}`;
  for (const i of issues) {
    const el = document.createElement('div');
    el.className = `issue ${i.level}`;
    el.innerHTML = `<div class="code">${i.code}</div><div class="msg">${i.message}</div>`;
    el.onclick = () => {
      const ref = i.refs?.[0];
      if (ref) select(ref.split('.')[0]);
    };
    list.appendChild(el);
  }
}

function select(id) {
  state.selection = id;
  highlightSelection();
  renderPlacements();
  renderSelection();
}

// ---------------------------------------------------------------------------
// editing
// ---------------------------------------------------------------------------

function uniqueId(base) {
  const taken = new Set(state.scene.placements.map((p) => p.id));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
}

function addPlacement(partId) {
  const part = state.partsById.get(partId);
  const base = partId.split('-').slice(-1)[0].replace(/[^a-z0-9]/gi, '') || 'part';
  const e = state.resolved?.extent;
  const pos = e ? [e.max[0] + 20, e.min[1], 0] : [0, 0, 0];
  state.scene.placements.push({
    id: uniqueId(base), part: partId, label: part?.name ?? null,
    pos, rot_z: 0, flip: false, locked: false,
    parent: null, parent_mate: null, mate: null, mate_gap: 0,
  });
  state.selection = state.scene.placements.at(-1).id;
  scheduleResolve(0);
}

function removeSelected() {
  const id = state.selection;
  if (!id) return;
  const pl = state.scene.placements.find((p) => p.id === id);
  if (!pl || pl.locked) { status('locked -- not removed', 'err'); return; }
  const doomed = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of state.scene.placements) {
      if (p.parent && doomed.has(p.parent) && !doomed.has(p.id)) { doomed.add(p.id); grew = true; }
    }
  }
  state.scene.placements = state.scene.placements.filter((p) => !doomed.has(p.id));
  state.selection = null;
  scheduleResolve(0);
}

function descendants(id) {
  const out = [];
  const walk = (parent) => {
    for (const p of state.scene.placements) {
      if (p.parent === parent) { out.push(p.id); walk(p.id); }
    }
  };
  walk(id);
  return out;
}

// ---------------------------------------------------------------------------
// pointer: pick and drag
// ---------------------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const dragPlane = new THREE.Plane();
const dragStart = new THREE.Vector3();
let drag = null;

function pickAt(ev) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(solidsGroup.children, true);
  return hits.find((h) => h.object.isMesh) || null;
}

renderer.domElement.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0) return;
  const hit = pickAt(ev);
  if (!hit) return;
  const id = hit.object.userData.placement;
  select(id);

  const pl = state.scene.placements.find((p) => p.id === id);
  if (!pl || pl.locked || pl.parent) return;   // mated parts follow their parent

  controls.enabled = false;
  const vertical = ev.shiftKey;
  const normal = vertical
    ? new THREE.Vector3().subVectors(camera.position, hit.point).setZ(0).normalize()
    : new THREE.Vector3(0, 0, 1);
  dragPlane.setFromNormalAndCoplanarPoint(normal, hit.point);
  raycaster.ray.intersectPlane(dragPlane, dragStart);

  drag = {
    id, vertical,
    origin: [...pl.pos],
    moved: [id, ...descendants(id)].map((d) => ({
      group: groupsByPlacement.get(d),
      base: groupsByPlacement.get(d)?.position.clone(),
    })).filter((m) => m.group),
  };
  renderer.domElement.setPointerCapture(ev.pointerId);
});

renderer.domElement.addEventListener('pointermove', (ev) => {
  if (!drag) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const now = new THREE.Vector3();
  if (!raycaster.ray.intersectPlane(dragPlane, now)) return;

  const d = new THREE.Vector3().subVectors(now, dragStart);
  if (drag.vertical) { d.x = 0; d.y = 0; } else { d.z = 0; }

  for (const m of drag.moved) m.group.position.copy(m.base).add(d);

  const pl = state.scene.placements.find((p) => p.id === drag.id);
  pl.pos = [drag.origin[0] + d.x, drag.origin[1] + d.y, drag.origin[2] + d.z];
  $('hint').textContent =
    `${drag.id}  x ${pl.pos[0].toFixed(1)}  y ${pl.pos[1].toFixed(1)}  z ${pl.pos[2].toFixed(1)}`;
});

function endDrag(ev) {
  if (!drag) return;
  const pl = state.scene.placements.find((p) => p.id === drag.id);
  pl.pos = pl.pos.map((v) => Math.round(v * 10) / 10);
  drag = null;
  controls.enabled = true;
  if (ev) renderer.domElement.releasePointerCapture?.(ev.pointerId);
  scheduleResolve(0);
}
renderer.domElement.addEventListener('pointerup', endDrag);
renderer.domElement.addEventListener('pointercancel', endDrag);

// ---------------------------------------------------------------------------
// keyboard
// ---------------------------------------------------------------------------

window.addEventListener('keydown', (ev) => {
  if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;
  const pl = state.scene?.placements.find((p) => p.id === state.selection);
  const step = ev.shiftKey ? 0.1 : 1;
  const nudge = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[ev.key];
  if (nudge && pl && !pl.locked && !pl.parent) {
    pl.pos[0] = +(pl.pos[0] + nudge[0]).toFixed(2);
    pl.pos[1] = +(pl.pos[1] + nudge[1]).toFixed(2);
    ev.preventDefault();
    scheduleResolve(60);
  } else if ((ev.key === 'r' || ev.key === 'R') && pl) {
    pl.rot_z = ((pl.rot_z || 0) + (ev.shiftKey ? -90 : 90) + 360) % 360;
    scheduleResolve(0);
  } else if (ev.key === 'Delete' || ev.key === 'Backspace') {
    removeSelected();
  } else if (ev.key === 'Escape') {
    select(null);
  }
});

// ---------------------------------------------------------------------------
// toolbar
// ---------------------------------------------------------------------------

async function loadScene(name) {
  state.sceneName = name;
  state.scene = await api(`/api/scenes/${name}`);
  state.selection = null;
  await doResolve();
  frameCamera();
}

function frameCamera() {
  const e = state.resolved?.extent;
  if (!e) return;
  const cx = (e.min[0] + e.max[0]) / 2, cy = (e.min[1] + e.max[1]) / 2;
  const span = Math.max(e.max[0] - e.min[0], e.max[1] - e.min[1], 100);
  controls.target.set(cx, cy, 0);
  camera.position.set(cx + span * 0.35, cy - span * 0.85, span * 0.75);
  controls.update();
}

$('btn-reload').onclick = () => loadScene(state.sceneName);
$('btn-save').onclick = async () => {
  try {
    const r = await api(`/api/scenes/${state.sceneName}`, {
      method: 'PUT', body: JSON.stringify(state.scene),
    });
    status(`saved ${r.placements} placements`, 'ok');
  } catch (err) { status(err.message, 'err'); }
};
$('btn-svg').onclick = async () => {
  const svg = await api('/api/export/svg', { method: 'POST', body: JSON.stringify(state.scene) });
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  window.open(url, '_blank');
};
$('btn-dxf').onclick = async () => {
  const r = await fetch('/api/export/dxf', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.scene),
  });
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = `${state.sceneName}-layers.dxf`; a.click();
};
$('chk-case').onchange = () => ($('chk-case').checked ? refreshCase() : caseGroup.clear());
$('chk-corridors').onchange = () => buildCorridors(state.resolved);
$('chk-grid').onchange = () => { grid.visible = $('chk-grid').checked; axes.visible = grid.visible; };
$('scene-select').onchange = (ev) => loadScene(ev.target.value);

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------

(async function boot() {
  resize();
  tick();
  try {
    const { parts } = await api('/api/parts');
    state.parts = parts;
    state.partsById = new Map(parts.map((p) => [p.id, p]));
    renderParts();

    const { scenes } = await api('/api/scenes');
    const sel = $('scene-select');
    sel.innerHTML = scenes.map((s) => `<option>${s}</option>`).join('');
    if (scenes.length) await loadScene(scenes[0]);
    else status('no scenes in backend/scenes/', 'err');
  } catch (err) {
    status(err.message, 'err');
    console.error(err);
  }
})();
