// The header menu bar.
//
// Extracted from app.js so the click path can be executed in a test rather
// than argued about. It shipped broken twice, both times because reasoning
// about event order looked easier than running it.
//
// Two rules keep it out of trouble:
//
//   * Hover only ever OPENS, and only from a menu *button*. It is bound to the
//     button and not to `.menu`, because `.menu` contains the popup -- which is
//     wider than the button and overlaps its neighbours -- so binding there
//     turns every trip in and out of the popup into a chance to change state.
//     Reaching into an open menu cannot close it if hover cannot close
//     anything at all.
//   * Nothing closes on `pointerdown` inside a menu. Closing there hides the
//     popup before the click is delivered, and the item silently does nothing.
//
// Every close records why, on `window.__menuLog`. A menu that vanishes on its
// own is near-impossible to diagnose by watching it, and the reason is one
// line of bookkeeping.
//
// Takes a `root` and a `win` so a test can hand it a small document.

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
    menus.forEach((m) => setOpen(m, false));
  };

  for (const m of menus) {
    const btn = m.querySelector('.menu-btn');
    if (!btn) continue;

    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const open = !m.classList.contains('open');
      closeAll(open ? 'opening another menu' : 'its own button again');
      setOpen(m, open);
      if (open) note('opened by click');
    });

    // Sliding along the bar with a menu already open moves to the next one.
    // On the BUTTON, and open-only: hover must never be able to close
    // anything, or travelling towards an item becomes a hazard.
    btn.addEventListener('pointerenter', () => {
      if (!anyOpen() || m.classList.contains('open')) return;
      closeAll('slid onto another menu');
      setOpen(m, true);
      note('opened by hover');
    });
  }

  // A press elsewhere closes up -- but never a press inside a menu, or the
  // popup is gone before the click reaches the item.
  win.addEventListener('pointerdown', (ev) => {
    if (!ev.target?.closest?.('.menu')) closeAll('pressed outside');
  });

  // Once an item is clicked the menu has done its job. This runs after the
  // item's own handler, because it is on an ancestor and click bubbles --
  // which is the only reason closing here is safe.
  for (const pop of root.querySelectorAll('.menu-pop')) {
    pop.addEventListener('click', (ev) => {
      // Checkboxes stay open: you usually want to flip two of them.
      if (ev.target?.closest?.('button')) closeAll('an item was chosen');
    });
  }

  win.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closeAll('escape');
  });

  return { closeAll, log, isOpen: (m) => m.classList.contains('open') };
}
