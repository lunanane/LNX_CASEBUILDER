import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { snapDelta, snapLines } from './snap.js';
import { FINISHES, finishFor } from './finishes.js';
import { createHistory } from './history.js';
import { STOCK, restockName } from './stock.js';
import { wireMenus } from './menu.js';

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
  renderCaseScrews();
  renderPlates();
  renderRenderPanel();
  renderEngravePanel();
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
const supportGroup = new THREE.Group();
const engraveGroup = new THREE.Group();
view.add(engraveGroup);
view.add(solidsGroup, caseGroup, corridorGroup, panelGroup, gizmoGroup,
         openingGroup, snapGroup, supportGroup);

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

/** The mounting holes the case is carrying, drawn as a post from the plate to
 *  the board so you can see which way round it is being held. */
function buildSupports(resolved) {
  supportGroup.clear();
  if (!$('chk-supports').checked) return;
  for (const s of resolved.supports || []) {
    const [x, y] = s.at;
    const down = s.mode === 'from_floor';
    const from = down ? (resolved.extent?.min[2] ?? 0) : s.board_top;
    const to = down ? s.board_bottom : (resolved.extent?.max[2] ?? 0);
    const colour = down ? 0x6bd68a : 0x57c7ff;
    const g = new THREE.CylinderGeometry(s.screw_d / 2, s.screw_d / 2,
                                         Math.max(Math.abs(to - from), 0.5), 12);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
      color: colour, transparent: true, opacity: 0.75 }));
    m.rotation.x = Math.PI / 2;
    m.position.set(x, y, (from + to) / 2);
    supportGroup.add(m);
  }
}


/** Darken the walls of a slab where they meet the slabs above and below.
 *
 *  A stack of flat sheets has almost nothing concave in it, so a screen-space
 *  AO pass finds very little to darken and costs a full postprocessing chain
 *  to run. What actually reads as "these are separate pieces of plywood" is
 *  the shadow line in the seam between two of them, and that is a function of
 *  position within the slab -- so it can simply be baked in.
 *
 *  Only the walls: the flat faces are what you look at, and dimming those
 *  would just make the whole model muddy. Returns whether it did anything, so
 *  the material only pays for vertex colours when there are some.
 */
function bakeContactShading(geom, depth, strength) {
  if (!(strength > 0) || depth <= 0) return false;
  const pos = geom.getAttribute('position');
  const nrm = geom.getAttribute('normal');
  if (!pos || !nrm) return false;

  const col = new Float32Array(pos.count * 3).fill(1);
  // How far in from a seam the darkening reaches. Fixed in millimetres, not a
  // fraction of the thickness: a shadow in a joint is about the same width
  // whether the sheet is 3 mm or 12 mm.
  const reach = Math.min(1.2, depth / 2);

  for (let i = 0; i < pos.count; i++) {
    if (Math.abs(nrm.getZ(i)) > 0.5) continue;          // a cap, not a wall
    const z = pos.getZ(i);
    const d = Math.min(z, depth - z);                   // distance to a seam
    if (d >= reach) continue;
    const shade = 1 - strength * (1 - d / reach);
    col[i * 3] = col[i * 3 + 1] = col[i * 3 + 2] = shade;
  }
  geom.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return true;
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
    const ao = bakeContactShading(geom, depth, renderSettings().ao);
    const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
      color: finish.color, roughness: finish.roughness, metalness: finish.metalness,
      transparent: finish.opacity < 1, opacity: finish.opacity,
      side: THREE.DoubleSide, vertexColors: ao,
    }));
    mesh.position.z = layer.z0 + 0.025;
    mesh.castShadow = mesh.receiveShadow = true;
    caseGroup.add(mesh);
  }
  buildEngravings(caseModel);
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
    buildSupports(resolved);
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
    renderCaseScrews();
    // the build is the only thing that knows a label outgrew its box
    renderEngravePanel();
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
/** Re-stock every layer at once.
 *
 *  The per-layer controls below are the fine grain, and most of the time what
 *  someone wants is "the whole thing in smoked acrylic" -- which was eleven
 *  separate colour pickers away.
 *
 *  Thickness is left alone: it is a structural number, not an appearance one,
 *  and quietly changing it here would move every board in the case.
 */
function restockCase(stock) {
  const mats = state.scene?.case?.materials || [];
  if (!mats.length) return;
  edit();
  for (const m of mats) {
    m.name = restockName(m.name, stock);
    m.color = stock.color;
  }
  renderMaterials();
  buildCase(state.caseModel);
  status(`whole case in ${stock.label}`, 'ok');
}

function renderMaterials() {
  const list = $('material-list');
  list.innerHTML = '';
  const mats = state.scene?.case?.materials || [];

  const bar = document.createElement('div');
  bar.className = 'field';
  bar.innerHTML = '<label title="set every layer at once">all layers</label>' +
    `<select id="stock-all"><option value="">choose stock&hellip;</option>` +
    STOCK.map((v, i) => `<option value="${i}">${v.label}</option>`).join('') +
    '</select>';
  list.appendChild(bar);
  $('stock-all').onchange = (ev) => {
    const pick = STOCK[Number(ev.target.value)];
    ev.target.value = '';
    if (pick) restockCase(pick);
  };

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
  if (!mats.length) {
    list.insertAdjacentHTML('beforeend',
      '<div class="note">no materials in this scene</div>');
  }
}

/** Bolts through the whole stack.
 *
 *  These are what turn a pile of sheets into a case, so they get their own
 *  section rather than another checkbox in the toolbar. Every field writes
 *  straight to scene.case and rebuilds, because the only way to judge a screw
 *  pattern is to look at it.
 */
const SCREW_FIELDS = {
  'cs-inset': 'case_screw_inset',
  'cs-d': 'case_screw_d',
  'cs-head': 'case_screw_head',
  'cs-spacing': 'case_screw_spacing',
  'cs-boss': 'case_screw_boss',
  'cs-min-seg': 'min_segment',
  'cs-sheet-margin': 'sheet_margin',
  'cs-sheet-gap': 'sheet_spacing',
};

/** The cutting plates you actually have.
 *
 *  One row is the ordinary case and is not removable -- a stock of zero
 *  plates cuts nothing. `+ plate` adds a row seeded from the last one,
 *  because the second entry is usually "same stock, and also this offcut".
 */
function renderPlates() {
  const box = $('plate-list');
  const c = state.scene?.case;
  if (!box || !c) return;
  if (!Array.isArray(c.plates) || !c.plates.length) c.plates = [[350, 350]];

  box.innerHTML = '';
  c.plates.forEach((plate, i) => {
    const row = document.createElement('div');
    row.className = 'plate-row';
    row.innerHTML =
      `<input type="number" class="pl-w" step="10" min="20" value="${plate[0]}">` +
      `<span class="unit">x</span>` +
      `<input type="number" class="pl-h" step="10" min="20" value="${plate[1]}">` +
      `<span class="unit">mm</span>` +
      (c.plates.length > 1
        ? `<button class="pl-del" title="remove this plate size">&times;</button>`
        : '');
    const setDim = (idx, el) => {
      const v = parseFloat(el.value);
      if (!Number.isFinite(v) || v < 20) { renderPlates(); return; }
      edit();
      c.plates[i][idx] = v;
    };
    row.querySelector('.pl-w').onchange = (ev) => setDim(0, ev.target);
    row.querySelector('.pl-h').onchange = (ev) => setDim(1, ev.target);
    row.querySelector('.pl-del')?.addEventListener('click', () => {
      edit();
      c.plates.splice(i, 1);
      renderPlates();
    });
    box.appendChild(row);
  });
}

function wirePlates() {
  $('btn-add-plate').onclick = () => {
    const c = state.scene?.case;
    if (!c) return;
    edit();
    const last = c.plates[c.plates.length - 1] ?? [350, 350];
    c.plates.push([...last]);
    renderPlates();
  };
}

function renderCaseScrews() {
  const c = state.scene?.case;
  if (!c) return;
  const mode = c.case_screws || 'none';
  $('cs-mode').value = mode;
  $('cs-center').checked = !!c.case_screw_center;
  $('cs-wells').checked = c.screw_wells !== false;   // default on
  for (const [id, key] of Object.entries(SCREW_FIELDS)) {
    const el = $(id);
    if (document.activeElement !== el) el.value = c[key] ?? '';
  }
  // spacing only means anything when there are edge bolts to space
  $('cs-spacing').disabled = mode !== 'perimeter';
  for (const id of ['cs-inset', 'cs-d', 'cs-head', 'cs-boss']) {
    $(id).disabled = mode === 'none';
  }

  // The build tells us if a bolt is about to go through a board; that is the
  // one thing here worth interrupting for.
  const hits = new Set();
  for (const l of state.caseModel?.layers || []) {
    for (const n of l.notes || []) {
      const m = /^case bolt at \(([-\d.]+), ([-\d.]+)\) runs into (.+)$/.exec(n);
      if (m) hits.add(`(${m[1]}, ${m[2]}) hits ${m[3]}`);
    }
  }
  $('cs-note').textContent = hits.size
    ? `bolt ${[...hits].join('; ')} — move the board or the screw`
    : '';
}

function wireCaseScrews() {
  const push = () => {
    if (!$('chk-case').checked) $('chk-case').checked = true;
    renderCaseScrews();
    refreshCase().then(renderCaseScrews);
    scheduleResolve(0);
  };
  $('cs-mode').onchange = () => {
    edit();
    state.scene.case.case_screws = $('cs-mode').value;
    push();
  };
  $('cs-center').onchange = () => {
    edit();
    state.scene.case.case_screw_center = $('cs-center').checked;
    push();
  };
  $('cs-wells').onchange = () => {
    edit();
    state.scene.case.screw_wells = $('cs-wells').checked;
    push();
  };
  for (const [id, key] of Object.entries(SCREW_FIELDS)) {
    $(id).onchange = () => {
      const v = parseFloat($(id).value);
      if (!Number.isFinite(v) || v < 0) { renderCaseScrews(); return; }
      edit();
      state.scene.case[key] = v;
      push();
    };
  }
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
  // A problem has to be visible from whichever pane you happen to be in,
  // otherwise panes just hide things better than one long scroll did.
  markIssueCount(issues);
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
    pl.tilt || 0, pl.support || 'none',
    pl.under_panel ? 1 : 0,
    JSON.stringify(pl.sides || [])].join('|');
  if (force || shellFor !== key) { buildSelectionShell(pl); shellFor = key; }
  updateSelectionValues(pl);
}

const SIDES = ['+x', '-x', '+y', '-y'];
const SIDE_LABEL = { '+x': 'right (+x)', '-x': 'left (-x)',
                     '+y': 'back (+y)', '-y': 'front (-y)' };
const POLICIES = ['per_connector', 'open_to_edge', 'channel', 'open_side', 'none'];

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
    e = { side, cutout: 'per_connector', include: null, margin: null,
          channel_width: 10.0, headroom: 2.0, span: 'full' };
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
    const wall = `
      <div class="field"><label>wall</label>
        <input type="number" step="0.5" min="0" placeholder="auto"
               data-margin="${side}" value="${e && e.margin != null ? e.margin : ''}">
        <span class="preset">mm to outside</span></div>`;
    const chan = policy === 'channel' ? `
      <div class="field"><label>width</label>
        <input type="number" step="1" min="1" data-chanw="${side}"
               value="${e ? e.channel_width : 10}">
        <span class="preset">mm groove</span></div>` : '';
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
      ${wall}${chan}${extra}<div class="ports">${ports}</div>
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
  box.querySelectorAll('[data-margin]').forEach((el) => {
    el.onchange = () => {
      edit();
      const v = el.value.trim();
      sideEntry(pl, el.dataset.margin, true).margin = v === '' ? null : (parseFloat(v) || 0);
      scheduleResolve(0);
    };
  });
  box.querySelectorAll('[data-chanw]').forEach((el) => {
    el.onchange = () => {
      edit();
      sideEntry(pl, el.dataset.chanw, true).channel_width =
        parseFloat(el.value) || 10.0;
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
    <div class="field"><label>screws</label><select id="f-support">
      <option value="none">not mounted</option>
      <option value="from_floor">post up from the bottom plate</option>
      <option value="from_lid">screw down through the faceplate</option>
    </select></div>
    ${pl.support && pl.support !== 'none' ? `
    <div class="field"><label title="sink the screw heads into the outer plate so they finish flush with it">inset</label>
      <select id="f-inset">
        <option value="auto">auto</option>
        <option value="yes">flush heads</option>
        <option value="no">heads proud</option>
      </select></div>
    <div class="note">${pl.support === 'from_lid'
      ? 'Auto = <b>proud</b>. A board directly under the faceplate needs the '
        + 'head to bear on the outside face; counterboring the only plate '
        + 'between the head and the board leaves it nothing to pull against.'
      : 'Auto = <b>flush</b>, so a proud head does not make the case rock on '
        + 'the bench.'}</div>` : ''}
    <div class="field"><label>stand</label><select id="f-tilt">
      <option value="0">flat</option>
      <option value="90">on edge (front)</option>
      <option value="180">upside down</option>
      <option value="270">on edge (back)</option>
    </select></div>
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
  sel('f-support', (v) => (pl.support = v));
  sel('f-inset', (v) => {
    pl.screw_inset = v === 'auto' ? null : v === 'yes';
  });
  sel('f-tilt', (v) => { pl.tilt = parseInt(v, 10); pl.flip = false; });
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
  const su = $('f-support'); if (su) su.value = pl.support || 'none';
  const ins = $('f-inset');
  if (ins) {
    ins.value = pl.screw_inset == null ? 'auto' : (pl.screw_inset ? 'yes' : 'no');
  }
  const ti = $('f-tilt');
  if (ti) ti.value = String(pl.tilt || (pl.flip ? 180 : 0));
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
    tilt: 0, support: 'none', mount: 'auto',
    on_panel: null, panel_ref: 'auto', panel_offset: 0,
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

async function timeTravel(redo) {
  const next = redo ? history.redo(state.scene) : history.undo(state.scene);
  if (!next) { status(redo ? 'nothing to redo' : 'nothing to undo'); return; }
  await applyScene(next);
  status(redo ? 'redone' : `undone (${history.depth} left)`, 'ok');
}
document.addEventListener('hw-undo', () => timeTravel(false));
document.addEventListener('hw-redo', () => timeTravel(true));

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
  state.scene = await api(`/api/scenes/${encodeURIComponent(name)}`);
  history.clear();
  $('sel-interior').value = state.scene.case?.interior || 'pocketed';
  renderCaseScrews();
  renderPlates();
  renderRenderPanel();
  renderEngravePanel();
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

/** Refresh the picker and select `pick`. */
async function refreshScenes(pick) {
  const { scenes } = await api('/api/scenes');
  $('scene-select').innerHTML = scenes.map(
    (s) => `<option${s === pick ? ' selected' : ''}>${s}</option>`).join('');
  return scenes;
}

function askName(what, suggested) {
  const name = window.prompt(what, suggested);
  if (name == null) return null;
  const clean = name.trim();
  if (!clean) { status('a scene needs a name', 'err'); return null; }
  return clean;
}

/** Write the current in-memory scene under a new name and switch to it.
 *  `seed` copies the source file first, so its comments come along. */
async function writeAs(name, seed) {
  try {
    await api('/api/scenes', {
      method: 'POST',
      body: JSON.stringify({ name, copy_from: seed ? state.sceneName : null }),
    });
    await api(`/api/scenes/${encodeURIComponent(name)}`, {
      method: 'PUT', body: JSON.stringify(state.scene),
    });
    await refreshScenes(name);
    await loadScene(name);
    status(`now editing ${name}`, 'ok');
  } catch (err) { status(err.message, 'err'); }
}

$('btn-new').onclick = async () => {
  const name = askName('Name for the new scene', 'untitled');
  if (!name) return;
  try {
    await api('/api/scenes', { method: 'POST', body: JSON.stringify({ name }) });
    await refreshScenes(name);
    await loadScene(name);
    status(`started ${name}`, 'ok');
  } catch (err) { status(err.message, 'err'); }
};

$('btn-dup').onclick = () => {
  const name = askName('Copy this scene to', `${state.sceneName}-v2`);
  if (name) writeAs(name, true);
};

$('btn-saveas').onclick = () => {
  const name = askName('Save this scene as', `${state.sceneName}-v2`);
  if (name) writeAs(name, true);
};

$('btn-rename').onclick = async () => {
  const name = askName('Rename this scene to', state.sceneName);
  if (!name || name === state.sceneName) return;
  try {
    await api(`/api/scenes/${encodeURIComponent(state.sceneName)}/rename`, {
      method: 'POST', body: JSON.stringify({ to: name }),
    });
    await refreshScenes(name);
    await loadScene(name);
    status(`renamed to ${name}`, 'ok');
  } catch (err) { status(err.message, 'err'); }
};

$('btn-reload').onclick = () => loadScene(state.sceneName);
$('btn-save').onclick = async () => {
  try {
    const r = await api(`/api/scenes/${encodeURIComponent(state.sceneName)}`, {
      method: 'PUT', body: JSON.stringify(state.scene),
    });
    status(`saved ${r.placements} placements${r.backup ? ` (backup ${r.backup})` : ''}`, 'ok');
  } catch (err) { status(err.message, 'err'); }
};
// Blob URLs are held until they are replaced. One export is most of a
// megabyte, and a session is a lot of exports.
let lastExportUrl = null;

function exportUrl(blob) {
  if (lastExportUrl) URL.revokeObjectURL(lastExportUrl);
  lastExportUrl = URL.createObjectURL(blob);
  return lastExportUrl;
}

/** Hand the file to the browser's download machinery.
 *
 *  The anchor is put in the document before it is clicked: a detached one
 *  works in some browsers and is quietly ignored in others.
 */
function downloadBlob(blob, filename) {
  const a = document.createElement('a');
  a.href = exportUrl(blob);
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Save a blob, asking where to put it when the browser can.
 *
 *  `showSaveFilePicker` is a real save dialog -- pick the folder, pick the
 *  name, and the file is written there. Chrome and Edge have it; Firefox and
 *  Safari do not, and there the download folder is the best available answer.
 *
 *  It needs the click that started this to still count as a user gesture, and
 *  that lasts a few seconds -- long enough to generate the file first, which
 *  is worth doing so a failed export never opens a dialog for a file that does
 *  not exist. If the gesture has expired anyway, fall back rather than fail:
 *  the point is to end up with the file.
 */
async function saveBlob(blob, filename, description, mime) {
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description, accept: { [mime]: [`.${filename.split('.').pop()}`] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return { ok: true, message: `saved ${handle.name}` };
    } catch (err) {
      if (err.name === 'AbortError') return { ok: false, message: 'save cancelled' };
      // SecurityError (gesture expired), NotAllowedError (permission), or an
      // older browser lying about support -- the download still works.
      console.warn('save dialog unavailable, falling back', err);
    }
  }
  downloadBlob(blob, filename);
  return { ok: true, message: `${filename} saved to your downloads folder` };
}

// The same handler, reachable two ways: from the file menu and from a plain
// button on the bar. A menu is a nicety; exporting is the point.
$('btn-svg-bar').onclick = () => $('btn-svg').click();

/** Export the cut, split into bed-sized sheets.
 *
 *  One file per sheet plus a cut list. Where the browser has a directory
 *  picker (Chrome, Edge) the user chooses a folder and real files land in
 *  it; Firefox has no such API, so there the same files arrive as one zip
 *  through the ordinary save path. Both come from the same server-side
 *  packing, so the files are identical either way.
 */
async function exportSheets(fmt) {
  status('packing sheets…');
  try {
    if (window.showDirectoryPicker) {
      const r = await api(`/api/export/sheets/${fmt}?as_json=true`,
        { method: 'POST', body: JSON.stringify(state.scene) });
      try {
        const dir = await window.showDirectoryPicker({ mode: 'readwrite' });
        for (const f of r.sheets) {
          const handle = await dir.getFileHandle(f.name, { create: true });
          const w = await handle.createWritable();
          await w.write(f.content);
          await w.close();
        }
        status(`${r.count} sheet${r.count === 1 ? '' : 's'} + cut list saved to ${dir.name}/`, 'ok');
        return;
      } catch (err) {
        if (err.name === 'AbortError') { status('export cancelled'); return; }
        // picker refused (expired gesture, permission) -- fall through to zip
        console.warn('directory picker unavailable, falling back to zip', err);
      }
    }
    const rz = await fetch(`/api/export/sheets/${fmt}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state.scene),
    });
    if (!rz.ok) throw new Error((await rz.text()).slice(0, 300));
    const saved = await saveBlob(await rz.blob(),
      `${state.sceneName}-sheets-${fmt}.zip`, 'Cut sheets', 'application/zip');
    status(saved.message, saved.ok ? 'ok' : '');
  } catch (err) {
    status(`export failed — ${err.message}`, 'err');
  }
}

$('btn-svg').onclick = async () => exportSheets('svg');
$('btn-dxf').onclick = async () => exportSheets('dxf');

const turnSelection = (dir) => {
  const pl = movableRoot(state.selection);
  if (!pl) { status('select a part first', 'err'); return; }
  if (pl.locked) { status(`${pl.id} is locked`, 'err'); return; }
  edit();
  quarterTurn(pl, dir);
  scheduleResolve(0);
};
$('btn-undo').onclick = () => document.dispatchEvent(new Event('hw-undo'));
$('btn-redo').onclick = () => document.dispatchEvent(new Event('hw-redo'));
$('btn-delete').onclick = () => removeSelected();
$('btn-frame').onclick = () => frameCamera();
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
  const rs = renderSettings();
  view.background = new THREE.Color(on ? 0x0d0f12 : 0x14161a);
  view.environment = on ? (envCache.get(rs.env) ?? envTexture) : null;
  renderer.toneMappingExposure = rs.exposure;
  renderer.shadowMap.enabled = on && rs.shadows;
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
    // The chosen environment may not be in memory yet; this is the one place
    // that notices and fetches it.
    if (!envCache.has(rs.env)) applyEnvironment(rs.env);
    else applyEnvironment(rs.env);
  }
  buildCase(state.caseModel);
  document.body.classList.toggle('rendering', on);
}
$('chk-render').onchange = () => { applyRenderMode(); if ($('chk-render').checked && !state.caseModel) refreshCase(); };
for (const id of ['sun-az', 'sun-el', 'sun-power']) {
  $(id).oninput = () => { if ($('chk-render').checked) placeSun(); };
}
$('chk-openings').onchange = () => buildSideOpenings(state.resolved);
$('chk-supports').onchange = () => buildSupports(state.resolved);
$('chk-grid').onchange = () => {
  grid.visible = axes.visible = $('chk-grid').checked && !$('chk-render').checked;
};
$('scene-select').onchange = (ev) => loadScene(ev.target.value);


// ---------------------------------------------------------------------------
// modal sheets
// ---------------------------------------------------------------------------
//
// One sheet serves the import report, the manual and the about box. They all
// want the same thing: say something at length, offer a couple of actions, and
// get out of the way. Escape and a click on the backdrop both close it, because
// a dialog you cannot dismiss with Escape is a dialog people learn to dread.

let modalPrevFocus = null;

function showModal(title, bodyHtml, actions = []) {
  $('modal-title').textContent = title;
  $('modal-body').innerHTML = bodyHtml;

  const bar = $('modal-actions');
  bar.innerHTML = '';
  for (const a of actions) {
    const b = document.createElement('button');
    b.textContent = a.label;
    b.onclick = () => { closeModal(); a.act?.(); };
    bar.appendChild(b);
  }
  const close = document.createElement('button');
  close.textContent = actions.length ? 'close' : 'got it';
  close.onclick = closeModal;
  bar.appendChild(close);

  modalPrevFocus = document.activeElement;
  $('modal').hidden = false;
  close.focus();
}

/** Same, but for plain text: paragraphs split on blank lines. */
function showAbout(title, text, actions = []) {
  const html = String(text).split('\n\n')
    .map((p) => `<p>${escapeHtml(p)}</p>`).join('');
  showModal(title, html, actions);
}

function closeModal() {
  $('modal').hidden = true;
  $('modal-body').innerHTML = '';
  modalPrevFocus?.focus?.();
  modalPrevFocus = null;
}

function wireModal() {
  $('modal').onpointerdown = (ev) => {
    if (ev.target === $('modal')) closeModal();   // backdrop only
  };
  // Capture, so Escape closes the sheet instead of clearing the selection
  // behind it.
  window.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !$('modal').hidden) {
      ev.stopPropagation();
      closeModal();
    }
  }, true);
}

// ---------------------------------------------------------------------------
// the vendor catalogue
// ---------------------------------------------------------------------------
//
// The palette lists parts somebody has measured and vouched for. This searches
// the other five and a half thousand things Adafruit sell, and imports one on
// demand. Listing them all would bury the dozen that matter.
//
// An import is a draft, and the UI says so rather than smoothing it over: the
// envelope and mounting holes are real, but the orientation is a guess and
// there are no connectors at all, so the board asks the case for no cable room
// until somebody adds them. Discovering that with a soldering iron in hand is
// the outcome this warning exists to prevent.

const catalog = { timer: null, seq: 0, busy: false, last: '' };

function catStatus(msg, cls = '') {
  const el = $('cat-status');
  el.textContent = msg;
  el.className = cls;
}

async function runCatalogSearch(q) {
  const seq = ++catalog.seq;
  try {
    const r = await api(`/api/catalog/search?q=${encodeURIComponent(q)}&limit=20`);
    if (seq !== catalog.seq) return;          // a later keystroke won already
    renderCatalog(r.results, r.catalog);
  } catch (err) {
    if (seq !== catalog.seq) return;
    catStatus(err.message, 'err');
    $('cat-results').innerHTML = '';
  }
}

function renderCatalog(results, status) {
  const box = $('cat-results');
  box.innerHTML = '';

  if (status?.error && !status.products) {
    catStatus('catalogue unavailable — ' + status.error, 'err');
    return;
  }
  const age = status?.age_seconds;
  const stale = status?.stale
    ? ` · showing a cached copy${age ? ` ${Math.round(age / 3600)}h old` : ''}`
    : '';
  catStatus(results.length
    ? `${results.length} of ${status.products} products${stale}`
    : `nothing matches${stale}`);

  for (const e of results) {
    const row = document.createElement('div');
    row.className = 'row cat-row' + (e.in_library ? ' have' : '');
    const badge = e.in_library
      ? '<span class="tag measured">in library</span>'
      : (e.importable ? '<span class="tag datasheet">has CAD</span>'
                      : '<span class="tag estimated">no CAD</span>');
    row.innerHTML =
      `<div class="name">${escapeHtml(e.name)}${badge}</div>` +
      `<div class="meta">#${e.id}${e.category ? ' · ' + escapeHtml(e.category) : ''}</div>`;

    if (e.in_library) {
      row.title = 'already in the palette above — click to add it to the scene';
      row.onclick = () => addPlacement(e.part_id);
    } else if (e.importable) {
      row.title = 'click to measure the vendor model and add it to the palette';
      row.onclick = () => importProduct(e, row);
    } else {
      row.title = 'Adafruit publish no model for this one, so there is nothing '
        + 'to measure — it would have to be entered by hand';
      row.classList.add('disabled');
    }
    box.appendChild(row);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function importProduct(entry, row) {
  if (catalog.busy) return;
  catalog.busy = true;
  row.classList.add('working');
  catStatus(`fetching and measuring ${entry.name}…`);
  try {
    const r = await api(`/api/catalog/import/${entry.id}`, { method: 'POST' });
    const { parts } = await api('/api/parts?reload=true');
    state.parts = parts;
    state.partsById = new Map(parts.map((p) => [p.id, p]));
    renderParts();

    // Say what was guessed. An imported board has no connectors, so it asks
    // the case for no cable room -- that is worth interrupting for.
    const lines = [
      `${r.part.name} imported from ${r.bodies} bodies in ${r.source}.`,
      'This is a DRAFT: the envelope and mounting holes are measured, but the '
        + 'volumes are unnamed and it has no connectors, so it will ask the '
        + 'case for no cable room until you add them.',
    ];
    if (r.flipped) lines.push('It was turned over — check the side you expect '
      + 'to face the panel really does.');
    for (const w of r.warnings || []) lines.push('· ' + w);
    showAbout(`imported ${r.part.name}`, lines.join('\n\n'),
      [{ label: 'add it to the scene', act: () => addPlacement(r.part.id) },
       { label: 'undo the import', act: () => forgetPart(r.part.id) }]);
    catStatus(`${r.part.name} added to the palette`);
  } catch (err) {
    catStatus(err.message, 'err');
  } finally {
    catalog.busy = false;
    row.classList.remove('working');
  }
}

async function forgetPart(partId) {
  try {
    await api(`/api/catalog/import/${encodeURIComponent(partId)}`,
      { method: 'DELETE' });
    const { parts } = await api('/api/parts?reload=true');
    state.parts = parts;
    state.partsById = new Map(parts.map((p) => [p.id, p]));
    renderParts();
    catStatus('import undone');
  } catch (err) {
    catStatus(err.message, 'err');
  }
}

function wireCatalog() {
  const input = $('cat-q');
  input.oninput = () => {
    const q = input.value.trim();
    clearTimeout(catalog.timer);
    if (q.length < 2) {
      catalog.seq++;                          // cancel anything in flight
      $('cat-results').innerHTML = '';
      catStatus('');
      return;
    }
    if (q === catalog.last) return;
    catalog.last = q;
    catStatus('searching…');
    // Debounced: the catalogue is five and a half thousand rows and there is
    // no reason to score it on every keystroke.
    catalog.timer = setTimeout(() => runCatalogSearch(q), 220);
  };
  input.onkeydown = (ev) => {
    ev.stopPropagation();                     // the editor owns R, Del, arrows
    if (ev.key === 'Escape') { input.value = ''; input.oninput(); input.blur(); }
  };
}



// ---------------------------------------------------------------------------
// menus, panes, and the help that used to be a permanent strip of text
// ---------------------------------------------------------------------------

// Read from the server rather than kept here: two copies of a version
// number drift, and the one that matters is the one that computed the
// geometry.
let VERSION = '?';

// This one IS baked in, and that is the point: it identifies the JavaScript
// the browser is running, which the server cannot tell you. A browser holding
// a cached app.js will report an old stamp here while the server reports the
// new version beside it, and that mismatch is the whole diagnosis -- "it does
// nothing when I click it" is what stale UI code looks like from outside.
const UI_BUILD = '2026-08-24c';

function wireTabs() {
  const tabs = [...document.querySelectorAll('.tabs .tab')];
  const show = (id) => {
    for (const t of tabs) {
      const on = t.dataset.pane === id;
      t.classList.toggle('active', on);
      $(t.dataset.pane).hidden = !on;
    }
  };
  for (const t of tabs) t.onclick = () => show(t.dataset.pane);
  showPane = show;
}

let showPane = () => {};

/** Badge the issues tab, so a problem is visible from whichever pane you are in. */
function markIssueCount(issues) {
  const el = $('issue-count');
  const errs = issues.filter((i) => i.level === 'error').length;
  const warns = issues.length - errs;
  el.textContent = issues.length ? String(issues.length) : '';
  el.className = errs ? 'err' : (warns ? 'warn' : '');
}

// ---------------------------------------------------------------------------
// help
// ---------------------------------------------------------------------------

const MANUAL = `
<p>hwcase builds an enclosure around hardware you already own, by stacking
flat sheets. Every layer is a 2D outline, which means the same model is a
laser job, a 2.5D milling job and a solid you can look at &mdash; there is no
separate "export" model that can drift out of step with what you see.</p>

<h3>the loop</h3>
<p><b>Add</b> parts from the palette on the left. What is listed there has been
measured and vouched for; the search box under it reaches the rest of what
Adafruit sell and imports one on demand.</p>
<p><b>Arrange</b> them by dragging. Boards snap edge to edge; hold <kbd>alt</kbd>
to place one freely. A part carries its real volumes &mdash; sockets, glass,
knobs &mdash; so the case knows what it has to avoid, not just how big the
board is.</p>
<p><b>Check</b> the issues pane. It is not decoration: it is the difference
between a case that goes together and one that needs a file. "unverified"
means a number came from a shop page rather than a measurement. A board you
imported from the catalogue has <i>no connectors</i> until you add them, so it
asks the case for no cable room at all &mdash; that one is worth remembering.</p>
<p><b>Cut</b> with export SVG or DXF.</p>

<h3>what the case pane decides</h3>
<p><b>Interior</b> is what the layers between your boards look like.
<i>pocketed</i> cuts each part its own recess; <i>hollow</i> takes out
everything it can; <i>ribs</i> leaves stiffening walls; <i>grown</i> routes
around the cables. All four keep the outer wall and all four guarantee an
I2C lead can get from any board to any other.</p>
<p><b>Bolts</b> clamp the stack together. Corners is usually enough; add edges
for anything much over a hand's width. A bolt that would land on a board is
reported rather than drilled.</p>
<p><b>Min web</b> is the narrowest strip of material you are willing to cut.
Anything thinner is opened out, because a 1 mm thread of plywood snaps the
first time it is handled.</p>

<h3>decorating the panel</h3>
<p>The <b>look</b> pane engraves the lid: labels, fins, grills, rings, hex
mesh, rules and frames. Engraving is a <i>separate pass</i> &mdash; blue in the SVG, its own
DXF layer, titled &ldquo;do not cut&rdquo; &mdash; because a laser that runs a
grill as a cut hands you a faceplate with the middle missing. If you actually
want it cut through there is a switch for that, and you are on your own for
whether the panel still holds together.</p>
<p>Labels use a single-stroke font, so the laser follows the centre line of
each letter in one pass instead of filling an outline &mdash; which at label
size closes up into a blob. The <b>height</b> you set is cap height in real
millimetres, the dimension you measure on the finished panel, and a label is
never squashed to fit the box it sits in: it overhangs and tells you by how
much, because shrinking it would make that number untrue.</p>
<p>The same pane lights the preview and, if Mitsuba is installed, traces a
finished photograph using the same environment, so the picture looks like the
thing you were designing.</p>

<h3>what it will not do for you</h3>
<p>It does not check your wiring, and it does not know that a board needs
airflow, or that you wanted the display the other way up. It reserves room for
cables and tells you when something does not fit.</p>
`;

const SHORTCUTS = `
<h3>mouse</h3>
<p><kbd>drag a part</kbd> move it in its plane &middot;
<kbd>shift + drag</kbd> move it in Z &middot;
<kbd>drag the blue ring</kbd> rotate (hold <kbd>shift</kbd> for 15&deg; steps)
&middot; <kbd>drag empty space</kbd> orbit &middot;
<kbd>alt</kbd> while dragging bypasses snapping</p>

<h3>keyboard</h3>
<p><kbd>R</kbd> / <kbd>shift R</kbd> rotate 90&deg; &middot;
<kbd>arrows</kbd> nudge 1&nbsp;mm, with <kbd>shift</kbd> 0.1&nbsp;mm &middot;
<kbd>del</kbd> remove &middot; <kbd>esc</kbd> deselect &middot;
<kbd>ctrl Z</kbd> / <kbd>ctrl shift Z</kbd> undo and redo &middot;
<kbd>ctrl S</kbd> save &middot; <kbd>F1</kbd> this manual</p>
`;

function aboutHtml() {
  const c = state.scene?.case;
  const parts = state.parts?.length ?? 0;
  return `
<p><b>hwcase ${VERSION}</b> &mdash; parametric enclosures around real hardware.</p>
<p>Parts are described once, in YAML, with where every number came from
attached to it: <i>measured</i>, <i>datasheet</i>, <i>vendor</i>,
<i>community</i> or <i>estimated</i>. Nothing in the geometry is a round
number somebody liked the look of, and anything that is still a guess says so
in the issues pane rather than quietly becoming a cut line.</p>
<p>The case is a stack of 2D slabs, so a layer is simultaneously a laser
outline, a milling pass and a solid. Geometry is shapely, the browser only
draws &mdash; it never computes a cut line of its own, which is why what you
see and what you cut cannot disagree.</p>
<p><b>Engine ${VERSION}</b>, <b>interface ${UI_BUILD}</b>. If those look out
of step with each other, the browser is running a cached copy of the editor:
reload with <kbd>ctrl</kbd>+<kbd>shift</kbd>+<kbd>R</kbd>.</p>
<p>Loaded: ${parts} parts &middot; ${state.scene?.placements?.length ?? 0}
placements &middot; case ${c?.interior ?? '?'},
${(c?.materials ?? []).length} layers.</p>
<p>Vendor models come from Adafruit's public CAD repository and their product
API; both are cached locally, so the editor works with the network unplugged.</p>
`;
}

function wireHelp() {
  $('btn-manual').onclick = () => showModal('how this works', MANUAL);
  $('btn-shortcuts').onclick = () => showModal('keyboard & mouse', SHORTCUTS);
  $('btn-about').onclick = () => showModal(`about hwcase ${VERSION}`, aboutHtml());
  $('btn-hint-more').onclick = () => showModal('keyboard & mouse', SHORTCUTS);
  window.addEventListener('keydown', (ev) => {
    if (ev.key === 'F1') { ev.preventDefault(); showModal('how this works', MANUAL); }
  });
}



// ---------------------------------------------------------------------------
// image-based lighting
// ---------------------------------------------------------------------------
//
// RoomEnvironment is a box with a few emissive panels in it. It is enough for
// gloss to have *something* to reflect, and it is not enough for a picture:
// everything comes out lit from nowhere in particular, and brushed aluminium
// looks like grey plastic.
//
// These are real captured environments (Poly Haven, CC0, credits in
// vendor/hdri/CREDITS.json), vendored at 1k because that is about 1.6 MB each
// and a PMREM cares far more about the light in an environment than its
// resolution. They load on demand and are cached: nobody should pay five
// megabytes for a schematic view they never switch out of.

const ENVIRONMENTS = {
  room: { label: 'room (built in)', file: null,
    hint: 'the synthetic box -- instant, and lit from nowhere in particular' },
  studio: { label: 'studio', file: './vendor/hdri/studio.hdr',
    hint: 'soft even light from large sources; the product-shot look' },
  daylight: { label: 'daylight', file: './vendor/hdri/daylight.hdr',
    hint: 'hard sun and blue sky; strong shadows, cool fill' },
  dusk: { label: 'dusk', file: './vendor/hdri/dusk.hdr',
    hint: 'low warm light against a dark surround; flatters metal' },
};

const envCache = new Map();          // key -> PMREM texture
let envLoader = null;

async function loadEnvironment(key) {
  if (envCache.has(key)) return envCache.get(key);

  const spec = ENVIRONMENTS[key];
  if (!spec || !spec.file) {
    envCache.set('room', envTexture);
    return envTexture;
  }

  if (!envLoader) {
    const { RGBELoader } = await import('./vendor/RGBELoader.js');
    envLoader = new RGBELoader();
  }
  const hdr = await envLoader.loadAsync(spec.file);
  hdr.mapping = THREE.EquirectangularReflectionMapping;
  const tex = pmrem.fromEquirectangular(hdr).texture;
  hdr.dispose();                      // the PMREM is all we keep
  envCache.set(key, tex);
  return tex;
}

async function applyEnvironment(key) {
  const rendering = $('chk-render').checked;
  try {
    const tex = await loadEnvironment(key);
    view.environment = rendering ? tex : null;
    // Showing the environment as a backdrop only makes sense when it is a real
    // place; the synthetic room as a backdrop is just a grey smear.
    const asBackdrop = rendering && key !== 'room' && $('r-backdrop')?.checked;
    view.background = asBackdrop ? tex
      : new THREE.Color(rendering ? 0x0d0f12 : 0x14161a);
    view.backgroundBlurriness = asBackdrop ? 0.25 : 0;
    ground.visible = rendering && !asBackdrop;
  } catch (err) {
    status(`could not load the ${key} environment: ${err.message}`, 'err');
  }
}

// ---------------------------------------------------------------------------
// ambient occlusion
// ---------------------------------------------------------------------------
//
// The cheap trick, and the reason it is the right one here: a stack of flat
// slabs has no concave detail for a screen-space AO pass to find, but it has a
// great many *edges*, and what actually reads as "these are separate sheets"
// is contact darkening where one slab meets the next. A darkened rim baked
// into the material does that convincingly, costs one shader tweak, and does
// not need eight postprocessing files vendored to achieve it.
//
// Real GTAO is the next step up and is deliberately behind a switch: it needs
// the whole EffectComposer chain, and on an integrated GPU at 4K it is the
// difference between a live editor and a slideshow.

const RENDER_DEFAULTS = {
  env: 'studio', exposure: 1.0, backdrop: false, ao: 0.55, shadows: true,
};

function renderSettings() {
  const s = state.scene?.view ?? {};
  return { ...RENDER_DEFAULTS, ...s };
}

function setRender(key, value) {
  if (!state.scene) return;
  state.scene.view = { ...renderSettings(), [key]: value };
}

function renderRenderPanel() {
  const box = $('render-panel');
  if (!box) return;
  const s = renderSettings();
  box.innerHTML = `
    <div class="field"><label>light</label>
      <select id="r-env">${Object.entries(ENVIRONMENTS).map(([k, v]) =>
        `<option value="${k}"${k === s.env ? ' selected' : ''}>${v.label}</option>`
      ).join('')}</select></div>
    <p class="note" id="r-env-hint">${ENVIRONMENTS[s.env]?.hint ?? ''}</p>
    <label class="check"><input type="checkbox" id="r-backdrop"${s.backdrop ? ' checked' : ''}>
      show it behind the case</label>
    <label class="check"><input type="checkbox" id="r-shadows"${s.shadows ? ' checked' : ''}>
      cast shadows</label>
    <div class="field"><label title="film exposure; the environments are not all the same brightness">exposure</label>
      <input type="range" id="r-exposure" min="0.2" max="2.5" step="0.05" value="${s.exposure}"></div>
    <div class="field"><label title="darkening where one sheet meets the next -- what makes a stack read as separate slabs">contact</label>
      <input type="range" id="r-ao" min="0" max="1" step="0.05" value="${s.ao}"></div>
    <p class="note">These affect the preview only. Nothing here changes a cut
      line &mdash; the geometry is the same whichever way it is lit.</p>

    <h2>final image</h2>
    <div class="field"><label title="more samples, less noise -- noise falls with the square root, so four times the samples is half the grain">quality</label>
      <select id="r-samples">
        <option value="48">draft</option>
        <option value="128" selected>good</option>
        <option value="384">slow and clean</option>
      </select></div>
    <button id="btn-shoot">render a photograph</button>
    <p class="note" id="r-shoot-note">Traced properly, on the server, using the
      same light as the preview. Takes minutes, not milliseconds.</p>
  `;

  $('r-env').onchange = async () => {
    const k = $('r-env').value;
    setRender('env', k);
    $('r-env-hint').textContent = ENVIRONMENTS[k]?.hint ?? '';
    if (!$('chk-render').checked) $('chk-render').checked = true;
    applyRenderMode();
    status(`loading the ${k} environment…`);
    await applyEnvironment(k);
    status('');
  };
  $('r-backdrop').onchange = () => {
    setRender('backdrop', $('r-backdrop').checked);
    applyEnvironment(renderSettings().env);
  };
  $('r-shadows').onchange = () => {
    setRender('shadows', $('r-shadows').checked);
    applyRenderMode();
  };
  $('r-exposure').oninput = () => {
    const v = parseFloat($('r-exposure').value);
    setRender('exposure', v);
    renderer.toneMappingExposure = v;
  };
  $('r-ao').oninput = () => {
    setRender('ao', parseFloat($('r-ao').value));
    buildCase(state.caseModel);
  };
  $('btn-shoot').onclick = shootPhotograph;
  refreshShootAvailability();
}

/** The renderer is an optional install, so say up front whether it is there
 *  rather than letting the button fail when it is pressed. */
async function refreshShootAvailability() {
  try {
    const r = await api('/api/render/status');
    if (r.available) return;
    $('btn-shoot').disabled = true;
    $('r-shoot-note').textContent = r.hint;
    $('r-shoot-note').classList.add('warn');
  } catch {
    /* the status endpoint is a courtesy; the button still works without it */
  }
}

async function shootPhotograph() {
  const btn = $('btn-shoot');
  const note = $('r-shoot-note');
  const s = renderSettings();
  btn.disabled = true;
  note.classList.remove('warn');
  note.textContent = 'tracing… this takes minutes, and the editor stays usable';

  const started = Date.now();
  try {
    const q = new URLSearchParams({
      samples: $('r-samples').value,
      env: s.env === 'room' ? 'studio' : s.env,   // no HDRI for the fake room
    });
    const res = await fetch(`/api/render?${q}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(state.scene),
    });
    if (!res.ok) throw new Error((await res.text()).slice(0, 300));

    const url = URL.createObjectURL(await res.blob());
    const secs = Math.round((Date.now() - started) / 1000);
    // Shown rather than downloaded: the usual next step is deciding whether
    // the angle was right, and that wants looking at, not saving.
    showModal(`render — ${secs}s`,
      `<p><img src="${url}" style="max-width:100%;border-radius:4px"></p>
       <p class="note">Right-click to save. Lit by the ${s.env} environment,
       the same one the preview uses.</p>`);
    note.textContent = `done in ${secs}s`;
  } catch (err) {
    note.textContent = err.message;
    note.classList.add('warn');
  } finally {
    btn.disabled = false;
  }
}



// ---------------------------------------------------------------------------
// front-panel engraving
// ---------------------------------------------------------------------------
//
// A cut goes through the sheet and changes the shape of the part; an engrave
// marks the surface and changes nothing structural. The UI keeps them apart
// as firmly as the exporter does, and "cut through" is a deliberate switch
// with a warning on it rather than a subtle difference in a dropdown.

const PATTERNS = {
  text: 'lettering, in a single-stroke font a laser follows in one pass',
  fins: 'parallel fins — the amplifier front-panel look',
  slots: 'rounded slots in rows, like a speaker grill',
  rings: 'concentric rings, for a speaker or a big knob',
  hex: 'hex mesh; the most machined-looking of them',
  rule: 'one hairline, for separating groups of controls',
  frame: 'a border following the edge of the region',
};

// Starting points that look right without fiddling. A grill whose defaults
// need three adjustments before it stops looking like a test pattern is a
// grill nobody uses.
const ENGRAVE_PRESETS = {
  text: { stroke: 0.8, pitch: 3.0, size: [60, 12], round_ends: true },
  fins: { stroke: 1.2, pitch: 3.0, size: [70, 34], round_ends: true },
  slots: { stroke: 1.6, pitch: 2.2, size: [64, 28], round_ends: true },
  rings: { stroke: 0.8, pitch: 2.4, size: [40, 40], round_ends: true },
  hex: { stroke: 0.7, pitch: 5.0, size: [60, 40], round_ends: false },
  rule: { stroke: 0.6, pitch: 3.0, size: [80, 4], round_ends: true },
  frame: { stroke: 1.0, pitch: 3.0, size: [80, 50], round_ends: false },
};

function engravings() {
  if (!state.scene) return [];
  if (!state.scene.engravings) state.scene.engravings = [];
  return state.scene.engravings;
}

function addEngraving(pattern = 'fins') {
  const p = ENGRAVE_PRESETS[pattern];
  const n = engravings().length + 1;
  edit();
  engravings().push({
    name: `${pattern} ${n}`, pattern,
    at: [0, 0], size: [...p.size], rotation: 0,
    stroke: p.stroke, pitch: p.pitch, round_ends: p.round_ends, through: false,
  });
  renderEngravePanel();
  refreshCase();
}

function renderEngravePanel() {
  const box = $('engrave-panel');
  if (!box) return;

  // A rebuild triggered by something else -- dragging a board, say -- must not
  // wipe out a label somebody is halfway through typing, because free text
  // only commits on blur. Selects and number fields have already committed by
  // the time they fire, so those redraw normally: skipping them would swallow
  // the very warning the change was supposed to produce.
  const focused = document.activeElement;
  const typing = focused && box.contains(focused) &&
    (focused.tagName === 'TEXTAREA' ||
     (focused.tagName === 'INPUT' && focused.type === 'text'));
  if (typing) return;

  const list = engravings();

  box.innerHTML = `
    <div class="field">
      <label>add</label>
      <select id="eng-add">
        <option value="">choose a pattern&hellip;</option>
        ${Object.entries(PATTERNS).map(([k, v]) =>
          `<option value="${k}">${k} — ${v}</option>`).join('')}
      </select>
    </div>
    <div id="eng-list"></div>
    ${list.length ? '' : `<p class="note">Nothing engraved yet. Marks go on
      the top face of the lid and are exported as their own pass, in blue,
      on their own DXF layer — a cutter must never run them as cuts.</p>`}
  `;

  $('eng-add').onchange = () => {
    const v = $('eng-add').value;
    if (v) addEngraving(v);
    $('eng-add').value = '';
  };

  const holder = $('eng-list');
  list.forEach((e, i) => {
    const el = document.createElement('div');
    el.className = 'engrow' + (e.through ? ' through' : '');
    el.innerHTML = `
      <div class="engrow-head">
        <input type="text" class="eng-name" value="${escapeHtml(e.name)}" title="name">
        <button class="eng-del" title="remove this engraving">&times;</button>
      </div>
      <div class="field"><label>pattern</label>
        <select class="eng-pattern">${Object.keys(PATTERNS).map((k) =>
          `<option value="${k}"${k === e.pattern ? ' selected' : ''}>${k}</option>`).join('')}
        </select></div>
      <div class="field"><label>centre</label>
        <input type="number" class="eng-x" step="1" value="${e.at[0]}">
        <input type="number" class="eng-y" step="1" value="${e.at[1]}"></div>
      <div class="field"><label title="${e.pattern === 'text'
          ? 'where the label sits. A label is NOT scaled to fit this -- its height is set below, in real millimetres'
          : 'the area the pattern fills'}">${e.pattern === 'text' ? 'box' : 'size'}</label>
        <input type="number" class="eng-w" step="1" min="1" value="${e.size[0]}">
        <input type="number" class="eng-h" step="1" min="1" value="${e.size[1]}"></div>
      ${e.pattern === 'text' ? `
      <div class="field"><label title="what the label says; enter starts a new line">says</label>
        <textarea class="eng-text" rows="2"
          placeholder="VOLUME">${escapeHtml(e.text ?? '')}</textarea></div>
      <div class="field"><label title="cap height -- the dimension you measure on the finished panel">height</label>
        <input type="number" class="eng-size" step="0.5" min="1" value="${e.text_size ?? 6}"><span class="unit">mm</span></div>
      <div class="field"><label>weight</label>
        <select class="eng-font">
          <option value="light"${(e.font ?? 'light') === 'light' ? ' selected' : ''}>light</option>
          <option value="medium"${e.font === 'medium' ? ' selected' : ''}>medium</option>
        </select>
        <select class="eng-align">
          <option value="center"${(e.text_align ?? 'center') === 'center' ? ' selected' : ''}>centred</option>
          <option value="left"${e.text_align === 'left' ? ' selected' : ''}>left</option>
          <option value="right"${e.text_align === 'right' ? ' selected' : ''}>right</option>
        </select></div>
      <div class="field"><label title="width of the stroke the laser follows">stroke</label>
        <input type="number" class="eng-stroke" step="0.1" min="0.1" value="${e.stroke}">
        <label title="extra space between letters; negative tightens">track</label>
        <input type="number" class="eng-track" step="0.1" value="${e.tracking ?? 0}"></div>
      ` : `
      <div class="field"><label title="width of one mark">stroke</label>
        <input type="number" class="eng-stroke" step="0.1" min="0.1" value="${e.stroke}">
        <label title="gap between marks">pitch</label>
        <input type="number" class="eng-pitch" step="0.1" min="0.1" value="${e.pitch}"></div>
      `}
      <div class="field"><label>angle</label>
        <input type="number" class="eng-rot" step="15" value="${e.rotation}"><span class="unit">deg</span></div>
      ${e.pattern === 'text' ? '' : `<label class="check"><input type="checkbox" class="eng-round"${e.round_ends ? ' checked' : ''}> rounded ends</label>`}
      <label class="check danger"><input type="checkbox" class="eng-through"${e.through ? ' checked' : ''}>
        cut right through</label>
      ${e.through ? `<p class="note warn">This one is cut, not engraved — it
        comes out of the plate. Check the panel still holds together.</p>` : ''}
      ${engraveWarning(e)}
    `;

    const num = (sel) => parseFloat(el.querySelector(sel).value);
    const set = (fn) => { edit(); fn(); refreshCase(); };

    el.querySelector('.eng-del').onclick = () => {
      edit();
      list.splice(i, 1);
      renderEngravePanel();
      refreshCase();
    };
    el.querySelector('.eng-name').onchange = (ev) => set(() => { e.name = ev.target.value; });
    el.querySelector('.eng-pattern').onchange = (ev) => {
      // Switching pattern brings its preset with it, otherwise a hex mesh
      // inherits fin spacing and looks like a mistake.
      const p = ENGRAVE_PRESETS[ev.target.value];
      set(() => {
        e.pattern = ev.target.value;
        e.stroke = p.stroke; e.pitch = p.pitch; e.round_ends = p.round_ends;
      });
      renderEngravePanel();
    };
    el.querySelector('.eng-x').onchange = () => set(() => { e.at = [num('.eng-x'), e.at[1]]; });
    el.querySelector('.eng-y').onchange = () => set(() => { e.at = [e.at[0], num('.eng-y')]; });
    el.querySelector('.eng-w').onchange = () => set(() => { e.size = [num('.eng-w'), e.size[1]]; });
    el.querySelector('.eng-h').onchange = () => set(() => { e.size = [e.size[0], num('.eng-h')]; });
    el.querySelector('.eng-stroke').onchange = () => set(() => { e.stroke = num('.eng-stroke'); });
    el.querySelector('.eng-pitch')?.addEventListener('change',
      () => set(() => { e.pitch = num('.eng-pitch'); }));

    // Text: retyping a label on every keystroke would re-solve the whole case
    // per character, so it commits on blur.
    el.querySelector('.eng-text')?.addEventListener('change', (ev) => {
      set(() => { e.text = ev.target.value; });
    });
    el.querySelector('.eng-size')?.addEventListener('change',
      () => set(() => { e.text_size = num('.eng-size'); }));
    el.querySelector('.eng-font')?.addEventListener('change', (ev) => {
      set(() => { e.font = ev.target.value; });
    });
    el.querySelector('.eng-align')?.addEventListener('change', (ev) => {
      set(() => { e.text_align = ev.target.value; });
    });
    el.querySelector('.eng-track')?.addEventListener('change',
      () => set(() => { e.tracking = num('.eng-track'); }));
    el.querySelector('.eng-rot').onchange = () => set(() => { e.rotation = num('.eng-rot'); });
    el.querySelector('.eng-round')?.addEventListener('change',
      (ev) => set(() => { e.round_ends = ev.target.checked; }));
    el.querySelector('.eng-through').onchange = (ev) => {
      set(() => { e.through = ev.target.checked; });
      renderEngravePanel();
    };
    holder.appendChild(el);
  });
}

/** Anything the build said about this engraving that is worth repeating.
 *
 *  A label is not scaled to fit its box -- the height you set is a real cap
 *  height in millimetres -- so it can outgrow the box and get trimmed by the
 *  edge of the plate instead. The build notices; this is where you see it.
 */
function engraveWarning(e) {
  for (const layer of state.caseModel?.layers ?? []) {
    for (const n of layer.notes ?? []) {
      if (n.includes(`'${e.name}'`) &&
          (n.includes('bigger than') || n.includes('falls outside'))) {
        return `<p class="note warn">${escapeHtml(n)}</p>`;
      }
    }
  }
  return '';
}

/** Draw the marks on the lid, so you can see them without exporting. */
function buildEngravings(caseModel) {
  engraveGroup.clear();
  if (!caseModel || !$('chk-case').checked) return;
  for (const layer of caseModel.layers) {
    if (!layer.engrave?.length) continue;
    const shapes = shapesFromRings(layer.engrave);
    if (!shapes.length) continue;
    // A shallow extrusion rather than a flat plane: coplanar with the lid it
    // would z-fight, and a mark you cannot see is not a preview.
    const geom = new THREE.ExtrudeGeometry(shapes, {
      depth: 0.35, bevelEnabled: false, curveSegments: 6 });
    const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
      color: 0x1a1d22, roughness: 0.95, metalness: 0.0 }));
    mesh.position.z = layer.z1 - 0.3;
    engraveGroup.add(mesh);
  }
}


// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------

(async function boot() {
  resize();
  tick();
  wireCaseScrews();
  wireModal();
  wireCatalog();
  wireMenus();
  wireTabs();
  wireHelp();
  wirePlates();
  renderRenderPanel();
  renderEngravePanel();
  try {
    api('/api/health')
      .then((h) => {
        VERSION = h.version ?? '?';
        status(`hwcase ${VERSION} · ui ${UI_BUILD}`);
      })
      .catch(() => {});
    const { parts } = await api('/api/parts');
    state.parts = parts;
    state.partsById = new Map(parts.map((p) => [p.id, p]));
    renderParts();

    const scenes = await refreshScenes();
    if (scenes.length) await loadScene(scenes[0]);
    else status('no scenes yet — press "new" to start one', 'err');
  } catch (err) {
    status(err.message, 'err');
    console.error(err);
  }
})();
