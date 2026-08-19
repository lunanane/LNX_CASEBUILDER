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
  badPlacements: new Set(),   // placements named by an error
};

const KIND_STYLE = {
  body:     { color: 0x6f7c91, opacity: 0.95 },
  keepout:  { color: 0xff6b6b, opacity: 0.16 },
  cable:    { color: 0xb98cff, opacity: 0.20 },
  actuator: { color: 0xffc24b, opacity: 0.98 },
  display:  { color: 0x57c7ff, opacity: 0.98 },
};
const BAD_COLOR = 0xff5c5c;
const BAD_OPACITY = 0.28;

const $ = (id) => document.getElementById(id);
const status = (msg, cls = '') => { const el = $('status'); el.textContent = msg; el.className = cls; };
const hint = (msg) => { $('hint').innerHTML = msg; };
const DEFAULT_HINT = $('hint').innerHTML;

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
const keyLight = new THREE.DirectionalLight(0xffffff, 1.6);
keyLight.position.set(180, -240, 400);
view.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x88aaff, 0.5);
fillLight.position.set(-250, 200, 150);
view.add(fillLight);

const grid = new THREE.GridHelper(1000, 100, 0x3a4150, 0x24282f);
grid.rotation.x = Math.PI / 2;
view.add(grid);
const axes = new THREE.AxesHelper(40);
view.add(axes);

const solidsGroup = new THREE.Group();
const caseGroup = new THREE.Group();
const corridorGroup = new THREE.Group();
const panelGroup = new THREE.Group();
const gizmoGroup = new THREE.Group();
view.add(solidsGroup, caseGroup, corridorGroup, panelGroup, gizmoGroup);

const groupsByPlacement = new Map();   // id -> { group, meshes: [{mesh, style}] }

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
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) throw new Error(`${r.status} ${path}: ${(await r.text()).slice(0, 300)}`);
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

function entryFor(id) {
  let e = groupsByPlacement.get(id);
  if (!e) {
    const group = new THREE.Group();
    group.userData.placement = id;
    e = { group, meshes: [] };
    groupsByPlacement.set(id, e);
    solidsGroup.add(group);
  }
  return e;
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
    const mesh = new THREE.Mesh(geom, new THREE.MeshLambertMaterial({
      color: style.color, transparent: true, opacity: style.opacity,
    }));
    mesh.position.z = z0;
    mesh.userData = { placement: s.placement, name: s.name, kind: s.kind };

    const entry = entryFor(s.placement);
    entry.meshes.push({ mesh, style });
    entry.group.add(mesh);

    if (s.kind !== 'keepout' && s.kind !== 'cable') {
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geom, 25),
        new THREE.LineBasicMaterial({ color: 0x0f1114, transparent: true, opacity: 0.5 }));
      edges.position.z = z0;
      edges.userData.isEdge = true;
      entry.group.add(edges);
    }
  }
  applyAppearance();
}

/** Solid when the part is fine, ghosted red when it is part of an error.
 *  That way you can shove things around and read the result without looking
 *  away from the viewport. */
function applyAppearance() {
  for (const [id, entry] of groupsByPlacement) {
    const bad = state.badPlacements.has(id);
    const selected = id === state.selection;
    for (const { mesh, style } of entry.meshes) {
      const m = mesh.material;
      m.color.setHex(bad ? BAD_COLOR : style.color);
      m.opacity = bad ? BAD_OPACITY : style.opacity;
      m.depthWrite = !bad && style.opacity > 0.5;
      m.emissive.setHex(selected ? (bad ? 0x4a1c1c : 0x24435c) : 0x000000);
    }
    for (const child of entry.group.children) {
      if (child.userData.isEdge) child.material.opacity = bad ? 0.15 : 0.5;
    }
  }
}

function buildCorridors(resolved) {
  corridorGroup.clear();
  if (!$('chk-corridors').checked) return;
  for (const c of resolved.connectors) {
    const dir = new THREE.Vector3(...c.normal).normalize();
    corridorGroup.add(new THREE.ArrowHelper(
      dir, new THREE.Vector3(...c.at), c.reach ?? 15,
      c.external ? 0xff9a4b : 0x57c7ff, 4, 3));
  }
}

function buildPanels(resolved) {
  panelGroup.clear();
  if (!$('chk-panels').checked || !resolved?.extent) return;
  const e = resolved.extent;
  const pad = 12;
  for (const p of resolved.panels || []) {
    if (p.z == null) continue;
    const w = e.max[0] - e.min[0] + pad * 2;
    const h = e.max[1] - e.min[1] + pad * 2;
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({
        color: 0x57c7ff, transparent: true, opacity: 0.05,
        side: THREE.DoubleSide, depthWrite: false,
      }));
    plane.position.set((e.min[0] + e.max[0]) / 2, (e.min[1] + e.max[1]) / 2, p.z);
    panelGroup.add(plane);
    const corners = [
      [e.min[0] - pad, e.min[1] - pad], [e.max[0] + pad, e.min[1] - pad],
      [e.max[0] + pad, e.max[1] + pad], [e.min[0] - pad, e.max[1] + pad],
      [e.min[0] - pad, e.min[1] - pad],
    ].map(([x, y]) => new THREE.Vector3(x, y, p.z));
    panelGroup.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(corners),
      new THREE.LineBasicMaterial({ color: 0x57c7ff, transparent: true, opacity: 0.45 })));
  }
}

function buildCase(caseModel) {
  caseGroup.clear();
  if (!caseModel || !$('chk-case').checked) return;
  const mat = new THREE.LineBasicMaterial({ color: 0xc8a165, transparent: true, opacity: 0.5 });
  for (const layer of caseModel.layers) {
    for (const ring of layer.rings) {
      const pts = ring.map(([x, y]) => new THREE.Vector3(x, y, layer.z0));
      caseGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }
  }
}

// ---------------------------------------------------------------------------
// the rotate gizmo: a ring on the part's own centre, with one handle
// ---------------------------------------------------------------------------

const gizmo = { center: null, radius: 0, ring: null, handle: null };

function placementBounds(id) {
  const solids = (state.resolved?.solids || []).filter((s) => s.placement === id);
  if (!solids.length) return null;
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (const s of solids) {
    x0 = Math.min(x0, s.bounds[0]); y0 = Math.min(y0, s.bounds[1]); z0 = Math.min(z0, s.bounds[2]);
    x1 = Math.max(x1, s.bounds[3]); y1 = Math.max(y1, s.bounds[4]); z1 = Math.max(z1, s.bounds[5]);
  }
  return { x0, y0, z0, x1, y1, z1 };
}

function buildGizmo() {
  gizmoGroup.clear();
  gizmo.ring = gizmo.handle = gizmo.center = null;
  const pl = currentPlacement();
  if (!pl || pl.locked || pl.parent) return;
  const b = placementBounds(pl.id);
  if (!b) return;

  const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2, z = b.z1 + 1.5;
  const r = Math.max(b.x1 - b.x0, b.y1 - b.y0) / 2 + 9;
  gizmo.center = new THREE.Vector3(cx, cy, z);
  gizmo.radius = r;

  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(r, 0.7, 8, 96),
    new THREE.MeshBasicMaterial({ color: 0x57c7ff, transparent: true, opacity: 0.55 }));
  ring.position.set(cx, cy, z);
  ring.userData.gizmo = 'ring';
  gizmoGroup.add(ring);
  gizmo.ring = ring;

  const a = THREE.MathUtils.degToRad(pl.rot_z || 0);
  const handle = new THREE.Mesh(
    new THREE.SphereGeometry(3.2, 20, 16),
    new THREE.MeshBasicMaterial({ color: 0x57c7ff }));
  handle.position.set(cx + Math.cos(a) * r, cy + Math.sin(a) * r, z);
  handle.userData.gizmo = 'handle';
  gizmoGroup.add(handle);
  gizmo.handle = handle;

  // a stub from the centre so the current angle is readable at a glance
  gizmoGroup.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(cx, cy, z), handle.position.clone()]),
    new THREE.LineBasicMaterial({ color: 0x57c7ff, transparent: true, opacity: 0.4 })));
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
  if (resolveInFlight) { scheduleResolve(60); return; }
  resolveInFlight = true;
  try {
    const resolved = await api('/api/resolve', {
      method: 'POST', body: JSON.stringify(state.scene),
    });
    state.resolved = resolved;
    state.badPlacements = new Set(
      resolved.issues.filter((i) => i.level === 'error')
        .flatMap((i) => (i.refs || []).map((r) => r.split('.')[0])));

    buildSolids(resolved);
    buildCorridors(resolved);
    buildPanels(resolved);
    if (!drag) buildGizmo();
    renderIssues(resolved.issues);
    renderPlacements();
    renderSelection();

    const e = resolved.extent;
    $('extent').textContent =
      `${(e.max[0] - e.min[0]).toFixed(1)} x ${(e.max[1] - e.min[1]).toFixed(1)} ` +
      `x ${(e.max[2] - e.min[2]).toFixed(1)} mm`;
    const errors = resolved.issues.filter((i) => i.level === 'error').length;
    status(errors ? `${errors} error${errors > 1 ? 's' : ''}` : 'ok', errors ? 'err' : 'ok');
    if ($('chk-case').checked) await refreshCase();
  } catch (err) {
    status(err.message, 'err');
    console.error(err);
  } finally {
    resolveInFlight = false;
    if (drag) reacquireDrag();
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
// panels: parts, placements, issues
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
      row.title = `${(p.notes || '').trim()}\n\nclick to add to the scene`;
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
    if (state.badPlacements.has(pl.id)) row.style.color = 'var(--err)';
    row.innerHTML =
      `<div class="name">${pl.id}${pl.locked ? ' &#128274;' : ''}</div>` +
      `<div class="meta">${part ? part.name : pl.part}` +
      `${pl.parent ? ` &rarr; ${pl.parent}` : ''}</div>`;
    row.onclick = () => select(pl.id);
    list.appendChild(row);
  }
}

function renderIssues(issues) {
  const list = $('issue-list');
  list.innerHTML = '';
  const errors = issues.filter((i) => i.level === 'error').length;
  const warns = issues.filter((i) => i.level === 'warning').length;
  $('issues-heading').textContent =
    `issues — ${errors} error${errors === 1 ? '' : 's'}, ${warns} warning${warns === 1 ? '' : 's'}`;
  for (const i of issues) {
    const el = document.createElement('div');
    el.className = `issue ${i.level}`;
    el.innerHTML = `<div class="code">${i.code}</div><div class="msg">${i.message}</div>`;
    el.onclick = () => { const ref = i.refs?.[0]; if (ref) select(ref.split('.')[0]); };
    list.appendChild(el);
  }
}

// ---------------------------------------------------------------------------
// the selection inspector
//
// Rebuilt only when the selection actually changes; every other resolve just
// writes fresh numbers into the existing inputs. Blowing away innerHTML on
// every drag frame made the panel flicker.
// ---------------------------------------------------------------------------

let shellFor = null;

function currentPlacement() {
  return state.scene?.placements.find((p) => p.id === state.selection) || null;
}

function renderSelection(force = false) {
  const box = $('selection');
  const pl = currentPlacement();
  if (!pl) {
    if (shellFor !== null || force) { box.innerHTML = '<div class="note">nothing selected</div>'; shellFor = null; }
    return;
  }
  const key = `${pl.id}|${pl.parent ? 1 : 0}|${pl.on_panel || ''}`;
  if (force || shellFor !== key) { buildSelectionShell(pl); shellFor = key; }
  updateSelectionValues(pl);
}

function buildSelectionShell(pl) {
  const part = state.partsById.get(pl.part);
  const attached = !!pl.parent;
  const panelNames = (state.resolved?.panels || []).map((p) => p.name);
  // any volume of this part -- or of anything mated on top of it -- can be the
  // feature that sits flush, which is how "the collar, not the shaft" is said
  const refOptions = ['auto', 'top', ...[pl.id, ...descendants(pl.id)]
    .map((id) => state.scene.placements.find((p) => p.id === id)?.part)
    .map((pid) => state.partsById.get(pid))
    .filter(Boolean)
    .flatMap((p) => (p.volumes || []).map((v) => v.name))];

  const solvedZ = attached || pl.on_panel;
  $('selection').innerHTML = `
    <div class="note">${part ? part.name : pl.part}</div>
    ${attached ? `<div class="note">mated to <b>${pl.parent}</b> via
      ${pl.parent_mate} &harr; ${pl.mate}</div>` : ''}
    <div class="field"><label>x</label><input id="f-x" type="number" step="0.5"
      ${attached ? 'disabled' : ''}></div>
    <div class="field"><label>y</label><input id="f-y" type="number" step="0.5"
      ${attached ? 'disabled' : ''}></div>
    <div class="field"><label>z</label><input id="f-z" type="number" step="0.5"
      ${solvedZ ? 'disabled' : ''}></div>
    <div class="field"><label>rot</label><input id="f-r" type="number" step="15"></div>
    ${attached ? '<div class="field"><label>gap</label><input id="f-g" type="number" step="0.5"></div>' : `
      <div class="field"><label>panel</label><select id="f-panel">
        <option value="">-- free --</option>
        ${panelNames.map((n) => `<option value="${n}">${n}</option>`).join('')}
      </select></div>
      ${pl.on_panel ? `
      <div class="field"><label>flush</label><select id="f-panelref">
        ${refOptions.map((n) => `<option value="${n}">${n}</option>`).join('')}
      </select></div>
      <div class="field"><label>offset</label><input id="f-panelofs" type="number" step="0.5"></div>` : ''}`}
    <div class="note">${pl.locked ? 'locked &mdash; unlock it in the YAML to move it'
      : (attached ? 'height comes from the mate' : '')}</div>
  `;

  const num = (id, set) => {
    const el = $(id);
    if (el) el.onchange = () => { set(parseFloat(el.value) || 0); scheduleResolve(0); };
  };
  num('f-x', (v) => (pl.pos[0] = v));
  num('f-y', (v) => (pl.pos[1] = v));
  num('f-z', (v) => (pl.pos[2] = v));
  num('f-r', (v) => rotateTo(pl, v));
  num('f-g', (v) => (pl.mate_gap = v));
  num('f-panelofs', (v) => (pl.panel_offset = v));

  const sel = (id, set) => {
    const el = $(id);
    if (el) el.onchange = () => { set(el.value); renderSelection(true); scheduleResolve(0); };
  };
  sel('f-panel', (v) => (pl.on_panel = v || null));
  sel('f-panelref', (v) => (pl.panel_ref = v));
}

function updateSelectionValues(pl) {
  const frame = state.resolved?.frames?.[pl.id];
  const solved = !!(pl.parent || pl.on_panel);
  const set = (id, v) => {
    const el = $(id);
    if (el && document.activeElement !== el) el.value = Number(v).toFixed(2);
  };
  set('f-x', pl.parent ? frame?.pos[0] ?? 0 : pl.pos[0]);
  set('f-y', pl.parent ? frame?.pos[1] ?? 0 : pl.pos[1]);
  set('f-z', solved ? frame?.pos[2] ?? 0 : pl.pos[2]);
  set('f-r', pl.rot_z || 0);
  set('f-g', pl.mate_gap || 0);
  set('f-panelofs', pl.panel_offset || 0);
  const p = $('f-panel'); if (p) p.value = pl.on_panel || '';
  const r = $('f-panelref'); if (r) r.value = pl.panel_ref || 'auto';
}

function select(id) {
  state.selection = id;
  applyAppearance();
  buildGizmo();
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
  state.scene.placements.push({
    id: uniqueId(base), part: partId, label: part?.name ?? null,
    pos: e ? [e.max[0] + 20, e.min[1], 0] : [0, 0, 0],
    rot_z: 0, flip: false, locked: false,
    parent: null, parent_mate: null, mate: null, mate_gap: 0,
    on_panel: null, panel_ref: 'auto', panel_offset: 0,
  });
  select(state.scene.placements.at(-1).id);
  scheduleResolve(0);
}

function removeSelected() {
  const pl = currentPlacement();
  if (!pl) return;
  if (pl.locked) { status('locked -- not removed', 'err'); return; }
  const doomed = new Set([pl.id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of state.scene.placements) {
      if (p.parent && doomed.has(p.parent) && !doomed.has(p.id)) { doomed.add(p.id); grew = true; }
    }
  }
  state.scene.placements = state.scene.placements.filter((p) => !doomed.has(p.id));
  select(null);
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

/** Rotate about the part's own centre rather than its local origin.
 *
 *  `rot_z` turns the part around its local origin, which for `origin: min`
 *  parts is a corner -- rotating would fling the board across the bench. So we
 *  also swing `pos` around the same centre, which keeps the centre pinned:
 *      pos' = C + Rz(d) * (pos - C)
 */
function rotateBy(pl, deltaDeg, centerXY) {
  const c = centerXY || (() => {
    const b = placementBounds(pl.id);
    return b ? [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2] : [pl.pos[0], pl.pos[1]];
  })();
  const a = THREE.MathUtils.degToRad(deltaDeg);
  const ca = Math.cos(a), sa = Math.sin(a);
  const dx = pl.pos[0] - c[0], dy = pl.pos[1] - c[1];
  pl.pos = [
    +(c[0] + dx * ca - dy * sa).toFixed(3),
    +(c[1] + dx * sa + dy * ca).toFixed(3),
    pl.pos[2],
  ];
  pl.rot_z = +(((pl.rot_z || 0) + deltaDeg) % 360 + 360) % 360;
}

function rotateTo(pl, deg) {
  rotateBy(pl, deg - (pl.rot_z || 0));
}

// ---------------------------------------------------------------------------
// pointer handling
//
// OrbitControls attaches its own pointerdown in its constructor, so it would
// otherwise win the event and orbit the camera while you think you are
// dragging a board. We listen in the CAPTURE phase, which runs first, and stop
// propagation whenever the pointer actually landed on something -- so the
// camera only moves when you grab empty space.
// ---------------------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const dragPlane = new THREE.Plane();
let drag = null;

function updateRay(ev) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
}

function pickGizmo() {
  const hits = raycaster.intersectObjects(gizmoGroup.children, false);
  return hits.find((h) => h.object.userData.gizmo === 'handle')
      || hits.find((h) => h.object.userData.gizmo === 'ring') || null;
}

function pickPart() {
  return raycaster.intersectObjects(solidsGroup.children, true)
    .find((h) => h.object.isMesh) || null;
}

function planePoint() {
  const p = new THREE.Vector3();
  return raycaster.ray.intersectPlane(dragPlane, p) ? p : null;
}

renderer.domElement.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0) return;
  updateRay(ev);

  const g = pickGizmo();
  if (g) { ev.stopPropagation(); ev.preventDefault(); startRotate(ev); return; }

  const hit = pickPart();
  if (!hit) return;                       // empty space -> let the camera have it
  ev.stopPropagation();
  ev.preventDefault();

  const id = hit.object.userData.placement;
  if (id !== state.selection) select(id);

  const pl = currentPlacement();
  if (!pl || pl.locked || pl.parent) {
    hint(pl?.locked ? `${id} is locked` : `${id} follows ${pl?.parent} -- move the parent`);
    return;
  }
  startMove(ev, hit);
}, { capture: true });

function startMove(ev, hit) {
  const pl = currentPlacement();
  const vertical = ev.shiftKey && !pl.on_panel;   // z is solved for panel riders
  const normal = vertical
    ? new THREE.Vector3().subVectors(camera.position, hit.point).setZ(0).normalize()
    : new THREE.Vector3(0, 0, 1);
  dragPlane.setFromNormalAndCoplanarPoint(normal, hit.point);
  const start = planePoint();
  if (!start) return;

  drag = { mode: 'move', id: pl.id, vertical, start, origin: [...pl.pos], moved: null };
  captureGroups();
  gizmoGroup.visible = false;
  renderer.domElement.setPointerCapture(ev.pointerId);
}

function startRotate(ev) {
  const pl = currentPlacement();
  if (!pl || pl.locked || pl.parent) return;
  dragPlane.setFromNormalAndCoplanarPoint(new THREE.Vector3(0, 0, 1), gizmo.center);
  const start = planePoint();
  if (!start) return;

  drag = {
    mode: 'rotate', id: pl.id,
    center: [gizmo.center.x, gizmo.center.y],
    startAngle: Math.atan2(start.y - gizmo.center.y, start.x - gizmo.center.x),
    startRot: pl.rot_z || 0,
    startPos: [...pl.pos],
  };
  renderer.domElement.setPointerCapture(ev.pointerId);
}

function captureGroups() {
  if (!drag || drag.mode !== 'move') return;
  drag.moved = [drag.id, ...descendants(drag.id)]
    .map((d) => groupsByPlacement.get(d)?.group)
    .filter(Boolean)
    .map((group) => ({ group, base: group.position.clone() }));
}

/** After a mid-drag rebuild the old groups are gone; re-grab them and rebase
 *  so the drag keeps tracking the pointer instead of jumping. */
function reacquireDrag() {
  if (!drag || drag.mode !== 'move') return;
  const pl = state.scene.placements.find((p) => p.id === drag.id);
  if (pl) drag.origin = [...pl.pos];
  captureGroups();
  const now = planePoint();
  if (now) drag.start = now;
}

renderer.domElement.addEventListener('pointermove', (ev) => {
  if (!drag) return;
  ev.stopPropagation();
  updateRay(ev);
  const now = planePoint();
  if (!now) return;
  const pl = state.scene.placements.find((p) => p.id === drag.id);
  if (!pl) return;

  if (drag.mode === 'move') {
    const d = new THREE.Vector3().subVectors(now, drag.start);
    if (drag.vertical) { d.x = 0; d.y = 0; } else { d.z = 0; }
    for (const m of drag.moved) m.group.position.copy(m.base).add(d);
    pl.pos = [drag.origin[0] + d.x, drag.origin[1] + d.y, drag.origin[2] + d.z];
    hint(`<b>${drag.id}</b> &nbsp; x ${pl.pos[0].toFixed(1)} &nbsp; y ${pl.pos[1].toFixed(1)}` +
         `${drag.vertical ? ` &nbsp; z ${pl.pos[2].toFixed(1)}` : ''}`);
  } else {
    const angle = Math.atan2(now.y - drag.center[1], now.x - drag.center[0]);
    let deg = THREE.MathUtils.radToDeg(angle - drag.startAngle);
    if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
    pl.pos = [...drag.startPos];
    pl.rot_z = drag.startRot;
    rotateBy(pl, deg, drag.center);
    hint(`<b>${drag.id}</b> &nbsp; rot ${pl.rot_z.toFixed(1)}&deg;` +
         `${ev.shiftKey ? ' (15&deg; steps)' : ''}`);
  }
  scheduleResolve(110);        // live: the case and the issue list follow the drag
}, { capture: true });

function endDrag(ev) {
  if (!drag) return;
  const pl = state.scene.placements.find((p) => p.id === drag.id);
  if (pl && drag.mode === 'move') pl.pos = pl.pos.map((v) => Math.round(v * 10) / 10);
  drag = null;
  gizmoGroup.visible = true;
  hint(DEFAULT_HINT);
  if (ev) { try { renderer.domElement.releasePointerCapture(ev.pointerId); } catch {} }
  scheduleResolve(0);
}
renderer.domElement.addEventListener('pointerup', endDrag, { capture: true });
renderer.domElement.addEventListener('pointercancel', endDrag, { capture: true });

// ---------------------------------------------------------------------------
// keyboard
// ---------------------------------------------------------------------------

window.addEventListener('keydown', (ev) => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(ev.target.tagName)) return;
  const pl = currentPlacement();
  const step = ev.shiftKey ? 0.1 : 1;
  const nudge = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                  ArrowUp: [0, step], ArrowDown: [0, -step] }[ev.key];

  if (nudge && pl && !pl.locked && !pl.parent) {
    pl.pos[0] = +(pl.pos[0] + nudge[0]).toFixed(2);
    pl.pos[1] = +(pl.pos[1] + nudge[1]).toFixed(2);
    ev.preventDefault();
    scheduleResolve(60);
  } else if ((ev.key === 'r' || ev.key === 'R') && pl && !pl.locked && !pl.parent) {
    rotateBy(pl, ev.shiftKey ? -90 : 90);
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
  shellFor = null;
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
    status(`saved ${r.placements} placements${r.backup ? ` (backup ${r.backup})` : ''}`, 'ok');
  } catch (err) { status(err.message, 'err'); }
};
$('btn-svg').onclick = async () => {
  const svg = await api('/api/export/svg', { method: 'POST', body: JSON.stringify(state.scene) });
  window.open(URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' })), '_blank');
};
$('btn-dxf').onclick = async () => {
  const r = await fetch('/api/export/dxf', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.scene),
  });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = `${state.sceneName}-layers.dxf`;
  a.click();
};
$('chk-case').onchange = () => ($('chk-case').checked ? refreshCase() : caseGroup.clear());
$('chk-corridors').onchange = () => buildCorridors(state.resolved);
$('chk-panels').onchange = () => buildPanels(state.resolved);
$('chk-grid').onchange = () => { grid.visible = axes.visible = $('chk-grid').checked; };
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
    $('scene-select').innerHTML = scenes.map((s) => `<option>${s}</option>`).join('');
    if (scenes.length) await loadScene(scenes[0]);
    else status('no scenes in backend/scenes/', 'err');
  } catch (err) {
    status(err.message, 'err');
    console.error(err);
  }
})();
