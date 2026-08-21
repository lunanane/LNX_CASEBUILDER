import assert from 'node:assert/strict';
import test from 'node:test';

import { presetFor } from '../finishes.js';
import { STOCK, restockName } from '../stock.js';

test('restocking keeps the thickness the name states', () => {
  const smoked = STOCK.find((s) => s.label === 'smoked acrylic');
  assert.equal(restockName('plywood-3mm', smoked), 'acrylic-smoked-3mm');
  assert.equal(restockName('aluminium-2mm', smoked), 'acrylic-smoked-2mm');
  // decimals are real: 1.5 mm ply exists and rounding it would be a lie
  assert.equal(restockName('ply-1.5mm', smoked), 'acrylic-smoked-1.5mm');
});

test('a name with no thickness in it just becomes the stock', () => {
  const brass = STOCK.find((s) => s.label === 'brass');
  assert.equal(restockName('felt', brass), 'brass');
  assert.equal(restockName('', brass), 'brass');
  assert.equal(restockName(undefined, brass), 'brass');
});

test('restocking twice is not cumulative', () => {
  // `acrylic-smoked-3mm` re-stocked as ply must not become
  // `plywood-acrylic-smoked-3mm`
  const smoked = STOCK.find((s) => s.label === 'smoked acrylic');
  const ply = STOCK.find((s) => s.label === 'birch ply');
  const once = restockName('plywood-3mm', smoked);
  assert.equal(restockName(once, ply), 'plywood-3mm');
});

test('every stock renders as the material it claims to be', () => {
  // The finish is matched off the NAME, so a stock whose name does not
  // resolve would be the right colour and the wrong material -- black
  // acrylic that is opaque like plywood, say.
  for (const s of STOCK) {
    const preset = presetFor(restockName('x-3mm', s)).preset;
    assert.ok(preset, `${s.label} resolves to no finish`);
  }
  assert.equal(presetFor(restockName('x-3mm',
    STOCK.find((s) => s.label === 'smoked acrylic'))).preset, 'smoked');
  assert.equal(presetFor(restockName('x-3mm',
    STOCK.find((s) => s.label === 'clear acrylic'))).preset, 'acrylic');
});

test('every stock has a usable colour', () => {
  for (const s of STOCK) {
    assert.match(s.color, /^#[0-9a-f]{6}$/i, s.label);
    assert.ok(s.label && s.name, 'a stock needs a label and a material name');
  }
});
