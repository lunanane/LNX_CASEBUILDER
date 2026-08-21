// The sheet stock a case is plausibly cut from.
//
// Not a colour picker's worth of choice on purpose. These are materials you
// can actually buy, and "birch ply" says more about what the thing will look
// like than #c8a165 does. The per-layer controls are still there for anyone
// who wants a different sheet for the lid.
//
// `name` matters as much as `color`: the finish presets in finishes.js are
// matched off the material's name, so calling a layer `acrylic-smoked-3mm` is
// what makes it render translucent, not the hex value.

export const STOCK = [
  { label: 'birch ply', name: 'plywood', color: '#c8a165' },
  { label: 'dark ply', name: 'plywood', color: '#8a6034' },
  { label: 'MDF', name: 'mdf', color: '#b08d6a' },
  { label: 'clear acrylic', name: 'acrylic', color: '#dfe8f0' },
  { label: 'smoked acrylic', name: 'acrylic-smoked', color: '#33383f' },
  { label: 'black acrylic', name: 'acrylic', color: '#16181c' },
  { label: 'aluminium', name: 'aluminium', color: '#c4c8cc' },
  { label: 'brass', name: 'brass', color: '#c9a227' },
];

const THICKNESS = /(\d+(?:\.\d+)?)\s*mm/;

/** Rename a layer to a different stock, keeping the thickness it states.
 *
 *  A material name carries two things: what it is made of, and how thick the
 *  sheet is. Only the first is being changed here. Dropping the second would
 *  leave `plywood-3mm` as bare `acrylic`, and the thickness in the name is
 *  what anyone reading the cut list goes by.
 *
 *  The declared `thickness` field is untouched either way -- that is a
 *  structural number, and quietly changing it would move every board in the
 *  case.
 */
export function restockName(name, stock) {
  const mm = THICKNESS.exec(String(name ?? ''));
  return mm ? `${stock.name}-${mm[1]}mm` : stock.name;
}
