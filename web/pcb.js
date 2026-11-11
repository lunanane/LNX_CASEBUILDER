// A Eurorack panel made by a PCB service: the editor's half of it.
//
// The colours, finishes and limits come from the same table the backend
// validates against (pcb-fab.json, served at /api/pcb/palette). What lives
// here is the part that has to be instant: turning an uploaded image into
// the greyscale a scene stores, and thresholding it into coloured bands on
// a canvas while a slider moves -- the backend traces the same bands into
// polygons when the scene is built.
//
// Kept out of app.js so it can be tested headlessly.

/** Longest side, and total pixels, an upload is sampled down to. The same
 *  ceiling as the backend's ART_MAX_PIXELS. */
export const ART_MAX_SIDE = 512;
export const ART_MAX_PIXELS = 512 * 512;

/** What an engraving can be made of on a PCB panel. */
export const ELEMENT_LAYERS = ['silk', 'copper', 'substrate'];
/** ...and what a band of an artwork can be: `mask` leaves the board as is. */
export const ART_LAYERS = ['mask', 'silk', 'copper', 'substrate'];

/** The defaults a new PCB panel starts from (schema.PcbFab). */
export const PCB_DEFAULTS = { thickness: 1.6, mask: 'black', silk: 'white', finish: 'enig', ring: 0 };

export function pcbOf(scene) {
  const ep = scene?.case?.eurorack_panel;
  return ep && ep.fab === 'pcb' ? { ...PCB_DEFAULTS, ...(ep.pcb ?? {}) } : null;
}

/** 'gold' or 'silver', by the finish. */
export function metalWord(palette, finish) {
  return palette?.finish?.[finish]?.metal ?? 'gold';
}

/** What a layer is called on screen: copper is named for how it looks. */
export function layerLabel(palette, pcb, layer) {
  if (layer === 'copper') return metalWord(palette, pcb?.finish);
  if (layer === 'substrate') return 'bare board';
  if (layer === 'mask') return `${palette?.mask?.[pcb?.mask]?.label ?? 'mask'} (mask)`;
  return 'silk';
}

/** Preview sRGB of a layer on this board, as '#rrggbb'. */
export function layerColor(palette, pcb, layer) {
  if (!palette || !pcb) return '#888888';
  if (layer === 'silk') return palette.silk[pcb.silk]?.color ?? '#ffffff';
  if (layer === 'copper') return palette.finish[pcb.finish]?.color ?? '#d4af37';
  if (layer === 'substrate') return palette.substrate?.color ?? '#c8b77a';
  return palette.mask[pcb.mask]?.color ?? '#1a1a1a';
}

export function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex ?? ''));
  if (!m) return [128, 128, 128];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Which band a brightness falls in: 0 darkest, thresholds.length brightest.
 *  The same rule as the backend: a value AT a threshold is above it. */
export function bandOf(v, thresholds) {
  let b = 0;
  for (const t of thresholds) if (v >= t) b++;
  return b;
}

/** Sample size for an upload: the longest side at most ART_MAX_SIDE. */
export function fitSize(w, h, maxSide = ART_MAX_SIDE) {
  const s = Math.min(1, maxSide / Math.max(w, h));
  return [Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s))];
}

/** RGBA (canvas ImageData) to 8-bit luminance. Transparency reads as
 *  black -- the darkest band, which is usually "leave the mask". */
export function toGray(rgba, w, h) {
  const out = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const a = rgba[4 * i + 3] / 255;
    const y = 0.2126 * rgba[4 * i] + 0.7152 * rgba[4 * i + 1] + 0.0722 * rgba[4 * i + 2];
    out[i] = Math.round(y * a);
  }
  return out;
}

/** A separable Gaussian, edges extended -- the backend's blur, for the
 *  preview. `sigma` in pixels. */
export function blurGray(gray, w, h, sigma) {
  const src = Float32Array.from(gray);
  if (!(sigma >= 0.3)) return src;
  const r = Math.max(1, Math.round(3 * sigma));
  const k = [];
  let sum = 0;
  for (let i = -r; i <= r; i++) { const v = Math.exp(-0.5 * (i / sigma) ** 2); k.push(v); sum += v; }
  for (let i = 0; i < k.length; i++) k[i] /= sum;
  const tmp = new Float32Array(w * h);
  const out = new Float32Array(w * h);
  const clamp = (v, n) => (v < 0 ? 0 : v >= n ? n - 1 : v);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let acc = 0;
      for (let i = -r; i <= r; i++) acc += k[i + r] * src[y * w + clamp(x + i, w)];
      tmp[y * w + x] = acc;
    }
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let acc = 0;
      for (let i = -r; i <= r; i++) acc += k[i + r] * tmp[clamp(y + i, h) * w + x];
      out[y * w + x] = acc;
    }
  }
  return out;
}

/** The artwork as it will come out: each pixel its band's layer colour.
 *  Returns RGBA for a canvas the size of the raster. */
export function thresholdPreview(gray, art, palette, pcb) {
  const [w, h] = art.pixels;
  const px = art.width / w;
  const field = blurGray(gray, w, h, (art.blur ?? 0) / px);
  const colors = (art.layers ?? []).map((l) => hexToRgb(layerColor(palette, pcb, l)));
  const out = new Uint8ClampedArray(w * h * 4);
  const ts = art.thresholds ?? [128];
  for (let i = 0; i < w * h; i++) {
    const v = art.invert ? 255 - field[i] : field[i];
    const c = colors[bandOf(v, ts)] ?? [255, 0, 255];
    out[4 * i] = c[0]; out[4 * i + 1] = c[1]; out[4 * i + 2] = c[2]; out[4 * i + 3] = 255;
  }
  return out;
}

function toBase64(bytes) {
  if (typeof Buffer !== 'undefined') return Buffer.from(bytes).toString('base64');
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

function fromBase64(s) {
  if (typeof Buffer !== 'undefined') return new Uint8Array(Buffer.from(s, 'base64'));
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function pipe(bytes, stream) {
  const res = new Response(new Blob([bytes]).stream().pipeThrough(stream));
  return new Uint8Array(await res.arrayBuffer());
}

/** Greyscale to the scene's `data` string: zlib'd when the browser can
 *  ('deflate' in a CompressionStream is the zlib format), plain otherwise. */
export async function encodeGray(gray) {
  if (typeof CompressionStream !== 'undefined') {
    return 'z64:' + toBase64(await pipe(gray, new CompressionStream('deflate')));
  }
  return 'b64:' + toBase64(gray);
}

export async function decodeGray(data) {
  if (data.startsWith('z64:')) {
    return pipe(fromBase64(data.slice(4)), new DecompressionStream('deflate'));
  }
  if (data.startsWith('b64:')) return fromBase64(data.slice(4));
  throw new Error('artwork data starts z64: or b64:');
}

/** A new artwork entry, centred on a panel `panelW` wide and 128.5 tall. */
export function newArtwork(name, pixels, data, panelW, bands = 3) {
  const width = Math.min(panelW - 8, 40);
  return {
    name, at: [panelW / 2, 64.25], width, pixels, data,
    thresholds: bands === 3 ? [85, 170] : [128],
    layers: bands === 3 ? ['mask', 'silk', 'copper'] : ['mask', 'silk'],
    invert: false, blur: 0, min_feature: 0.2,
  };
}

/** Change the number of bands, keeping what can be kept. */
export function setBands(art, bands) {
  if (bands === art.layers.length) return art;
  if (bands === 3) {
    const t = art.thresholds[0] ?? 128;
    art.thresholds = [Math.max(0, Math.round(t * 0.66)), Math.min(255, Math.max(t, Math.round(t * 1.33)))];
    art.layers = [art.layers[0] ?? 'mask', 'silk', art.layers[art.layers.length - 1] === 'silk' ? 'copper' : art.layers[art.layers.length - 1]];
  } else {
    art.thresholds = [Math.round((art.thresholds[0] + art.thresholds[art.thresholds.length - 1]) / 2)];
    art.layers = [art.layers[0], art.layers[art.layers.length - 1]];
  }
  return art;
}
