import assert from 'node:assert/strict';
import test from 'node:test';

import * as bays from '../bays.js';
import {
  BAY_DEFAULTS, HP, ROW_FORMATS, bayWidth, newBayAt, roughBayBox, rowsLabel,
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

test('a bay knows nothing of modules: no slots, no picker', () => {
  for (const name of ['moduleEntries', 'findModule', 'firstFreeSlot',
    'setModuleSlot', 'removeModule', 'nearestSlot']) {
    assert.equal(bays[name], undefined, name);
  }
});
