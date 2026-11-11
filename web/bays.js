// Rack bays in a box case, the parts of the editor that are only arithmetic.
//
// A bay is Eurorack rails set into a box's deck (backend/hwcase/hybrid.py):
// the opening and the room behind it, kept clear for whatever modules are
// screwed in later. The modules are not modelled here or in the engine.
// Everything here is plain data in, plain data out, so it can be tested
// headlessly like snap.js.

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

/** Rows as the short text the bay's header shows: `3U + 1U-intellijel`. */
export function rowsLabel(rows) {
  return (rows && rows.length ? rows : [{ format: '3U' }]).map((r) => r.format).join(' + ');
}
