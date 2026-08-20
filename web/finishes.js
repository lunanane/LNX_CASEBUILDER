// How a case material should look when the preview is rendered rather than
// drawn schematically.
//
// The engine only cares that a material has a thickness and a kerf. For a
// picture we also need to know that plywood is matte and pale, that acrylic is
// glossy and see-through, and that aluminium is neither. The material's `name`
// carries that: `plywood-3mm` is plywood, `acrylic-clear-3mm` is acrylic. The
// `color` declared in the scene always wins over the preset.

export const FINISHES = {
  plywood:   { color: 0xc8a165, roughness: 0.78, metalness: 0.02, opacity: 1.0 },
  mdf:       { color: 0xb08d6a, roughness: 0.92, metalness: 0.0,  opacity: 1.0 },
  acrylic:   { color: 0xdfe8f0, roughness: 0.08, metalness: 0.0,  opacity: 0.42 },
  smoked:    { color: 0x33383f, roughness: 0.12, metalness: 0.0,  opacity: 0.62 },
  aluminium: { color: 0xc4c8cc, roughness: 0.34, metalness: 0.95, opacity: 1.0 },
  brass:     { color: 0xc9a227, roughness: 0.28, metalness: 0.95, opacity: 1.0 },
  steel:     { color: 0x9aa0a6, roughness: 0.30, metalness: 0.92, opacity: 1.0 },
  cardboard: { color: 0xa8916b, roughness: 0.96, metalness: 0.0,  opacity: 1.0 },
  felt:      { color: 0x5a5f66, roughness: 1.0,  metalness: 0.0,  opacity: 1.0 },
};

export const DEFAULT_FINISH = {
  color: 0x9aa3ad, roughness: 0.6, metalness: 0.1, opacity: 1.0,
};

// Precedence, not length: a qualifier like "smoked" is shorter than the base
// material it qualifies, so `acrylic-smoked-3mm` has to resolve to smoked and
// not to acrylic. Qualifiers therefore come first in this list, and the first
// one found in the name wins.
export const PRECEDENCE = [
  'smoked',
  'plywood', 'mdf', 'cardboard', 'felt',
  'acrylic',
  'aluminium', 'brass', 'steel',
];

export function presetFor(name) {
  const n = String(name || '').toLowerCase();
  for (const key of PRECEDENCE) {
    if (n.includes(key)) return { ...FINISHES[key], preset: key };
  }
  return { ...DEFAULT_FINISH, preset: null };
}

export function parseColor(value) {
  if (value == null) return null;
  if (typeof value === 'number') return value;
  const s = String(value).trim().replace(/^#/, '');
  if (!/^[0-9a-f]{6}$/i.test(s)) return null;
  return parseInt(s, 16);
}

/** PBR parameters for one case material. */
export function finishFor(material) {
  const f = presetFor(material?.name);
  const explicit = parseColor(material?.color);
  return { ...f, color: explicit == null ? f.color : explicit };
}
