// Edge snapping, kept separate from the editor so it can be tested headlessly.
//
// Two NeoTrellis boards have to sit exactly 60 mm apart or the button grid
// breaks across the seam, and no amount of careful dragging gets you there.
// While you drag, the moving assembly's own edges and centreline are matched
// against every other board's edges and centrelines; the nearest match within
// the tolerance wins, independently in x and y.
//
// The candidate lines come from bounds the engine resolved -- the browser only
// picks the nearest one, which is cheap enough to run every frame.

export const SNAP_TOL = 2.0;        // mm, world units

/** The three lines a box offers on one axis: both edges and its centre. */
export function axisLines(box, axis, id) {
  const [a, b] = axis === 'x' ? [box.x0, box.x1] : [box.y0, box.y1];
  return [{ v: a, id }, { v: b, id }, { v: (a + b) / 2, id }];
}

/** Lines from every placement except the ones being dragged. */
export function snapLines(ids, boundsOf, exclude) {
  const xs = [], ys = [];
  for (const id of ids) {
    if (exclude.has(id)) continue;
    const b = boundsOf(id);
    if (!b) continue;
    xs.push(...axisLines(b, 'x', id));
    ys.push(...axisLines(b, 'y', id));
  }
  return { xs, ys };
}

/**
 * Nearest line to any of `edges`.
 * Returns `{delta, at, id}` — the correction to apply — or null.
 */
export function bestSnap(edges, lines, tol = SNAP_TOL) {
  let best = null;
  for (const e of edges) {
    for (const l of lines) {
      const delta = l.v - e;
      if (Math.abs(delta) > tol) continue;
      if (best === null || Math.abs(delta) < Math.abs(best.delta)) {
        best = { delta, at: l.v, id: l.id };
      }
    }
  }
  return best;
}

/**
 * Correct a drag delta so the moving box lands on a neighbour's line.
 * `box` is the assembly's bounds before the drag; `d` is {x, y}.
 */
export function snapDelta(box, d, lines, tol = SNAP_TOL) {
  const mx = [box.x0 + d.x, box.x1 + d.x, (box.x0 + box.x1) / 2 + d.x];
  const my = [box.y0 + d.y, box.y1 + d.y, (box.y0 + box.y1) / 2 + d.y];
  const sx = bestSnap(mx, lines.xs, tol);
  const sy = bestSnap(my, lines.ys, tol);
  return {
    x: d.x + (sx ? sx.delta : 0),
    y: d.y + (sy ? sy.delta : 0),
    sx, sy,
  };
}
