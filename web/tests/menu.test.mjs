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

/** Every pointer event a mouse produces while wandering over an element. */
function wander(el) {
  for (const type of ['pointerenter', 'pointerover', 'pointermove',
                      'pointerleave', 'pointerout']) {
    el.dispatch(type);
  }
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

test('the pointer moving never changes anything', () => {
  // The rule, stated plainly: an open menu closes when a button in it is
  // clicked, or when something outside it is clicked. Nothing else. Hover
  // switching is a convenience that cost three rounds of this bug; being able
  // to click an item is not a convenience.
  const { menus, win } = bar();
  press(win, menus.file.btn);

  for (const m of [menus.file, menus.view]) {
    for (const el of [m.btn, m.menu, m.pop]) wander(el);
  }
  assert.ok(menus.file.menu.classList.contains('open'),
    `the pointer closed it: ${JSON.stringify(win.__menuLog)}`);
  assert.ok(!menus.view.menu.classList.contains('open'),
    'hover opened a menu nobody clicked');
});


test('a click outside closes everything', () => {
  const { root, menus, win } = bar();
  press(win, menus.file.btn);

  const outside = new (menus.file.btn.constructor)('div');
  root.append(outside);
  win.fire(outside, 'click');
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
  wander(menus.file.pop);
  assert.ok(menus.file.menu.classList.contains('open'),
    `menu closed on its own: ${JSON.stringify(win.__menuLog)}`);
});
