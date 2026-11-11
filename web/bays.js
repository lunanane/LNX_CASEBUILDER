// Rack bays in a box case, the parts of the editor that are only arithmetic.
//
// A bay is Eurorack rails set into a box's deck (backend/hwcase/hybrid.py).
// Its modules are placed by slot -- `{id, hp_slot, row}` -- and the engine
// puts each one on the rails; the editor's job is to pick the slot, never to
// compute a position. Everything here is plain data in, plain data out, so
// it can be tested headlessly like snap.js.

/** One HP, in mm: the Eurorack grid. */
export const HP = 5.08;

/** What a new bay is when nothing says otherwise. The engine's default HP is
 *  the same, so a bay that never had its HP set means one thing everywhere. */
export const BAY_DEFAULTS = Object.freeze({
  hp: 28, module_depth: 40, module_panel_t: 2,
});

/** Row formats a bay (and a frame) understands, bottom to top. */
export const ROW_FORMATS = ['3U', '1U-intellijel', '1U-pulplogic'];

/** How far a new bay keeps from the boards already in the scene: room for a
 *  cheek, the floor's web between two rows of slots, and some air. */
export const NEW_BAY_GAP = 20;

/** A bay's rail span in mm. */
export function bayWidth(hp) {
  return (Number.isFinite(+hp) && +hp > 0 ? +hp : BAY_DEFAULTS.hp) * HP;
}

/** Every module entry as an object: a bare id (a module placed by hand) is
 *  `{id}`, a slotted one keeps its `hp_slot` and `row`. */
export function moduleEntries(bay) {
  return (bay?.modules || []).map((m) => (typeof m === 'string'
    ? { id: m } : { id: m.id, hp_slot: m.hp_slot ?? 0, row: m.row ?? 0 }));
}

/** Which bay, if any, lists placement `id` -- and its entry. */
export function findModule(bays, id) {
  for (const bay of bays || []) {
    const i = (bay.modules || []).findIndex(
      (m) => (typeof m === 'string' ? m : m.id) === id);
    if (i >= 0) return { bay, index: i };
  }
  return null;
}

/** The first slot in `row` where `hp` more HP fit, given the slots taken
 *  (`[{slot, hp, row}]`) in a bay `bayHp` wide; null when the row is full. */
export function firstFreeSlot(taken, hp, bayHp, row = 0) {
  const busy = taken.filter((t) => (t.row ?? 0) === row && t.slot != null)
    .map((t) => [t.slot, t.slot + Math.max(1, t.hp || 1)])
    .sort((a, b) => a[0] - b[0]);
  let at = 0;
  for (const [a, b] of busy) {
    if (a - at >= hp) break;
    at = Math.max(at, b);
  }
  return at + hp <= bayHp ? at : null;
}

/** Put module `id` in `bay` at a slot, replacing any entry it already has
 *  there -- a bare id becomes a slotted one. Returns the entry. */
export function setModuleSlot(bay, id, hpSlot, row = 0) {
  const mods = bay.modules || (bay.modules = []);
  const entry = { id, hp_slot: Math.max(0, Math.round(hpSlot)), row: Math.max(0, Math.round(row)) };
  const i = mods.findIndex((m) => (typeof m === 'string' ? m : m.id) === id);
  if (i >= 0) mods[i] = entry; else mods.push(entry);
  return entry;
}

/** Take module `id` out of `bay`. */
export function removeModule(bay, id) {
  bay.modules = (bay.modules || []).filter(
    (m) => (typeof m === 'string' ? m : m.id) !== id);
}

/**
 * Where a new bay goes: to the right of everything already in the scene --
 * boards (`boxes`, `{x0, y0, x1, y1}`) and other bays' footprints alike --
 * `gap` clear of the rightmost, its bottom level with the lowest. An empty
 * scene puts it at the origin.
 */
export function newBayAt(boxes, gap = NEW_BAY_GAP) {
  const real = (boxes || []).filter((b) => b && [b.x0, b.y0, b.x1, b.y1].every(Number.isFinite));
  if (!real.length) return [0, 0];
  const x = Math.max(...real.map((b) => b.x1)) + gap;
  const y = Math.min(...real.map((b) => b.y0));
  return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
}

/** A bay's footprint before the engine has measured it: the rail span plus
 *  a cheek each side, by a 3U row stack plus margins. Only for keeping a new
 *  bay clear of an old one. */
export function roughBayBox(bay, t = 3) {
  const [x, y] = bay.at || [0, 0];
  const rows = (bay.rows && bay.rows.length ? bay.rows : [{ format: '3U' }]);
  const h = rows.reduce((s, r) => s + (String(r.format).startsWith('1U') ? 44.45 : 133.35), 0);
  return { x0: x - t, y0: y - 12, x1: x + bayWidth(bay.hp) + t, y1: y + h + 12 };
}

/**
 * The nearest slot to a module panel dragged to `box` (`{x0, y0, x1, y1}`,
 * world mm), in a bay as the engine describes it (`{x0, hp, rows: [{y_mid}]}`).
 * The slot is clamped so a `hp`-wide module stays inside the rails. Returns
 * `{hp_slot, row, dx, dy}`: the slot, and the correction that lands the
 * panel's left edge on it and its centre on the row's centre line.
 */
export function nearestSlot(box, bay, hp) {
  const rows = bay.rows || [];
  if (!rows.length) return null;
  const cy = (box.y0 + box.y1) / 2;
  let row = 0;
  rows.forEach((r, i) => {
    if (Math.abs(r.y_mid - cy) < Math.abs(rows[row].y_mid - cy)) row = i;
  });
  const last = Math.max(0, Math.floor(bay.hp - Math.max(1, hp || 1) + 1e-9));
  const slot = Math.min(last, Math.max(0, Math.round((box.x0 - bay.x0) / HP)));
  return {
    hp_slot: slot, row,
    dx: bay.x0 + slot * HP - box.x0,
    dy: rows[row].y_mid - cy,
  };
}

/** Rows as the short text the bay's header shows: `3U + 1U-intellijel`. */
export function rowsLabel(rows) {
  return (rows && rows.length ? rows : [{ format: '3U' }]).map((r) => r.format).join(' + ');
}
