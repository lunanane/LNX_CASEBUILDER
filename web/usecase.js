// Which of the four use cases a scene is, and which controls belong to it.
//
// The editor grew one pipeline at a time -- layered case, Eurorack frame,
// module panel, box of plates with rack bays -- and each brought its own
// controls into the one shared inspector. Most of them were simply greyed out
// in the other three, which left every project wading through the settings of
// all four. A control now says which use cases it serves (`data-uses="layered
// box"`), and anything that does not serve the open scene is hidden.
//
// Kept out of app.js so it can be tested headlessly.

/** The four use cases, in the order the project chooser offers them. */
export const USE_CASES = ['layered', 'box', 'frame', 'panel'];

/** The server-side starter each use case begins from. A box is a parts scene
 *  with `case.style: box`, a panel one with `case.eurorack_panel`. */
export const STARTER_KIND = {
  layered: 'parts', box: 'box', frame: 'rack', panel: 'panel',
};

/** Read off what the scene already stores -- no field of its own, so every
 *  scene written before the split opens as what it always was. A panel is
 *  checked before the style: its case says `style: layered` too. */
export function useCaseOf(scene) {
  if (!scene) return 'layered';
  if (scene.kind === 'rack') return 'frame';
  if (scene.case?.eurorack_panel) return 'panel';
  if (scene.case?.style === 'box') return 'box';
  return 'layered';
}

/** Does a `data-uses` tag include this use case? No tag means everywhere. */
export function usedIn(tag, useCase) {
  if (tag == null) return true;
  const list = String(tag).trim().split(/\s+/).filter(Boolean);
  return list.length === 0 || list.includes(useCase);
}

/** Hide every tagged element that does not serve `useCase`, show the rest.
 *  Returns the number hidden, which is mostly useful to tests. */
export function applyUseCase(elements, useCase) {
  let hidden = 0;
  for (const el of elements) {
    const off = !usedIn(el.dataset?.uses ?? el.getAttribute?.('data-uses'), useCase);
    el.hidden = off;
    if (off) hidden++;
  }
  return hidden;
}
