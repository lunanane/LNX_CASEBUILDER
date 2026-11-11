// The editor's shell was rebuilt -- menus, four panes, a catalogue box, an
// engraving panel -- by editing index.html and app.js separately. Nothing
// checks that they still agree, and the failure mode is silent: `$('btn-save')`
// returns null, the handler is never attached, and the button simply does
// nothing when clicked. No error, no clue.
//
// These are structural checks, not a substitute for opening the page. They
// catch the mistake that restructuring actually makes.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', 'index.html'), 'utf8');
const js = fs.readFileSync(path.join(here, '..', 'app.js'), 'utf8');

const staticIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));

/** Ids built at runtime, via innerHTML in a render function -- in app.js, or
 *  in a module that renders markup for it (the import review, importer.js). */
const rendered = js + fs.readFileSync(path.join(here, '..', 'importer.js'), 'utf8');
const builtIds = new Set(
  [...rendered.matchAll(/id=["'`]([a-z][\w-]*)["'`]/g)].map((m) => m[1]));

/** Ids app.js looks up with $(). Template literals are skipped: those are
 *  computed and cannot be checked statically. */
const lookups = new Set([...js.matchAll(/\$\('([^']+)'\)/g)].map((m) => m[1]));

test('every element app.js looks up actually exists', () => {
  const missing = [...lookups].filter(
    (id) => !staticIds.has(id) && !builtIds.has(id));
  assert.deepEqual(missing, [],
    `app.js calls $() on ids that are in neither index.html nor any render ` +
    `function: ${missing.join(', ')}`);
});

test('no id is declared twice', () => {
  const all = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
  const seen = new Set();
  const dupes = all.filter((id) => seen.has(id) || (seen.add(id), false));
  assert.deepEqual(dupes, [], `duplicate ids in index.html: ${dupes}`);
});

test('every menu item is wired to something', () => {
  // A menu entry that looks clickable and does nothing is worse than a
  // missing one: it reads as a broken feature rather than an absent one.
  const menuButtons = [...html.matchAll(
    /<div class="menu-pop"[\s\S]*?<\/div>/g)]
    .flatMap((block) => [...block[0].matchAll(/<button id="([^"]+)"/g)])
    .map((m) => m[1]);

  assert.ok(menuButtons.length >= 10, 'expected a populated menu bar');
  const unwired = menuButtons.filter((id) => !lookups.has(id));
  assert.deepEqual(unwired, [], `menu items with no handler: ${unwired}`);
});

test('every pane a tab points at exists', () => {
  const panes = [...html.matchAll(/data-pane="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(panes.length >= 3, 'expected several inspector panes');
  for (const id of panes) {
    assert.ok(staticIds.has(id), `tab points at a missing pane: ${id}`);
  }
  // exactly one starts visible, or the inspector opens blank or doubled
  const hidden = panes.filter((id) => {
    const m = new RegExp(`id="${id}"[^>]*`).exec(html);
    return m && m[0].includes('hidden');
  });
  assert.equal(hidden.length, panes.length - 1,
    'exactly one pane should start visible');
});

test('the checkboxes the header lost are still somewhere', () => {
  // These moved from the toolbar into the view menu during the rebuild.
  // Losing one would silently drop a diagnostic overlay.
  for (const id of ['chk-corridors', 'chk-panels', 'chk-openings',
                    'chk-supports', 'chk-grid', 'chk-snap', 'chk-case',
                    'chk-render']) {
    assert.ok(staticIds.has(id), `${id} vanished in the rebuild`);
  }
});

test('the page loads no external resources', () => {
  // The editor is meant to work with the network unplugged: three.js is
  // vendored, the environments are vendored, and a stray CDN link would only
  // be discovered by somebody offline.
  const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1]);
  const external = urls.filter((u) => /^(https?:)?\/\//.test(u));
  assert.deepEqual(external, [], `external resources: ${external}`);
});

test('the import map points at files that exist', () => {
  const map = /<script type="importmap">([\s\S]*?)<\/script>/.exec(html);
  assert.ok(map, 'no import map');
  for (const [, target] of map[1].matchAll(/"(\.\/[^"]+)"/g)) {
    const p = path.join(here, '..', target);
    assert.ok(fs.existsSync(p), `import map points at a missing file: ${target}`);
  }
});

test('every module app.js imports is present', () => {
  for (const [, spec] of js.matchAll(/from '(\.\/[^']+)'/g)) {
    const p = path.join(here, '..', spec);
    assert.ok(fs.existsSync(p), `missing module: ${spec}`);
  }
});

// The menu's own behaviour is tested by running it, in menu.test.mjs --
// grepping the source for the right shape was how it got shipped broken twice.
// All that is left to check here is that the page still uses that tested code.

test('the menu bar uses the module the tests exercise', () => {
  assert.match(js, /import \{ wireMenus \} from '\.\/menu\.js'/,
    'app.js must delegate to menu.js, not keep its own copy');
  assert.ok(!/function wireMenus\s*\(/.test(js),
    'a second copy of the menu logic will drift away from the tested one');
});

test('the menu ignores the pointer entirely', () => {
  // Three attempts at this bug all involved hover. The behaviour it buys --
  // sliding along the bar to switch menus -- is a convenience; being able to
  // click an item is not. So there is no pointer handling at all now, and the
  // cheapest way to keep it that way is to check that none exists.
  const menu = fs.readFileSync(path.join(here, '..', 'menu.js'), 'utf8');
  const code = menu.replace(/\/\/[^\n]*/g, '');   // comments discuss it freely
  for (const evt of ['pointerenter', 'pointerleave', 'pointerover',
                     'pointerout', 'pointermove', 'pointerdown',
                     'mouseenter', 'mouseover', 'mouseleave']) {
    assert.ok(!code.includes(evt),
      `menu.js listens to ${evt}; an open menu must only react to clicks`);
  }

  // And the invisible strip that used to bridge the gap between button and
  // popup goes with it: 210 px wide at z-index 30, across its neighbours.
  const css = fs.readFileSync(path.join(here, '..', 'style.css'), 'utf8');
  assert.ok(!/\.menu-pop::before/.test(css), 'the bridge is no longer needed');
});

test('exporting does not depend on the menu', () => {
  // It is the last thing you do every session, and it spent three rounds
  // being unreachable because it lived only in a dropdown.
  assert.ok(staticIds.has('btn-svg-bar'), 'no export button on the bar');
  assert.match(js, /\$\('btn-svg-bar'\)\.onclick/, 'the bar button is not wired');
});

// Exporting produced no file and no error, twice over: a popup that the
// blocker ate, and then a click the menu swallowed. Both were silent, which
// is the part worth pinning.

test('nothing opens a popup window', () => {
  // window.open() called after an await is no longer attributable to the click
  // that started it, so a blocker discards it and the user sees nothing at
  // all. Exports save to a file instead.
  assert.ok(!/window\.open\s*\(/.test(js),
    'window.open is unreliable here; save the file or show it in the modal');
});

/** The body of a top-level `$('id').onclick = async () => { ... };`
 *
 *  Sliced by index rather than matched by a built-up RegExp: escaping a
 *  pattern through a template literal is its own small nightmare, and getting
 *  it wrong here fails the test rather than the code. */
function handlerBody(id) {
  const head = `$('${id}').onclick = async () => {`;
  const at = js.indexOf(head);
  if (at < 0) return null;
  const end = js.indexOf('\n};', at);
  return end < 0 ? null : js.slice(at + head.length, end);
}

test('both exports report success and failure', () => {
  // "Nothing happened" was the actual bug report. Whatever an export does, it
  // has to say so. Both buttons delegate to one sheet-export function, so the
  // reporting lives there.
  for (const id of ['btn-svg', 'btn-dxf']) {
    assert.match(js, new RegExp(
      `\\$\\('${id}'\\)\\.onclick = async \\(\\) => exportSheets\\(`),
      `${id} does not delegate to exportSheets`);
  }
  const at = js.indexOf('async function exportSheets(');
  assert.ok(at > 0, 'no exportSheets');
  const body = js.slice(at, js.indexOf('\n}\n', at));
  assert.match(body, /catch\s*\(/, 'exportSheets swallows failures');
  assert.match(body, /status\(/, 'exportSheets never reports anything');
  // Firefox has no directory picker; the zip fallback is not optional.
  assert.match(body, /showDirectoryPicker/, 'no folder-picker path');
  assert.match(body, /saveBlob\(/, 'no zip fallback for browsers without one');
});

test('saving falls back when there is no save dialog', () => {
  // showSaveFilePicker is Chrome and Edge only, and needs a live user gesture
  // even there. Firefox must still end up with the file.
  const fn = /async function saveBlob\(([\s\S]*?)\n\}/.exec(js);
  assert.ok(fn, 'no saveBlob helper');
  assert.match(fn[1], /window\.showSaveFilePicker/);
  assert.match(fn[1], /downloadBlob\(/, 'no fallback path');
  assert.match(fn[1], /AbortError/, 'a cancelled save must not read as an error');
});

test('the frame pane offers both constructions, and every control is wired', () => {
  // M7: a U-channel frame and a laser-cut one are chosen in the same pane
  assert.match(html, /<select id="fr-style">[\s\S]*value="u_channel"[\s\S]*value="laser"/);
  assert.match(html, /<select id="fr-joint">[\s\S]*value="tslot"[\s\S]*value="finger"/);
  for (const id of ['fr-style', 'fr-sheet-t', 'fr-joint', 'fr-top-plate']) {
    assert.ok(staticIds.has(id), `${id} is missing from index.html`);
    assert.match(js, new RegExp(`\\$\\('${id}'\\)\\.(onchange|oninput) =`),
      `${id} has no handler`);
  }
});

test('the case pane offers a box of plates, and its controls are wired', () => {
  // M9: a parts scene is built as layers or as a jointed box
  assert.match(html, /<select id="cs-style">[\s\S]*value="layered"[\s\S]*value="box"/);
  for (const id of ['cs-style', 'cs-box-joint']) {
    assert.ok(staticIds.has(id), `${id} is missing from index.html`);
    assert.match(js, new RegExp(`\\$\\('${id}'\\)\\.onchange =`), `${id} has no handler`);
  }
  assert.match(js, /caseModel\.kind === 'box'\) \{ buildBox\(caseModel\)/);
});

test('rack bays can be added to a box, and the box draws its rails', () => {
  // M10: bays live in the case pane; the 3D view draws plates and members
  assert.ok(staticIds.has('bay-list') && staticIds.has('btn-add-bay'));
  assert.match(js, /\$\('btn-add-bay'\)\.onclick =/);
  assert.match(js, /module_depth: 40, module_panel_t: 2, modules: \[\]/);
  assert.match(js, /\[\.\.\.model\.plates, \.\.\.\(model\.members \|\| \[\]\)\]/);
});

test('real models are vendored, mapped, toggled and placed by the engine', () => {
  // M4: the glTF loader from the same three.js release, lazily fetched models
  assert.match(html, /"three\/addons\/loaders\/GLTFLoader\.js": "\.\/vendor\/loaders\/GLTFLoader\.js"/);
  assert.ok(fs.existsSync(path.join(here, '..', 'vendor', 'loaders', 'GLTFLoader.js')));
  assert.ok(fs.existsSync(path.join(here, '..', 'vendor', 'utils', 'BufferGeometryUtils.js')));
  assert.ok(staticIds.has('chk-models'));
  assert.match(js, /\$\('chk-models'\)\.onchange =/);
  // the pose comes from the server's frame matrix, never worked out here
  assert.match(js, /new THREE\.Matrix4\(\)\.set\(\.\.\.frame\.matrix\)/);
  assert.match(js, /\/api\/parts\/\$\{encodeURIComponent\(partId\)\}\/model\.glb/);
});

test('a material row edits its kerf and joint clearance, and a coupon can be cut', () => {
  // the two numbers a fit depends on are the user's to set, and measured on
  // a coupon cut from the same scene before the real job
  const fn = /function renderMaterials\(\) \{([\s\S]*?)\n\}/.exec(js);
  assert.ok(fn, 'no renderMaterials');
  assert.match(fn[1], /fitField\(kerf, 'kerf'\)/);
  assert.match(fn[1], /fitField\(clear, 'joint_clearance'\)/);
  assert.match(html, /<button id="btn-coupon"[^>]*>export test coupon<\/button>/);
  assert.match(js, /exportSheets\('svg', 'coupon'\)/);
  assert.match(js, /\/api\/export\/\$\{route\}\/\$\{fmt\}/);
});

test('the frame keeps no copy of the default laser sheet of its own', () => {
  // the server cuts it; the editor reads it off the build
  assert.doesNotMatch(js, /LASER_SHEET\s*=/);
  assert.match(js, /laser_sheet/);
});

test('a frame shows its issues, which come back with its build', () => {
  assert.match(html, /data-pane="pane-issues" data-uses="[^"]* ?frame"/);
  assert.match(js, /renderIssues\(state\.caseModel\.issues \|\| \[\]\)/);
});
