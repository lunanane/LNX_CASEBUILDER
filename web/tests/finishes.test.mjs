import assert from 'node:assert/strict';
import test from 'node:test';

import { DEFAULT_FINISH, FINISHES, PRECEDENCE, finishFor, parseColor, presetFor } from '../finishes.js';

test('a material name picks its finish', () => {
  assert.equal(presetFor('plywood-3mm').preset, 'plywood');
  assert.equal(presetFor('aluminium-2mm').preset, 'aluminium');
  assert.equal(presetFor('acrylic-clear-3mm').preset, 'acrylic');
});

test('a qualifier beats the base material it qualifies', () => {
  // "acrylic-smoked-3mm" contains both, and smoked is the more specific one --
  // note it is also the SHORTER word, which is why precedence beats length
  assert.equal(presetFor('acrylic-smoked-3mm').preset, 'smoked');
  assert.equal(presetFor('smoked-acrylic-3mm').preset, 'smoked');
});

test('every preset is reachable by name', () => {
  for (const key of Object.keys(FINISHES)) {
    assert.equal(presetFor(`${key}-3mm`).preset, key, key);
  }
});

test('an unknown material still renders as something', () => {
  const f = presetFor('unobtainium-9mm');
  assert.equal(f.preset, null);
  assert.equal(f.color, DEFAULT_FINISH.color);
  assert.ok(f.roughness > 0);
});

test('presets are physically sane', () => {
  for (const [name, f] of Object.entries(FINISHES)) {
    assert.ok(f.roughness >= 0 && f.roughness <= 1, `${name} roughness`);
    assert.ok(f.metalness >= 0 && f.metalness <= 1, `${name} metalness`);
    assert.ok(f.opacity > 0 && f.opacity <= 1, `${name} opacity`);
  }
  assert.ok(FINISHES.aluminium.metalness > 0.5, 'metal should be metallic');
  assert.ok(FINISHES.plywood.metalness < 0.1, 'wood should not be');
  assert.ok(FINISHES.acrylic.opacity < 1, 'acrylic should be see-through');
});

test('colours parse from the scene, in the forms a scene actually uses', () => {
  assert.equal(parseColor('#c8a165'), 0xc8a165);
  assert.equal(parseColor('c8a165'), 0xc8a165);
  assert.equal(parseColor(0x123456), 0x123456);
  assert.equal(parseColor('not a colour'), null);
  assert.equal(parseColor(null), null);
});

test('a declared colour overrides the preset but keeps its feel', () => {
  const f = finishFor({ name: 'plywood-3mm', color: '#204080' });
  assert.equal(f.color, 0x204080, 'colour comes from the scene');
  assert.equal(f.roughness, FINISHES.plywood.roughness, 'but it is still matte wood');
});

test('a material with no colour falls back to the preset colour', () => {
  assert.equal(finishFor({ name: 'brass-1mm' }).color, FINISHES.brass.color);
});

test('a garbage colour does not poison the material', () => {
  const f = finishFor({ name: 'plywood-3mm', color: 'rgb(1,2,3)' });
  assert.equal(f.color, FINISHES.plywood.color);
});


test('precedence covers every preset, so none is unreachable', () => {
  assert.deepEqual([...PRECEDENCE].sort(), Object.keys(FINISHES).sort());
});
