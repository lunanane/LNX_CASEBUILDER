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

/** Ids app.js builds at runtime, via innerHTML in a render function. */
const builtIds = new Set(
  [...js.matchAll(/id=["'`]([a-z][\w-]*)["'`]/g)].map((m) => m[1]));

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
  // has to say so.
  for (const id of ['btn-svg', 'btn-dxf']) {
    const body = handlerBody(id);
    assert.ok(body, `${id} has no async click handler`);
    assert.match(body, /catch\s*\(/, `${id} swallows failures`);
    assert.match(body, /status\(/, `${id} never reports anything`);
  }
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
