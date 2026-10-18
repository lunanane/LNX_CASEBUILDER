// M3: the import review dialog's state, without a browser.

import assert from 'node:assert/strict';
import test from 'node:test';

import { choiceOf, importQuery, reviewHtml } from '../importer.js';

const PREVIEW = {
  file: '4741 1.5-inch OLED.step',
  files: ['4741 1.5-inch OLED.stl', '4741 1.5-inch OLED.step'],
  flipped: true,
  part: { name: 'OLED <1.5">', outline: { type: 'poly', points: [[0, 0], [1, 0], [1, 1]] },
          holes: [{}, {}, {}, {}] },
  connectors: [{ name: 'stemma_qt_left', type: 'stemma_qt', face: '-x' },
               { name: 'stemma_qt_right', type: 'stemma_qt', face: '+x' }],
  warnings: ['2 connector(s) proposed from body sizes'],
};

test('the query carries only the choices that were made', () => {
  assert.equal(importQuery(), '');
  assert.equal(importQuery({ flip: false }), '?flip=false');
  assert.equal(importQuery({ file: 'a b.step', flip: true }), '?file=a+b.step&flip=true');
});

test('the reviewed choice is what the preview settled on', () => {
  assert.deepEqual(choiceOf(PREVIEW), { file: '4741 1.5-inch OLED.step', flip: true });
  assert.deepEqual(choiceOf({ file: 'x.stl' }), { file: 'x.stl', flip: false });
});

test('every model is offered, and the measured one is selected', () => {
  const html = reviewHtml(PREVIEW);
  assert.match(html, /<option value="4741 1.5-inch OLED.stl">/);
  assert.match(html, /<option value="4741 1.5-inch OLED.step" selected>/);
  assert.match(html, /id="imp-flip" checked/);
});

test('proposed connectors are shown as proposals, and warnings as warnings', () => {
  const html = reviewHtml(PREVIEW);
  assert.match(html, /stemma_qt_left<\/b> \(stemma_qt, facing -x\) -- proposed/);
  assert.match(html, /<h3>check<\/h3>/);
  assert.match(html, /4 mounting holes/);
});

test('no connectors says what that costs', () => {
  const html = reviewHtml({ ...PREVIEW, connectors: [] });
  assert.match(html, /no cable room/);
});

test('names from a vendor are escaped, not injected', () => {
  assert.match(reviewHtml(PREVIEW), /OLED &lt;1.5&quot;&gt;/);
});
