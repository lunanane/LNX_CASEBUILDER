import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { snapDelta, snapLines } from './snap.js';
import { FINISHES, finishFor } from './finishes.js';
import { createHistory } from './history.js';

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

const history = createHistory();

/** Snapshot before a change. `key` coalesces a run -- a whole drag is one step,
 *  not one step per frame. */
function edit(key = null) {
  if (state.scene) history.push(state.scene, key);
}

async function applyScene(scene) {
  if (!scene) return;
  state.scene = scene;
  if (state.selection && !state.scene.placements.some((p) => p.id === state.selection)) {
    state.selection = null;
  }
  shellFor = placementsSig = issuesSig = null;
  $('sel-interior').value = state.scene.case?.interior || 'pocketed';
  await doResolve();
  buildGizmo();
}

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
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
viewport.appendChild(renderer.domElement);

const view = new THREE.Scene();
view.background = new THREE.Color(0x14161a);

const camera = new THREE.PerspectiveCamera(45, 1, 1, 5000);
camera.up.set(0, 0, 1);
camera.position.set(220, -280, 260);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.12;

// --- lighting ------------------------------------------------------------
// A single sun you can steer, plus an image-based ambient generated from
// RoomEnvironment so metal and gloss have something to reflect. No HDR file to
// download: it is built from geometry at startup.
const ambient = new THREE.AmbientLight(0xffffff, 0.35);
view.add(ambient);

const sun = new THREE.DirectionalLight(0xffffff, 2.6);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0008;
sun.shadow.normalBias = 0.6;
view.add(sun, sun.target);

const fillLight = new THREE.DirectionalLight(0x88aaff, 0.35);
fillLight.position.set(-250, 200, 150);
view.add(fillLight);

const pmrem = new THREE.PMREMGenerator(renderer);
const envTexture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

/** Steer the sun in spherical terms, and frame its shadow camera on the scene
 *  so a 300 mm machine gets a 300 mm shadow map rather than a default 10 mm one. */
function placeSun() {
  const az = THREE.MathUtils.degToRad(Number($('sun-az').value));
  const el = THREE.MathUtils.degToRad(Number($('sun-el').value));
  const e = state.resolved?.extent;
  const cx = e ? (e.min[0] + e.max[0]) / 2 : 0;
  const cy = e ? (e.min[1] + e.max[1]) / 2 : 0;
  const span = e ? Math.max(e.max[0] - e.min[0], e.max[1] - e.min[1], 100) : 200;
  const dist = span * 2.2;

  sun.position.set(
    cx + Math.cos(el) * Math.cos(az) * dist,
    cy + Math.cos(el) * Math.sin(az) * dist,
    Math.sin(el) * dist + 20);
  sun.target.position.set(cx, cy, 0);
  sun.target.updateMatrixWorld();

  const c = sun.shadow.camera;
  c.left = -span; c.right = span; c.top = span; c.bottom = -span;
  c.near = 1; c.far = dist * 3;
  c.updateProjectionMatrix();
  sun.intensity = Number($('sun-power').value);
}

// something for the shadows to land on
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(4000, 4000),
  new THREE.ShadowMaterial({ opacity: 0.32 }));
ground.receiveShadow = true;
ground.visible = false;
view.add(ground);

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
const openingGroup = new THREE.Group();
const snapGroup = new THREE.Group();
view.add(solidsGroup, caseGroup, corridorGroup, panelGroup, gizmoGroup,
         openingGroup, snapGroup);

const groupsByPlacement = new Map();   // id -> { group, meshes: [{mesh, style}] }

function resize() {
  const w = viewport.clientWidth, h = viewport.clientHeight;
  if (!w || !h) return;
  // setSize(w, h, false) leaves the canvas with NO css size, so it lays out at
  // its buffer size -- w * devicePixelRatio. On a scaled display that is wider
  // than the viewport, and since #viewport is positioned the overflowing canvas
  // paints straight over the inspector. Let three.js set the css size.
  renderer.setSize(w, h);
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
    const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
      color: style.color, transparent: true, opacity: style.opacity,
      roughness: 0.55, metalness: 0.12,
    }));
    mesh.castShadow = mesh.receiveShadow = true;
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
  const planes = [...(resolved.panels || [])];
  if (resolved.floor != null) {
    planes.push({ name: 'floor', z: resolved.floor, isFloor: true });
  }
  for (const p of planes) {
    if (p.z == null) continue;
    const w = e.max[0] - e.min[0] + pad * 2;
    const h = e.max[1] - e.min[1] + pad * 2;
    const tint = p.isFloor ? 0x6bd68a : 0x57c7ff;
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({
        color: tint, transparent: true, opacity: 0.05,
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
      new THREE.LineBasicMaterial({ color: tint, transparent: true, opacity: 0.45 })));
  }
}

function buildSideOpenings(resolved) {
  openingGroup.clear();
  if (!$('chk-openings').checked) return;
  for (const o of resolved.side_openings || []) {
    if (!o.outline || o.outline.length < 3) continue;
    const shape = shapeFromOutline(o.outline);
    const top = o.z[1];
    const face = new THREE.Mesh(
      new THREE.ShapeGeometry(shape),
      new THREE.MeshBasicMaterial({ color: 0xff9a4b, transparent: true, opacity: 0.16,
                                    side: THREE.DoubleSide, depthWrite: false }));
    face.position.z = top;
    openingGroup.add(face);
    const pts = o.outline.map(([x, y]) => new THREE.Vector3(x, y, top));
    openingGroup.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: 0xff9a4b, transparent: true, opacity: 0.7 })));
  }
}

/** Rings come back flat, exterior and holes mixed together. Largest-first and
 *  containment testing puts the holes back inside their own outline, which is
 *  what an extruded slab needs. */
function shapesFromRings(rings) {
  const loops = rings
    .filter((r) => r.length > 2)
    .map((r) => ({ pts: r, shape: shapeFromOutline(r), area: Math.abs(ringArea(r)) }))
    .sort((a, b) => b.area - a.area);

  const shapes = [];
  for (const loop of loops) {
    const parent = shapes.find((s) => pointInRing(loop.pts[0], s.pts));
    if (parent) parent.shape.holes.push(new THREE.Path(loop.shape.getPoints()));
    else shapes.push(loop);
  }
  return shapes.map((s) => s.shape);
}

function ringArea(r) {
  let a = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    a += (r[j][0] + r[i][0]) * (r[j][1] - r[i][1]);
  }
  return a / 2;
}

function pointInRing([px, py], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function buildCase(caseModel) {
  caseGroup.clear();
  if (!caseModel || !$('chk-case').checked) return;
  const solid = $('chk-render').checked;

  for (const layer of caseModel.layers) {
    const finish = finishFor(layer.material);
    if (!solid) {
      const mat = new THREE.LineBasicMaterial({
        color: finish.color, transparent: true, opacity: 0.5 });
      for (const ring of layer.rings) {
        const pts = ring.map(([x, y]) => new THREE.Vector3(x, y, layer.z0));
        caseGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
      }
      continue;
    }
    const shapes = shapesFromRings(layer.rings);
    if (!shapes.length) continue;
    const depth = Math.max(layer.z1 - layer.z0 - 0.05, 0.05);
    const geom = new THREE.ExtrudeGeometry(shapes, {
      depth, bevelEnabled: false, curveSegments: 6 });
    const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
      color: finish.color, roughness: finish.roughness, metalness: finish.metalness,
      transparent: finish.opacity < 1, opacity: finish.opacity,
      side: THREE.DoubleSide,
    }));
    mesh.position.z = layer.z0 + 0.025;
    mesh.castShadow = mesh.receiveShadow = true;
    caseGroup.add(mesh);
  }
}

// ---------------------------------------------------------------------------
// the rotate gizmo: a ring on the part's own centre, with one handle
// ---------------------------------------------------------------------------

const gizmo = { center: null, radius: 0, ring: null, handle: null };

// The rotate ring is a FIXED size rather than scaled to the part: sized to the
// part it became a 73 mm hoop around the AMYboard panel and a dot around a
// breakout. This keeps it the same grabbable target for everything.
const RING_MIN = 14, RING_MAX = 26, PIVOT_R = 3.4, AXIS_LEN = 15;

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

/** Bounds of a whole mate stack, not just one board.
 *
 *  A silicone pad glued to a Trellis is its own placement, but you cannot move
 *  it on its own -- and sizing the gizmo to the pad alone buried the pivot
 *  inside the board underneath it. Everything mated together is manipulated as
 *  one assembly, so the gizmo covers the lot and sits above the tallest part. */
function assemblyBounds(rootId) {
  const parts = [rootId, ...descendants(rootId)].map(placementBounds).filter(Boolean);
  if (!parts.length) return null;
  return {
    x0: Math.min(...parts.map((b) => b.x0)), y0: Math.min(...parts.map((b) => b.y0)),
    z0: Math.min(...parts.map((b) => b.z0)), x1: Math.max(...parts.map((b) => b.x1)),
    y1: Math.max(...parts.map((b) => b.y1)), z1: Math.max(...parts.map((b) => b.z1)),
  };
}

function assemblyCenter(rootId) {
  const b = assemblyBounds(rootId);
  return b ? [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2] : null;
}

/** Walk up the mate chain to the placement that actually carries a position.
 *  Clicking the pad should move the Trellis it is stuck to. */
function movableRoot(id) {
  let cur = state.scene?.placements.find((p) => p.id === id);
  const seen = new Set();
  while (cur && cur.parent && !seen.has(cur.id)) {
    seen.add(cur.id);
    cur = state.scene.placements.find((p) => p.id === cur.parent) || null;
  }
  return cur || null;
}

function tagGizmo(obj, kind) {
  obj.userData.gizmo = kind;
  obj.traverse?.((o) => { o.userData.gizmo = kind; });
  return obj;
}

function buildGizmo() {
  gizmoGroup.clear();
  gizmo.ring = gizmo.handle = gizmo.center = null;
  const pl = movableRoot(state.selection);
  if (!pl || pl.locked) return;
  const b = assemblyBounds(pl.id);
  if (!b) return;

  const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2, z = b.z1 + 2.0;
  const half = Math.max(b.x1 - b.x0, b.y1 - b.y0) / 2;
  const r = Math.min(RING_MAX, Math.max(RING_MIN, half + 6));
  gizmo.center = new THREE.Vector3(cx, cy, z);
  gizmo.radius = r;

  // --- the move pivot: a disc on the part's centre, with the two axes it
  // --- slides along. Grab any of it to drag the part in its plane.
  const disc = new THREE.Mesh(
    new THREE.CircleGeometry(PIVOT_R, 28),
    new THREE.MeshBasicMaterial({ color: 0xffc24b, transparent: true, opacity: 0.95,
                                  side: THREE.DoubleSide, depthTest: false }));
  disc.position.set(cx, cy, z);
  disc.renderOrder = 10;
  gizmoGroup.add(tagGizmo(disc, 'move'));

  const ringOutline = new THREE.Mesh(
    new THREE.TorusGeometry(PIVOT_R + 1.6, 0.45, 6, 28),
    new THREE.MeshBasicMaterial({ color: 0x14161a, transparent: true, opacity: 0.8,
                                  depthTest: false }));
  ringOutline.position.set(cx, cy, z);
  ringOutline.renderOrder = 11;
  gizmoGroup.add(tagGizmo(ringOutline, 'move'));

  for (const [dir, color] of [[[1, 0, 0], 0xff7b6b], [[0, 1, 0], 0x7bd88f]]) {
    const arrow = new THREE.ArrowHelper(
      new THREE.Vector3(...dir), new THREE.Vector3(cx, cy, z), AXIS_LEN, color, 4.5, 3);
    arrow.line.material.depthTest = false;
    arrow.cone.material.depthTest = false;
    arrow.renderOrder = 10;
    gizmoGroup.add(tagGizmo(arrow, 'move'));
  }

  // --- the rotate ring, well outside the pivot so the two never fight ---
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(r, 0.6, 8, 96),
    new THREE.MeshBasicMaterial({ color: 0x57c7ff, transparent: true, opacity: 0.45,
                                  depthTest: false }));
  ring.position.set(cx, cy, z);
  ring.renderOrder = 9;
  gizmoGroup.add(tagGizmo(ring, 'ring'));
  gizmo.ring = ring;

  const a = THREE.MathUtils.degToRad(pl.rot_z || 0);
  const handle = new THREE.Mesh(
    new THREE.SphereGeometry(2.8, 20, 16),
    new THREE.MeshBasicMaterial({ color: 0x57c7ff, depthTest: false }));
  handle.position.set(cx + Math.cos(a) * r, cy + Math.sin(a) * r, z);
  handle.renderOrder = 12;
  gizmoGroup.add(tagGizmo(handle, 'handle'));
  gizmo.handle = handle;
}

function moveHandleTo(deg) {
  if (!gizmo.handle || !gizmo.center) return;
  const a = THREE.MathUtils.degToRad(deg);
  gizmo.handle.position.set(
    gizmo.center.x + Math.cos(a) * gizmo.radius,
    gizmo.center.y + Math.sin(a) * gizmo.radius,
    gizmo.center.z);
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
    buildSideOpenings(resolved);
    if ($('chk-render').checked) applyRenderMode();
    if (!drag) buildGizmo();
    renderIssues(resolved.issues);
    renderCaseSize();
    renderMaterials();
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

let placementsSig = null;

function renderPlacements() {
  const list = $('placement-list');
  const sig = state.scene.placements.map((p) => `${p.id}:${p.part}:${p.parent || ''}`).join('|');
  if (sig !== placementsSig) {
    list.innerHTML = '';
    for (const pl of state.scene.placements) {
      const part = state.partsById.get(pl.part);
      const row = document.createElement('div');
      row.dataset.id = pl.id;
      row.className = 'row';
      row.innerHTML =
        `<div class="name">${pl.id}${pl.locked ? ' &#128274;' : ''}</div>` +
        `<div class="meta">${part ? part.name : pl.part}` +
        `${pl.parent ? ` &rarr; ${pl.parent}` : ''}</div>`;
      row.onclick = () => select(pl.id);
      list.appendChild(row);
    }
    placementsSig = sig;
  }
  // selection and error state are cheap attribute flips, never a rebuild
  for (const row of list.children) {
    row.classList.toggle('sel', row.dataset.id === state.selection);
    row.style.color = state.badPlacements.has(row.dataset.id) ? 'var(--err)' : '';
  }
}

let issuesSig = null;

/** The case's material stack, bottom sheet first. Colour is what the preview
 *  paints with; the preset behind the name decides how it catches the light. */
function renderMaterials() {
  const list = $('material-list');
  list.innerHTML = '';
  const mats = state.scene?.case?.materials || [];
  mats.forEach((m, i) => {
    const f = finishFor(m);
    const hex = '#' + f.color.toString(16).padStart(6, '0');
    const row = document.createElement('div');
    row.className = 'matrow';
    row.innerHTML =
      `<input type="color" value="${hex}" title="colour">` +
      `<input type="text" value="${m.name}" title="name -- decides the finish preset">` +
      `<input type="number" step="0.1" value="${m.thickness}" title="thickness, mm">` +
      `<span class="preset">${f.preset || 'plain'}</span>`;
    const [colour, name, thick] = row.querySelectorAll('input');
    colour.onchange = () => { edit(); m.color = colour.value; buildCase(state.caseModel); };
    name.onchange = () => {
      edit();
      m.name = name.value;
      renderMaterials();
      buildCase(state.caseModel);
    };
    thick.onchange = () => {
      edit();
      m.thickness = parseFloat(thick.value) || m.thickness;
      refreshCase();
    };
    list.appendChild(row);
  });
  if (!mats.length) list.innerHTML = '<div class="note">no materials in this scene</div>';
}

/** Auto, or pinned where it is.
 *
 *  While the outline is derived from the hardware's bounding box, pushing a
 *  board outward pushes the wall out with it, so a connector can never be
 *  brought flush with the outside. Freeze it and the wall stops moving. */
function renderCaseSize() {
  const box = $('case-size');
  const c = state.scene?.case;
  if (!c) { box.innerHTML = ''; return; }
  const fixed = !!c.outline;
  box.innerHTML = `
    <div class="field"><label>size</label>
      <select id="f-casemode">
        <option value="auto" ${fixed ? '' : 'selected'}>auto (fits the parts)</option>
        <option value="fixed" ${fixed ? 'selected' : ''}>fixed</option>
      </select></div>
    ${fixed ? `<div class="field"><label>w &times; h</label>
      <input id="f-casew" type="number" step="1" value="${c.outline.size[0]}">
      <input id="f-caseh" type="number" step="1" value="${c.outline.size[1]}"></div>
      <div class="note">the wall stays put &mdash; move a board and it moves
        relative to the case</div>` : `<div class="note">the wall follows the
        hardware, so a board can never reach the outside edge</div>`}
  `;

  $('f-casemode').onchange = async () => {
    edit();
    if ($('f-casemode').value === 'auto') {
      c.outline = null;
    } else {
      try {
        const r = await api('/api/case/freeze', {
          method: 'POST', body: JSON.stringify(state.scene) });
        c.outline = r.outline;
      } catch (err) { status(err.message, 'err'); return; }
    }
    renderCaseSize();
    scheduleResolve(0);
  };
  const size = (id, i) => {
    const el = $(id);
    if (el) el.onchange = () => {
      edit();
      c.outline.size[i] = parseFloat(el.value) || c.outline.size[i];
      refreshCase();
      scheduleResolve(0);
    };
  };
  size('f-casew', 0);
  size('f-caseh', 1);
}

function renderIssues(issues) {
  const list = $('issue-list');
  const sig = issues.map((i) => `${i.level}${i.code}${i.message}`).join('|');
  if (sig === issuesSig) return;
  issuesSig = sig;
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
  const key = [pl.id, pl.parent ? 1 : 0, pl.on_panel || '', pl.mount || 'auto',
    pl.under_panel ? 1 : 0,
    JSON.stringify(pl.sides || [])].join('|');
  if (force || shellFor !== key) { buildSelectionShell(pl); shellFor = key; }
  updateSelectionValues(pl);
}

const SIDES = ['+x', '-x', '+y', '-y'];
const SIDE_LABEL = { '+x': 'right (+x)', '-x': 'left (-x)',
                     '+y': 'back (+y)', '-y': 'front (-y)' };
const POLICIES = ['per_connector', 'open_to_edge', 'open_side', 'none'];

function connectorsOn(placementId, side) {
  return (state.resolved?.connectors || [])
    .filter((c) => c.placement === placementId && c.face === side);
}

/** The SidePolicy entry for one side, created on demand.
 *  Field names must match hwcase.schema.SidePolicy exactly -- the Scene model
 *  forbids extra keys, so a stray property would fail the whole save. */
function sideEntry(pl, side, create = false) {
  if (!Array.isArray(pl.sides)) pl.sides = [];
  let e = pl.sides.find((s) => s.side === side);
  if (!e && create) {
    e = { side, cutout: 'per_connector', include: null, headroom: 2.0, span: 'full' };
    pl.sides.push(e);
  }
  return e || null;
}

/** Tick/untick one port. The include list starts as null meaning "the external
 *  ones", so the first tick has to materialise the current state before
 *  changing it -- otherwise unticking one port would silently include every
 *  internal header on that side. */
function toggleConnector(pl, side, name, on) {
  const e = sideEntry(pl, side, true);
  if (e.include == null) {
    e.include = connectorsOn(pl.id, side).filter((c) => c.included).map((c) => c.name);
  }
  const i = e.include.indexOf(name);
  if (on && i < 0) e.include.push(name);
  if (!on && i >= 0) e.include.splice(i, 1);
}

function sidesSection(pl) {
  const rows = SIDES.map((side) => {
    const conns = connectorsOn(pl.id, side);
    if (!conns.length) return '';
    const e = sideEntry(pl, side);
    const policy = e ? e.cutout : 'per_connector';
    const opts = POLICIES.map((p) =>
      `<option value="${p}" ${policy === p ? 'selected' : ''}>${p}</option>`).join('');
    const ports = conns.map((c) => `
      <label class="port" title="${c.type}${c.external ? ', external' : ', internal wiring'}">
        <input type="checkbox" data-side="${side}" data-conn="${c.name}"
               ${c.included ? 'checked' : ''}>
        <span class="${c.included ? '' : 'off'}">${c.name}</span>
        <span class="ptype">${c.type}</span>
      </label>`).join('');
    const extra = policy === 'open_side' ? `
      <div class="field"><label>head</label>
        <input type="number" step="0.5" data-headroom="${side}"
               value="${e ? e.headroom : 2.0}">
        <select data-span="${side}">
          <option value="full" ${e && e.span === 'full' ? 'selected' : ''}>full</option>
          <option value="board" ${e && e.span === 'board' ? 'selected' : ''}>board</option>
        </select></div>` : '';
    return `<div class="side">
      <div class="side-head"><span>${SIDE_LABEL[side]}</span>
        <select data-policy="${side}">${opts}</select></div>
      ${extra}<div class="ports">${ports}</div>
    </div>`;
  }).join('');
  if (!rows) return '';
  return `<h2>ports &amp; sides</h2>
    <label class="port"><input type="checkbox" id="f-underpanel"
      ${pl.under_panel ? 'checked' : ''}>
      <span>under the faceplate (no window cut)</span></label>
    ${rows}`;
}

function wireSides(pl) {
  const box = $('selection');
  box.querySelectorAll('[data-policy]').forEach((el) => {
    el.onchange = () => {
      edit();
      sideEntry(pl, el.dataset.policy, true).cutout = el.value;
      renderSelection(true);
      scheduleResolve(0);
    };
  });
  box.querySelectorAll('[data-conn]').forEach((el) => {
    el.onchange = () => {
      edit();
      toggleConnector(pl, el.dataset.side, el.dataset.conn, el.checked);
      renderSelection(true);
      scheduleResolve(0);
    };
  });
  box.querySelectorAll('[data-headroom]').forEach((el) => {
    el.onchange = () => {
      sideEntry(pl, el.dataset.headroom, true).headroom = parseFloat(el.value) || 0;
      scheduleResolve(0);
    };
  });
  box.querySelectorAll('[data-span]').forEach((el) => {
    el.onchange = () => {
      sideEntry(pl, el.dataset.span, true).span = el.value;
      scheduleResolve(0);
    };
  });
  const up = $('f-underpanel');
  if (up) up.onchange = () => {
    edit();
    pl.under_panel = up.checked;
    scheduleResolve(0);
  };
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

  const solvedZ = attached || (pl.mount || 'auto') !== 'manual';
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
    <div class="field"><label>rot</label><input id="f-r" type="number" step="15">
      <button id="b-ccw" title="rotate 90&deg; counter-clockwise">&#8634;90</button>
      <button id="b-cw" title="rotate 90&deg; clockwise">90&#8635;</button></div>
    ${attached ? '<div class="field"><label>gap</label><input id="f-g" type="number" step="0.5"></div>' : `
      <div class="field"><label>height</label><select id="f-mount">
        <option value="auto">auto</option>
        <option value="panel">flush to panel</option>
        <option value="floor">on the floor</option>
        <option value="manual">manual z</option>
      </select></div>
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
      : (attached ? `height comes from the mate &mdash; dragging moves <b>${movableRoot(pl.id) ? movableRoot(pl.id).id : pl.parent}</b> and everything mated to it` : '')}</div>
    ${sidesSection(pl)}
  `;

  const num = (id, set) => {
    const el = $(id);
    if (el) el.onchange = () => {
      edit();
      set(parseFloat(el.value) || 0);
      scheduleResolve(0);
    };
  };
  num('f-x', (v) => (pl.pos[0] = v));
  num('f-y', (v) => (pl.pos[1] = v));
  // typing a height means you want that height, not whatever auto picks
  num('f-z', (v) => { pl.pos[2] = v; pl.mount = 'manual'; renderSelection(true); });
  num('f-r', (v) => rotateTo(pl, v));
  num('f-g', (v) => (pl.mate_gap = v));
  num('f-panelofs', (v) => (pl.panel_offset = v));

  const sel = (id, set) => {
    const el = $(id);
    if (el) el.onchange = () => {
      edit();
      set(el.value);
      renderSelection(true);
      scheduleResolve(0);
    };
  };
  sel('f-mount', (v) => (pl.mount = v));
  sel('f-panel', (v) => (pl.on_panel = v || null));
  sel('f-panelref', (v) => (pl.panel_ref = v));

  const turn = (id, dir) => {
    const el = $(id);
    if (el) el.onclick = () => {
      const target = movableRoot(pl.id);
      if (target && !target.locked) {
        edit();
        quarterTurn(target, dir);
        scheduleResolve(0);
      }
    };
  };
  turn('b-ccw', 1);
  turn('b-cw', -1);

  wireSides(pl);
}

function updateSelectionValues(pl) {
  const frame = state.resolved?.frames?.[pl.id];
  const solved = !!pl.parent || (pl.mount || 'auto') !== 'manual';
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
  const mo = $('f-mount'); if (mo) mo.value = pl.mount || 'auto';
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
  edit();
  const part = state.partsById.get(partId);
  const base = partId.split('-').slice(-1)[0].replace(/[^a-z0-9]/gi, '') || 'part';
  const e = state.resolved?.extent;
  state.scene.placements.push({
    id: uniqueId(base), part: partId, label: part?.name ?? null,
    pos: e ? [e.max[0] + 20, e.min[1], 0] : [0, 0, 0],
    rot_z: 0, flip: false, locked: false,
    parent: null, parent_mate: null, mate: null, mate_gap: 0,
    mount: 'auto', on_panel: null, panel_ref: 'auto', panel_offset: 0,
  });
  select(state.scene.placements.at(-1).id);
  scheduleResolve(0);
}

function removeSelected() {
  const pl = currentPlacement();
  if (!pl) return;
  if (pl.locked) { status('locked -- not removed', 'err'); return; }
  edit();
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
  const c = centerXY || assemblyCenter(pl.id) || [pl.pos[0], pl.pos[1]];
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

/** Snap to the next absolute multiple of 90, rather than adding 90 to whatever
 *  free angle the ring left behind. Free-rotate to 37 deg, press the button and
 *  you land on 90 or 0 -- never 127. */
function quarterTurn(pl, dir) {
  const cur = (((pl.rot_z || 0) % 360) + 360) % 360;
  const eps = 1e-6;
  const target = dir > 0
    ? Math.ceil((cur + eps) / 90) * 90
    : Math.floor((cur - eps) / 90) * 90;
  rotateBy(pl, target - cur);
}

// ---------------------------------------------------------------------------
// snapping
//
// Two NeoTrellis boards have to sit exactly 60 mm apart or the button grid
// breaks across the seam, and no amount of careful dragging gets you there.
// So while you drag, the board's own edges and centreline are matched against
// every other board's edges and centrelines, and the nearest match within
// SNAP_TOL wins -- independently in x and y.
//
// The candidate lines come from bounds the *engine* resolved; the browser only
// picks the nearest one, which is cheap enough to do every frame.
// ---------------------------------------------------------------------------

function applySnap(d) {
  snapGroup.clear();
  if (!drag) return d;
  drag.snapped = null;                       // never let a stale snap linger
  if (!drag.box || !$('chk-snap').checked || drag.noSnap) return d;

  const r = snapDelta(drag.box, d, drag.lines);
  d.x = r.x;
  d.y = r.y;
  drag.snapped = [r.sx, r.sy];

  const e = state.resolved?.extent;
  if (e && (r.sx || r.sy)) {
    const mat = new THREE.LineBasicMaterial({ color: 0x6bd68a, transparent: true, opacity: 0.9 });
    const z = (drag.box.z0 + drag.box.z1) / 2;
    if (r.sx) snapGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(r.sx.at, e.min[1] - 20, z),
      new THREE.Vector3(r.sx.at, e.max[1] + 20, z)]), mat));
    if (r.sy) snapGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(e.min[0] - 20, r.sy.at, z),
      new THREE.Vector3(e.max[0] + 20, r.sy.at, z)]), mat));
  }
  return d;
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
  const hits = raycaster.intersectObjects(gizmoGroup.children, true);
  return hits.find((h) => h.object.userData.gizmo === 'handle')
      || hits.find((h) => h.object.userData.gizmo === 'move')
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
  if (g) {
    ev.stopPropagation();
    ev.preventDefault();
    if (g.object.userData.gizmo === 'move') startMove(ev, g.point);
    else startRotate(ev);
    return;
  }

  const hit = pickPart();
  if (!hit) return;                       // empty space -> let the camera have it
  ev.stopPropagation();
  ev.preventDefault();

  const id = hit.object.userData.placement;
  if (id !== state.selection) select(id);

  const root = movableRoot(id);
  if (!root || root.locked) {
    hint(`${root ? root.id : id} is locked &mdash; unlock it in the YAML to move it`);
    return;
  }
  startMove(ev, hit.point);
}, { capture: true });

function startMove(ev, point) {
  const pl = movableRoot(state.selection);
  if (!pl || pl.locked) return;
  const vertical = ev.shiftKey && !pl.on_panel;   // z is solved for panel riders
  const normal = vertical
    ? new THREE.Vector3().subVectors(camera.position, point).setZ(0).normalize()
    : new THREE.Vector3(0, 0, 1);
  dragPlane.setFromNormalAndCoplanarPoint(normal, point);
  const start = planePoint();
  if (!start) return;

  edit(`move:${pl.id}`);          // one undo step for the whole drag
  const family = new Set([pl.id, ...descendants(pl.id)]);
  drag = {
    mode: 'move', id: pl.id, vertical, start, origin: [...pl.pos], moved: null,
    box: assemblyBounds(pl.id),
    lines: snapLines(state.scene.placements.map((p) => p.id), placementBounds, family),
    snapped: null,
    noSnap: ev.altKey,          // hold alt to place something off-grid
  };
  captureGroups();
  gizmoGroup.visible = false;
  renderer.domElement.setPointerCapture(ev.pointerId);
}

function startRotate(ev) {
  const pl = movableRoot(state.selection);
  if (!pl || pl.locked) return;
  dragPlane.setFromNormalAndCoplanarPoint(new THREE.Vector3(0, 0, 1), gizmo.center);
  const start = planePoint();
  if (!start) return;

  edit(`rotate:${pl.id}`);
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
  drag.box = assemblyBounds(drag.id);
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
    let d = new THREE.Vector3().subVectors(now, drag.start);
    if (drag.vertical) { d.x = 0; d.y = 0; } else { d.z = 0; }
    drag.noSnap = ev.altKey;
    if (drag.vertical && pl.mount !== 'manual') pl.mount = 'manual';   // same rule
    if (!drag.vertical) d = applySnap(d);
    for (const m of drag.moved) m.group.position.copy(m.base).add(d);
    pl.pos = [drag.origin[0] + d.x, drag.origin[1] + d.y, drag.origin[2] + d.z];
    const [sx, sy] = drag.snapped || [null, null];
    const snapNote = (sx || sy)
      ? ` &nbsp; <span style="color:#6bd68a">snapped to ${
          [...new Set([sx && sx.id, sy && sy.id].filter(Boolean))].join(' + ')}</span>`
      : '';
    hint(`<b>${drag.id}</b> &nbsp; x ${pl.pos[0].toFixed(1)} &nbsp; y ${pl.pos[1].toFixed(1)}` +
         `${drag.vertical ? ` &nbsp; z ${pl.pos[2].toFixed(1)}` : ''}${snapNote}`);
  } else {
    const angle = Math.atan2(now.y - drag.center[1], now.x - drag.center[0]);
    let deg = THREE.MathUtils.radToDeg(angle - drag.startAngle);
    if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
    pl.pos = [...drag.startPos];
    pl.rot_z = drag.startRot;
    rotateBy(pl, deg, drag.center);
    moveHandleTo(pl.rot_z);
    hint(`<b>${drag.id}</b> &nbsp; rot ${pl.rot_z.toFixed(1)}&deg;` +
         `${ev.shiftKey ? ' (15&deg; steps)' : ''}`);
  }
  scheduleResolve(110);        // live: the case and the issue list follow the drag
}, { capture: true });

function endDrag(ev) {
  if (!drag) return;
  snapGroup.clear();
  const pl = state.scene.placements.find((p) => p.id === drag.id);
  // round to 0.1 mm, but never round away a snap we just made exact
  if (pl && drag.mode === 'move') {
    const [sx, sy] = drag.snapped || [null, null];
    pl.pos = [
      sx ? pl.pos[0] : Math.round(pl.pos[0] * 10) / 10,
      sy ? pl.pos[1] : Math.round(pl.pos[1] * 10) / 10,
      Math.round(pl.pos[2] * 10) / 10,
    ];
  }
  drag = null;
  history.seal();
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

window.addEventListener('keydown', async (ev) => {
  // undo works even from a field: it is the one shortcut you want everywhere
  const z = (ev.key === 'z' || ev.key === 'Z');
  if ((ev.ctrlKey || ev.metaKey) && (z || ev.key === 'y' || ev.key === 'Y')) {
    ev.preventDefault();
    const redo = ev.key === 'y' || ev.key === 'Y' || (z && ev.shiftKey);
    const next = redo ? history.redo(state.scene) : history.undo(state.scene);
    if (!next) { status(redo ? 'nothing to redo' : 'nothing to undo'); return; }
    await applyScene(next);
    status(redo ? 'redone' : `undone (${history.depth} left)`, 'ok');
    return;
  }
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(ev.target.tagName)) return;
  const pl = movableRoot(state.selection);
  const step = ev.shiftKey ? 0.1 : 1;
  const nudge = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                  ArrowUp: [0, step], ArrowDown: [0, -step] }[ev.key];

  if (nudge && pl && !pl.locked) {
    edit(`nudge:${pl.id}`);
    pl.pos[0] = +(pl.pos[0] + nudge[0]).toFixed(2);
    pl.pos[1] = +(pl.pos[1] + nudge[1]).toFixed(2);
    ev.preventDefault();
    scheduleResolve(60);
  } else if ((ev.key === 'r' || ev.key === 'R') && pl && !pl.locked) {
    edit();
    quarterTurn(pl, ev.shiftKey ? -1 : 1);
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
  history.clear();
  $('sel-interior').value = state.scene.case?.interior || 'pocketed';
  state.selection = null;
  shellFor = placementsSig = issuesSig = null;
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
const turnSelection = (dir) => {
  const pl = movableRoot(state.selection);
  if (!pl) { status('select a part first', 'err'); return; }
  if (pl.locked) { status(`${pl.id} is locked`, 'err'); return; }
  edit();
  quarterTurn(pl, dir);
  scheduleResolve(0);
};
$('btn-ccw').onclick = () => turnSelection(1);
$('btn-cw').onclick = () => turnSelection(-1);

$('chk-case').onchange = () => ($('chk-case').checked ? refreshCase() : caseGroup.clear());
$('sel-interior').onchange = () => {
  if (!state.scene) return;
  edit();
  state.scene.case.interior = $('sel-interior').value;
  // no point changing it if you cannot see the result
  if (!$('chk-case').checked) { $('chk-case').checked = true; }
  refreshCase();
  scheduleResolve(0);
};
$('chk-corridors').onchange = () => buildCorridors(state.resolved);
$('chk-panels').onchange = () => buildPanels(state.resolved);

/** Schematic mode is for laying out; render mode is for looking at. The gizmo,
 *  guides and diagnostic overlays only make sense in the former. */
function applyRenderMode() {
  const on = $('chk-render').checked;
  view.background = new THREE.Color(on ? 0x0d0f12 : 0x14161a);
  view.environment = on ? envTexture : null;
  ground.visible = on;
  grid.visible = axes.visible = !on && $('chk-grid').checked;
  sun.visible = on;
  ambient.intensity = on ? 0.35 : 0.55;
  fillLight.intensity = on ? 0.35 : 0.5;
  for (const [, entry] of groupsByPlacement) {
    for (const { mesh } of entry.meshes) {
      mesh.castShadow = mesh.receiveShadow = on;
    }
  }
  if (on) {
    const e = state.resolved?.extent;
    ground.position.z = e ? e.min[2] - 0.6 : -8;
    placeSun();
  }
  buildCase(state.caseModel);
  document.body.classList.toggle('rendering', on);
}
$('chk-render').onchange = () => { applyRenderMode(); if ($('chk-render').checked && !state.caseModel) refreshCase(); };
for (const id of ['sun-az', 'sun-el', 'sun-power']) {
  $(id).oninput = () => { if ($('chk-render').checked) placeSun(); };
}
$('chk-openings').onchange = () => buildSideOpenings(state.resolved);
$('chk-grid').onchange = () => {
  grid.visible = axes.visible = $('chk-grid').checked && !$('chk-render').checked;
};
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
