import assert from 'node:assert/strict';
import test from 'node:test';

import { createHistory } from '../history.js';

const scene = (n) => ({ placements: [{ id: 'a', pos: [n, 0, 0] }] });

test('nothing to undo on a fresh history', () => {
  const h = createHistory();
  assert.equal(h.canUndo, false);
  assert.equal(h.undo(scene(1)), null);
});

test('undo returns the state from before the edit', () => {
  const h = createHistory();
  const a = scene(1);
  h.push(a);
  const b = scene(2);
  assert.deepEqual(h.undo(b), a);
});

test('undo then redo round-trips', () => {
  const h = createHistory();
  h.push(scene(1));
  const b = scene(2);
  const back = h.undo(b);
  assert.deepEqual(back, scene(1));
  assert.deepEqual(h.redo(back), b);
});

test('snapshots are deep copies, not references', () => {
  const h = createHistory();
  const a = scene(1);
  h.push(a);
  a.placements[0].pos[0] = 999;          // mutate after pushing
  assert.equal(h.undo(scene(2)).placements[0].pos[0], 1);
});

test('a new edit clears the redo branch', () => {
  const h = createHistory();
  h.push(scene(1));
  h.undo(scene(2));
  assert.equal(h.canRedo, true);
  h.push(scene(3));
  assert.equal(h.canRedo, false, 'you cannot redo into a future you overwrote');
});

test('a coalesced run is a single step', () => {
  const h = createHistory();
  // a drag: one push accepted, the rest folded into it
  assert.equal(h.push(scene(1), 'move:a'), true);
  assert.equal(h.push(scene(2), 'move:a'), false);
  assert.equal(h.push(scene(3), 'move:a'), false);
  assert.equal(h.depth, 1);
  assert.deepEqual(h.undo(scene(4)), scene(1), 'undo goes to before the drag');
});

test('seal ends a run so the next drag is its own step', () => {
  const h = createHistory();
  h.push(scene(1), 'move:a');
  h.seal();
  h.push(scene(2), 'move:a');
  assert.equal(h.depth, 2);
});

test('different keys are different steps', () => {
  const h = createHistory();
  h.push(scene(1), 'move:a');
  h.push(scene(2), 'move:b');
  assert.equal(h.depth, 2);
});

test('undo ends any open run, so the next edit is not folded in', () => {
  const h = createHistory();
  h.push(scene(1), 'move:a');
  h.undo(scene(2));
  assert.equal(h.push(scene(3), 'move:a'), true);
});

test('the stack is bounded and drops the oldest', () => {
  const h = createHistory(3);
  for (let i = 0; i < 10; i++) h.push(scene(i));
  assert.equal(h.depth, 3);
  assert.deepEqual(h.undo(scene(99)), scene(9), 'newest kept');
});

test('clear forgets everything', () => {
  const h = createHistory();
  h.push(scene(1));
  h.undo(scene(2));
  h.clear();
  assert.equal(h.canUndo, false);
  assert.equal(h.canRedo, false);
});
