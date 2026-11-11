import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';

import {
  ART_MAX_PIXELS, bandOf, decodeGray, encodeGray, fitSize, layerColor, layerLabel,
  newArtwork, pcbOf, setBands, thresholdPreview, toGray,
} from '../pcb.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const palette = JSON.parse(fs.readFileSync(path.join(here, '..', 'pcb-fab.json'), 'utf8'));
const pcb = { thickness: 1.6, mask: 'black', silk: 'white', finish: 'enig', ring: 0 };

test('only a pcb panel has pcb settings, defaults filled in', () => {
  assert.equal(pcbOf({ case: { eurorack_panel: { hp: 10 } } }), null);
  const p = pcbOf({ case: { eurorack_panel: { hp: 10, fab: 'pcb' } } });
  assert.equal(p.mask, 'black');
  assert.equal(p.thickness, 1.6);
});

test('layers take their colours from the shared table', () => {
  assert.equal(layerColor(palette, pcb, 'mask'), palette.mask.black.color);
  assert.equal(layerColor(palette, pcb, 'silk'), palette.silk.white.color);
  assert.equal(layerColor(palette, pcb, 'copper'), palette.finish.enig.color);
  assert.equal(layerColor(palette, pcb, 'substrate'), palette.substrate.color);
  assert.equal(layerLabel(palette, pcb, 'copper'), 'gold');
  assert.equal(layerLabel(palette, { ...pcb, finish: 'hasl' }, 'copper'), 'silver');
});

test('bands split the way the backend splits them', () => {
  assert.equal(bandOf(0, [85, 170]), 0);
  assert.equal(bandOf(85, [85, 170]), 1);       // at a threshold is above it
  assert.equal(bandOf(255, [85, 170]), 2);
  assert.equal(bandOf(127, [128]), 0);
});

test('an upload is sampled down inside the stored limit', () => {
  const [w, h] = fitSize(4000, 3000);
  assert.ok(w * h <= ART_MAX_PIXELS && Math.max(w, h) === 512);
  assert.deepEqual(fitSize(100, 50), [100, 50]);
});

test('transparent is dark, white is bright', () => {
  const g = toGray(new Uint8ClampedArray([255, 255, 255, 255, 255, 255, 255, 0]), 2, 1);
  assert.deepEqual([...g], [255, 0]);
});

test('the preview paints each band its layer colour', () => {
  const art = newArtwork('a', [3, 1], '', 50.5, 3);
  const rgba = thresholdPreview(Uint8Array.from([0, 128, 255]), art, palette, pcb);
  const hex = (i) => '#' + [0, 1, 2].map((k) => rgba[4 * i + k].toString(16).padStart(2, '0')).join('');
  assert.equal(hex(0), palette.mask.black.color);
  assert.equal(hex(1), palette.silk.white.color);
  assert.equal(hex(2), palette.finish.enig.color);
  art.invert = true;
  const inv = thresholdPreview(Uint8Array.from([0, 128, 255]), art, palette, pcb);
  assert.equal(inv[0], rgba[8]);
});

test('switching between two and three bands keeps the layers consistent', () => {
  const art = newArtwork('a', [1, 1], '', 50.5, 2);
  setBands(art, 3);
  assert.equal(art.layers.length, 3);
  assert.equal(art.thresholds.length, 2);
  assert.ok(art.thresholds[0] <= art.thresholds[1]);
  setBands(art, 2);
  assert.equal(art.layers.length, 2);
  assert.equal(art.thresholds.length, 1);
});

test('stored greyscale is zlib the backend can read, and round-trips', async () => {
  const gray = Uint8Array.from({ length: 1000 }, (_, i) => i % 256);
  const data = await encodeGray(gray);
  assert.ok(data.startsWith('z64:'));
  const raw = zlib.inflateSync(Buffer.from(data.slice(4), 'base64'));
  assert.deepEqual([...raw], [...gray]);
  assert.deepEqual([...(await decodeGray(data))], [...gray]);
});
