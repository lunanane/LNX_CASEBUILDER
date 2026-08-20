import assert from 'node:assert/strict';
import test from 'node:test';

import { axisLines, bestSnap, snapDelta, snapLines, SNAP_TOL } from '../snap.js';

const box = (x0, y0, x1, y1) => ({ x0, y0, x1, y1, z0: 0, z1: 5 });

// Two 60 mm Trellis boards: A already placed, B being dragged towards it.
const A = box(0, 0, 60, 60);
const B = box(100, 3, 160, 63);
const linesA = snapLines(['a'], () => A, new Set());
const xA = linesA.xs;          // bestSnap takes one axis at a time

test('a box offers both edges and its centre', () => {
  const l = axisLines(A, 'x', 'a').map((e) => e.v);
  assert.deepEqual(l, [0, 60, 30]);
});

test('the dragged board is never its own snap target', () => {
  const l = snapLines(['a', 'b'], (id) => (id === 'a' ? A : B), new Set(['b']));
  assert.ok(l.xs.every((e) => e.id === 'a'));
});

test('nothing snaps when everything is out of range', () => {
  assert.equal(bestSnap([500], xA), null);
});

test('the nearest line wins', () => {
  const s = bestSnap([61.5], xA);   // 1.5 from A.x1 = 60
  assert.equal(s.at, 60);
  assert.equal(s.delta, -1.5);
});

test('edge to edge lands exactly, so 60 mm boards tile', () => {
  // drag B left until its left edge is 1 mm short of A's right edge
  const d = { x: -(B.x0 - A.x1) - 1.0, y: 0 };
  const r = snapDelta(B, d, linesA);
  const landedX0 = B.x0 + r.x;
  assert.ok(Math.abs(landedX0 - 60) < 1e-9, 'left edge should land exactly on A.x1');
  assert.ok(Math.abs(landedX0 - A.x1) < 1e-9, 'no gap, no overlap');
});

test('the button pitch stays continuous across the seam', () => {
  // A's buttons at 7.5/22.5/37.5/52.5; B's should continue 67.5/82.5/...
  const d = { x: -(B.x0 - A.x1) - 0.7, y: -(B.y0 - A.y0) - 0.4 };
  const r = snapDelta(B, d, linesA);
  const bx0 = B.x0 + r.x, by0 = B.y0 + r.y;
  assert.equal(bx0, 60);
  assert.ok(Math.abs(by0) < 1e-9, 'and the rows line up too');
  const lastA = 52.5, firstB = bx0 + 7.5;
  assert.ok(Math.abs((firstB - lastA) - 15) < 1e-9, 'the 15 mm pitch carries across');
});

test('x and y snap independently', () => {
  const d = { x: -(B.x0 - A.x1) - 0.5, y: 500 };   // y hopelessly far
  const r = snapDelta(B, d, linesA);
  assert.ok(r.sx);
  assert.equal(r.sy, null);
  assert.equal(r.y, 500, 'an unsnapped axis passes through untouched');
});

test('tolerance is respected at the boundary', () => {
  assert.ok(bestSnap([60 + SNAP_TOL - 1e-9], xA), 'just inside snaps');
  assert.equal(bestSnap([60 + SNAP_TOL + 1e-6], xA), null, 'just outside does not');
});

test('centreline snapping centres one board on another', () => {
  const wide = box(0, 0, 200, 20);
  const small = box(90, 40, 110, 60);          // centre 100, A centre is 100
  const lines = snapLines(['w'], () => wide, new Set());
  const r = snapDelta(small, { x: 1.2, y: 0 }, lines);
  assert.ok(Math.abs((small.x0 + small.x1) / 2 + r.x - 100) < 1e-9);
});
