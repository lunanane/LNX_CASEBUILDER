// The header menu bar.
//
// An open menu closes for exactly three reasons: a button in it was clicked,
// something outside it was clicked, or Escape. Nothing else. In particular
// **nothing here reacts to the pointer moving**, which is what took three
// attempts to get right.
//
// The history is worth keeping, because each fix looked obviously correct:
//
//   1. `pointerdown` on the window closed every menu, including the one being
//      pressed. The popup went `display:none` before the click could be
//      delivered, so items silently did nothing.
//   2. Hover was bound to `.menu` and *toggled*, so travelling from the button
//      down into the popup -- which leaves `.menu` and re-enters it, because
//      the popup is positioned outside the button's box -- shut the menu you
//      were reaching into.
//   3. Hover was then bound to the button and made open-only, which is still
//      not enough: `.menu-pop` is far wider than its button and overlaps its
//      neighbours, so a pointer travelling towards an item can cross another
//      menu's button and switch away from the one you opened.
//
// The switch-on-hover behaviour a desktop menu bar usually has is simply not
// worth this. It is a convenience; being able to click an item is not.
//
// Closing is on `click`, not `pointerdown`: a click is what "clicked outside"
// means, and it happens after any in-menu click has already been delivered.
//
// Every close records why, on `window.__menuLog` and as a console line. A
// menu that shuts on its own cannot be diagnosed by watching it happen.
//
// `root` and `win` are injectable so a test can hand it a small document.

export function wireMenus(root = document, win = window) {
  const menus = [...root.querySelectorAll('.menu')];
  const log = [];
  win.__menuLog = log;

  const note = (why) => {
    log.push({ why, at: Date.now() });
    if (log.length > 50) log.shift();
    win.console?.debug?.('[menu]', why);
  };

  const setOpen = (m, open) => {
    m.classList.toggle('open', open);
    m.querySelector('.menu-btn')?.setAttribute('aria-expanded', String(open));
  };
  const anyOpen = () => menus.some((m) => m.classList.contains('open'));
  const closeAll = (why) => {
    if (anyOpen()) note(`closed: ${why}`);
    closingOnPurpose = true;
    menus.forEach((m) => setOpen(m, false));
    // Cleared after the microtask queue drains, because a MutationObserver
    // callback runs then rather than synchronously.
    Promise.resolve().then(() => { closingOnPurpose = false; });
  };

  for (const m of menus) {
    const btn = m.querySelector('.menu-btn');
    if (!btn) continue;

    btn.addEventListener('click', (ev) => {
      // Stops the document-level close below from seeing this click and
      // shutting the menu we are in the middle of opening.
      ev.stopPropagation();
      const open = !m.classList.contains('open');
      closingOnPurpose = true;
      menus.forEach((other) => setOpen(other, false));
      setOpen(m, open);
      Promise.resolve().then(() => { closingOnPurpose = false; });
      note(open ? 'opened by click' : 'closed: its own button again');
    });
  }

  for (const pop of root.querySelectorAll('.menu-pop')) {
    pop.addEventListener('click', (ev) => {
      // The item's own handler has already run -- this is an ancestor and
      // click bubbles, which is the only reason closing here is safe.
      // Checkboxes are left alone: you usually want to flip two of them.
      if (ev.target?.closest?.('button')) {
        ev.stopPropagation();
        closeAll('an item was chosen');
      }
    });
  }

  win.addEventListener('click', (ev) => {
    if (!ev.target?.closest?.('.menu')) closeAll('clicked outside');
  });

  win.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closeAll('escape');
  });

  // Catch a menu being closed by something that is not this module.
  //
  // Three fixes in and it still shuts on its own, so the possibility that the
  // culprit is elsewhere has to be tested rather than assumed. Every close
  // routed through here sets `expected` first; if the class disappears without
  // that, some other code did it, and the stack says which.
  watchForOutsideInterference(menus, note, win);

  return { closeAll, log, isOpen: (m) => m.classList.contains('open') };
}

/** Report anyone removing `.open` who is not us. */
function watchForOutsideInterference(menus, note, win) {
  const Observer = win.MutationObserver ?? globalThis.MutationObserver;
  if (!Observer) return;

  for (const m of menus) {
    let was = m.classList.contains('open');
    new Observer(() => {
      const now = m.classList.contains('open');
      if (was && !now && !closingOnPurpose) {
        // A stack from inside the observer names the code that mutated the
        // class, which is the whole point.
        const where = (new Error().stack || '').split(/\r?\n/)
          .slice(1, 4).join(' | ');
        note(`closed by something outside menu.js -- ${where}`);
      }
      was = now;
    }).observe(m, { attributes: true, attributeFilter: ['class'] });
  }
}

//: set while this module is deliberately closing a menu, so the observer above
//: can tell our own work from somebody else's
let closingOnPurpose = false;
