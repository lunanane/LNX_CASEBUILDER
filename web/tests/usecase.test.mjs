import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { applyUseCase, STARTER_KIND, USE_CASES, useCaseOf, usedIn } from '../usecase.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', 'index.html'), 'utf8');

test('a scene is read as the use case it already stores', () => {
  assert.equal(useCaseOf({ kind: 'rack', case: {} }), 'frame');
  assert.equal(useCaseOf({ kind: 'parts', case: { style: 'box' } }), 'box');
  assert.equal(useCaseOf({ kind: 'parts', case: { style: 'layered' } }), 'layered');
  assert.equal(useCaseOf({ kind: 'parts' }), 'layered');
  assert.equal(useCaseOf(null), 'layered');
});

test('a panel is a panel, although its case says layered', () => {
  const s = { kind: 'parts', case: { style: 'layered', eurorack_panel: { hp: 10 } } };
  assert.equal(useCaseOf(s), 'panel');
});

test('an untagged control is everywhere, a tagged one only where it says', () => {
  assert.ok(usedIn(undefined, 'frame'));
  assert.ok(usedIn('', 'frame'));
  assert.ok(usedIn('layered box', 'box'));
  assert.ok(!usedIn('layered box', 'frame'));
  assert.ok(!usedIn('layered', 'panel'));
});

test('applying a use case hides exactly what does not serve it', () => {
  const els = ['layered', 'box layered', 'frame', 'panel box'].map(
    (uses) => ({ dataset: { uses }, hidden: false }));
  assert.equal(applyUseCase(els, 'box'), 2);
  assert.deepEqual(els.map((e) => e.hidden), [true, false, true, false]);
  // and switching back undoes it -- nothing stays hidden from a former use
  applyUseCase(els, 'layered');
  assert.deepEqual(els.map((e) => e.hidden), [false, false, true, true]);
});

test('every use case has a starter, and every tag in the page names one', () => {
  for (const u of USE_CASES) assert.ok(STARTER_KIND[u], u);
  const tags = [...html.matchAll(/data-uses="([^"]*)"/g)].flatMap((m) => m[1].split(/\s+/));
  assert.ok(tags.length > 10, 'expected the page to tag its controls');
  const unknown = tags.filter((t) => t && !USE_CASES.includes(t));
  assert.deepEqual(unknown, [], `data-uses names no use case: ${unknown}`);
});

test('the frame tab is the frame\'s alone, and the frame has no palette', () => {
  assert.match(html, /data-pane="pane-frame"[^>]*data-uses="frame"|data-uses="frame"[^>]*data-pane="pane-frame"/);
  assert.match(html, /<aside id="palette" data-uses="layered box panel"/);
});
