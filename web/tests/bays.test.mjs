import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BAY_DEFAULTS, HP, ROW_FORMATS, bayWidth, findModule, firstFreeSlot,
  moduleEntries, nearestSlot, newBayAt, removeModule, roughBayBox, rowsLabel,
  setModuleSlot,
} from '../bays.js';

test('a new bay is 28 HP, as the engine assumes when none is set', () => {
  assert.equal(BAY_DEFAULTS.hp, 28);
  assert.equal(bayWidth(undefined), 28 * HP);
  assert.equal(bayWidth(84), 84 * HP);
});

test('the row formats are the frame\'s', () => {
  assert.deepEqual(ROW_FORMATS, ['3U', '1U-intellijel', '1U-pulplogic']);
  assert.equal(rowsLabel([{ format: '1U-intellijel' }, { format: '3U' }]),
    '1U-intellijel + 3U');
  assert.equal(rowsLabel(undefined), '3U');
});

test('an empty scene puts a new bay at the origin', () => {
  assert.deepEqual(newBayAt([]), [0, 0]);
});

test('a new bay goes clear of every board, not on top of them', () => {
  const boards = [{ x0: -10, y0: 5, x1: 60, y1: 70 }, { x0: 100, y0: -20, x1: 160, y1: 40 }];
  const [x, y] = newBayAt(boards, 20);
  assert.equal(x, 180);
  assert.equal(y, -20);
  const bay = { at: [x, y], hp: 28, rows: [{ format: '3U' }] };
  const box = roughBayBox(bay);
  for (const b of boards) assert.ok(box.x0 > b.x1, 'the bay\'s cheek is right of every board');
});

test('a second new bay goes clear of the first', () => {
  const first = { at: [0, 0], hp: 28, rows: [{ format: '3U' }] };
  const [x] = newBayAt([roughBayBox(first)]);
  assert.ok(x - 3 > 28 * HP + 3);
});

test('bare ids and slotted entries read alike', () => {
  const bay = { modules: ['amy', { id: 'b', hp_slot: 10, row: 1 }, { id: 'c' }] };
  assert.deepEqual(moduleEntries(bay), [
    { id: 'amy' }, { id: 'b', hp_slot: 10, row: 1 }, { id: 'c', hp_slot: 0, row: 0 }]);
  assert.deepEqual(moduleEntries({}), []);
});

test('a module is found in whichever bay lists it', () => {
  const a = { id: 'a', modules: ['x'] };
  const b = { id: 'b', modules: [{ id: 'y', hp_slot: 2, row: 0 }] };
  assert.equal(findModule([a, b], 'y').bay, b);
  assert.equal(findModule([a, b], 'x').index, 0);
  assert.equal(findModule([a, b], 'z'), null);
});

test('setting a slot turns a bare id into a slotted entry, once', () => {
  const bay = { modules: ['amy'] };
  setModuleSlot(bay, 'amy', 4.4, 0);
  assert.deepEqual(bay.modules, [{ id: 'amy', hp_slot: 4, row: 0 }]);
  setModuleSlot(bay, 'new', 10);
  assert.equal(bay.modules.length, 2);
  removeModule(bay, 'amy');
  assert.deepEqual(bay.modules, [{ id: 'new', hp_slot: 10, row: 0 }]);
});

test('the first free slot skips the modules already there', () => {
  const taken = [{ slot: 0, hp: 10, row: 0 }, { slot: 14, hp: 4, row: 0 }];
  assert.equal(firstFreeSlot(taken, 4, 28), 10);
  assert.equal(firstFreeSlot(taken, 6, 28), 18);
  assert.equal(firstFreeSlot(taken, 11, 28), null);
  assert.equal(firstFreeSlot(taken, 10, 28, 1), 0, 'another row is empty');
});

// The 28 HP bay of the hybrid fixture, as /api/resolve describes it.
const BAY = { x0: 0, hp: 28, rows: [{ y_mid: 66.25 }] };
const amy = (x0, y0) => ({ x0, y0, x1: x0 + 50.5, y1: y0 + 128.5 });

test('a dragged module lands on the nearest HP slot and its row\'s centre', () => {
  const s = nearestSlot(amy(12.0, 5.0), BAY, 10);
  assert.equal(s.hp_slot, 2);
  assert.equal(s.row, 0);
  assert.ok(Math.abs(12.0 + s.dx - 2 * HP) < 1e-9);
  assert.ok(Math.abs(5.0 + 64.25 + s.dy - 66.25) < 1e-9);
});

test('a module dragged past either end stays on the rails', () => {
  assert.equal(nearestSlot(amy(-40, 2), BAY, 10).hp_slot, 0);
  assert.equal(nearestSlot(amy(500, 2), BAY, 10).hp_slot, 18);
});

test('in a two-row bay the nearer row wins', () => {
  const two = { x0: 0, hp: 28, rows: [{ y_mid: 66.25 }, { y_mid: 199.6 }] };
  assert.equal(nearestSlot(amy(0, 120), two, 10).row, 1);
  assert.equal(nearestSlot(amy(0, 10), two, 10).row, 0);
  assert.equal(nearestSlot(amy(0, 0), { x0: 0, hp: 28, rows: [] }, 10), null);
});
