// A DOM small enough to hand-write and real enough to test event order on.
//
// The editor ships with no dependencies on purpose -- three.js is vendored and
// there is no build step -- so pulling in jsdom to test sixty lines of menu
// logic would be a poor trade. What the menu bug actually turned on is the
// order of pointerdown, click, and bubbling to an ancestor, and that is
// faithfully reproducible in far less code than the bug cost.
//
// Deliberately NOT a general DOM: no layout, no CSS, no default actions. If a
// test needs any of that, it needs a browser, and pretending otherwise here
// would be worse than having nothing.

class ClassList {
  constructor(el) { this.el = el; this.set = new Set(); }
  add(...c) { c.forEach((x) => this.set.add(x)); }
  remove(...c) { c.forEach((x) => this.set.delete(x)); }
  contains(c) { return this.set.has(c); }
  toggle(c, force) {
    const on = force === undefined ? !this.contains(c) : !!force;
    if (on) this.add(c); else this.remove(c);
    return on;
  }
  get value() { return [...this.set].join(' '); }
}

export class El {
  constructor(tag, classes = []) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.classList = new ClassList(this);
    this.attributes = {};
    this.listeners = new Map();
    classes.forEach((c) => this.classList.add(c));
  }

  append(...kids) {
    for (const k of kids) { k.parentNode = this; this.children.push(k); }
    return this;
  }

  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] ?? null; }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }

  /** Nearest self-or-ancestor matching a `.class` or a bare tag name. */
  closest(sel) {
    for (let n = this; n; n = n.parentNode) if (n.matches(sel)) return n;
    return null;
  }

  matches(sel) {
    return sel.startsWith('.')
      ? this.classList.contains(sel.slice(1))
      : this.tagName === sel.toUpperCase();
  }

  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }

  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      for (const k of n.children) { if (k.matches(sel)) out.push(k); walk(k); }
    };
    walk(this);
    return out;
  }

  /** Dispatch with real capture-free bubbling: target first, then ancestors. */
  dispatch(type, extra = {}) {
    const ev = {
      type,
      target: this,
      _stopped: false,
      stopPropagation() { this._stopped = true; },
      preventDefault() { this.defaultPrevented = true; },
      ...extra,
    };
    for (let n = this; n; n = n.parentNode) {
      for (const fn of n.listeners.get(type) ?? []) {
        fn.call(n, ev);
        if (ev._stopped) return ev;
      }
    }
    return ev;
  }
}

/** A window that sees events after they finish bubbling up the tree. */
export function makeWindow(root) {
  const win = {
    listeners: new Map(),
    addEventListener(type, fn) {
      if (!win.listeners.has(type)) win.listeners.set(type, []);
      win.listeners.get(type).push(fn);
    },
    /** Fire an event at `el` and then, if it was not stopped, at the window --
     *  which is where a real event ends up when nothing stops it. */
    fire(el, type, extra) {
      const ev = el.dispatch(type, extra);
      if (!ev._stopped) {
        for (const fn of win.listeners.get(type) ?? []) fn(ev);
      }
      return ev;
    },
    /** An event with no element behind it, e.g. a keypress. */
    fireBare(type, extra = {}) {
      const ev = { type, target: null, _stopped: false,
                   stopPropagation() {}, preventDefault() {}, ...extra };
      for (const fn of win.listeners.get(type) ?? []) fn(ev);
      return ev;
    },
  };
  win.root = root;
  return win;
}

/** The header's shape: several menus, each a button and a popup of items. */
export function buildMenuBar(spec) {
  const root = new El('div');
  const made = {};
  for (const [name, items] of Object.entries(spec)) {
    const menu = new El('div', ['menu']);
    const btn = new El('button', ['menu-btn']);
    const pop = new El('div', ['menu-pop']);
    const entries = {};
    for (const item of items) {
      const b = new El('button');
      b.setAttribute('id', item);
      pop.append(b);
      entries[item] = b;
    }
    menu.append(btn, pop);
    root.append(menu);
    made[name] = { menu, btn, pop, items: entries };
  }
  return { root, menus: made };
}
