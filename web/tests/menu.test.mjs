// Does a click on a menu item actually reach the item?
//
// That is the whole question, and it was answered wrong twice by reading the
// code. So it gets executed instead.

import assert from 'node:assert/strict';
import test from 'node:test';

import { wireMenus } from '../menu.js';
import { buildMenuBar, makeWindow } from './dom-stub.mjs';

function bar() {
  const { root, menus } = buildMenuBar({
    file: ['btn-save', 'btn-svg', 'btn-dxf'],
    view: ['btn-frame'],
  });
  const win = makeWindow(root);
  wireMenus(root, win);
  return { root, menus, win };
}

/** What a real click is: a press, then a click, both reaching the window. */
function press(win, el) {
  win.fire(el, 'pointerdown');
  return win.fire(el, 'click');
}

test('a click on a menu item reaches the item', () => {
  const { menus, win } = bar();
  let fired = 0;
  menus.file.items['btn-svg'].addEventListener('click', () => { fired += 1; });

  press(win, menus.file.btn);
  assert.ok(menus.file.menu.classList.contains('open'), 'the menu did not open');

  press(win, menus.file.items['btn-svg']);
  assert.equal(fired, 1, 'the export handler never ran');
});

test('the press that opens an item does not close the menu first', () => {
  // This was the bug. A pointerdown listener on the window closed every menu,
  // so the popup was display:none before the click could be delivered and the
  // item silently did nothing.
  const { menus, win } = bar();
  press(win, menus.file.btn);

  win.fire(menus.file.items['btn-svg'], 'pointerdown');
  assert.ok(menus.file.menu.classList.contains('open'),
    'the menu closed on pointerdown, so the click will never land');
});

test('the menu closes once the item has been clicked', () => {
  const { menus, win } = bar();
  press(win, menus.file.btn);
  press(win, menus.file.items['btn-svg']);
  assert.ok(!menus.file.menu.classList.contains('open'),
    'a menu that stays open after acting is worse than no menu');
});

test('reaching into an open menu does not shut it', () => {
  // The popup hangs a few pixels below its button, so travelling down into it
  // leaves .menu and re-enters -- firing pointerenter a second time. Toggling
  // there closed the menu you were reaching into: "it closes if I move too
  // slowly".
  const { menus, win } = bar();
  press(win, menus.file.btn);

  menus.file.btn.dispatch('pointerenter');
  assert.ok(menus.file.menu.classList.contains('open'),
    're-entering its own menu closed it');

  // and again, because the pointer can cross that boundary repeatedly
  menus.file.btn.dispatch('pointerenter');
  assert.ok(menus.file.menu.classList.contains('open'));
});

test('sliding onto another menu switches to it', () => {
  const { menus, win } = bar();
  press(win, menus.file.btn);

  menus.view.btn.dispatch('pointerenter');
  assert.ok(menus.view.menu.classList.contains('open'), 'view did not open');
  assert.ok(!menus.file.menu.classList.contains('open'), 'file stayed open too');
});

test('hovering a menu with none open does nothing', () => {
  const { menus } = bar();
  menus.view.btn.dispatch('pointerenter');
  assert.ok(!menus.view.menu.classList.contains('open'),
    'menus must not open on hover alone');
});

test('a press outside closes everything', () => {
  const { root, menus, win } = bar();
  press(win, menus.file.btn);

  const outside = new (menus.file.btn.constructor)('div');
  root.append(outside);
  win.fire(outside, 'pointerdown');
  assert.ok(!menus.file.menu.classList.contains('open'));
});

test('escape closes everything', () => {
  const { menus, win } = bar();
  press(win, menus.file.btn);
  win.fireBare('keydown', { key: 'Escape' });
  assert.ok(!menus.file.menu.classList.contains('open'));
});

test('clicking the button again closes its own menu', () => {
  const { menus, win } = bar();
  press(win, menus.file.btn);
  press(win, menus.file.btn);
  assert.ok(!menus.file.menu.classList.contains('open'));
});

test('aria-expanded tracks the menu', () => {
  const { menus, win } = bar();
  assert.equal(menus.file.btn.getAttribute('aria-expanded'), null);
  press(win, menus.file.btn);
  assert.equal(menus.file.btn.getAttribute('aria-expanded'), 'true');
  press(win, menus.file.btn);
  assert.equal(menus.file.btn.getAttribute('aria-expanded'), 'false');
});

test('hover can never close a menu, only open one', () => {
  // The invariant that makes "it vanished while I was reaching for it"
  // impossible by construction. Hover is bound to the button rather than to
  // .menu, because .menu contains the popup -- which is wider than the button
  // and overlaps its neighbours -- so pointer traffic around the popup would
  // otherwise be able to drive the menu's state.
  const { menus, win } = bar();
  press(win, menus.file.btn);

  for (const m of [menus.file, menus.view]) {
    for (const el of [m.btn, m.menu, m.pop]) {
      el.dispatch('pointerenter');
      el.dispatch('pointerleave');
      el.dispatch('pointerout');
    }
  }
  const open = [menus.file, menus.view].filter(
    (m) => m.menu.classList.contains('open'));
  assert.equal(open.length, 1, 'hover left no menu open, or left two open');
});

test('every close records why it happened', () => {
  // A menu that vanishes on its own cannot be diagnosed by watching it.
  const { menus, win } = bar();
  press(win, menus.file.btn);
  win.fireBare('keydown', { key: 'Escape' });

  const why = win.__menuLog.map((e) => e.why);
  assert.ok(why.some((w) => w.includes('opened')), why);
  assert.ok(why.some((w) => w.includes('escape')), why);
});

test('a menu left alone stays open', () => {
  // Nothing on a timer, nothing on a render pass, nothing at all should shut
  // a menu that the user has not touched.
  const { menus, win } = bar();
  press(win, menus.file.btn);
  win.fireBare('keydown', { key: 'a' });
  win.fire(menus.file.pop, 'pointermove');
  assert.ok(menus.file.menu.classList.contains('open'),
    `menu closed on its own: ${JSON.stringify(win.__menuLog)}`);
});
