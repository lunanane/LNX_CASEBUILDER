// Scene storage, browser side.
//
// The editor has two homes for a project and picks one at boot from
// /api/config. Run locally, hwcase is a single-user tool and the files in
// backend/scenes *are* the project: text you can diff, commit next to the
// firmware, and edit in something else. Nothing in here improves on that, so
// locally nothing in here is used.
//
// A hosted instance has no such directory. One shared drawer that every
// visitor can list, rename and overwrite is not storage, it is a collision
// waiting for two people to both call a scene `case`. So the project moves
// into the browser, and out of it again as a file the user keeps.
//
// The canonical form on this side is the YAML *text*, not the parsed model.
// That is deliberate. Comments carry the reasoning in a scene file -- why the
// Pi is rotated, what the panel is derived from, which face has to sit flush
// -- and they survive a round trip only if the text that was opened is handed
// back to the server to merge the new values into. Keeping the model instead
// would throw all of it away on the first save, which is the exact bug
// hwcase.scenefile exists to prevent.

const KEY = 'hwcase:scene:';
const INDEX = 'hwcase:scenes';
const LAST = 'hwcase:last';

/** The server's SCENE_NAME, so a name that works here works there too. */
export const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$/;

// localStorage is not always there to be had: a private window, a browser set
// to block site data, an iframe with storage partitioned off. None of that
// should cost you the editor, so it degrades to a Map -- you keep the session,
// you lose it on reload, and `store.durable` says so out loud rather than
// letting you find out by closing the tab.
let mem = null;
function backing() {
  if (mem) return mem;
  try {
    const probe = '__hwcase__';
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    mem = new Map();
    mem.getItem = (k) => (mem.has(k) ? mem.get(k) : null);
    mem.setItem = (k, v) => void mem.set(k, String(v));
    mem.removeItem = (k) => void mem.delete(k);
    return mem;
  }
}

const get = (k) => backing().getItem(k);
const put = (k, v) => backing().setItem(k, v);
const drop = (k) => backing().removeItem(k);

function index() {
  try {
    const raw = JSON.parse(get(INDEX) || '[]');
    return Array.isArray(raw) ? raw.filter((n) => typeof n === 'string') : [];
  } catch { return []; }
}

function setIndex(names) {
  put(INDEX, JSON.stringify([...new Set(names)].sort()));
}

export const store = {
  /** False when everything here dies with the tab. */
  get durable() { return backing() !== mem; },

  list() { return index().filter((n) => get(KEY + n) !== null); },
  has(name) { return get(KEY + name) !== null; },
  read(name) { return get(KEY + name); },

  /** Returns nothing; throws with a readable message when the quota is hit. */
  write(name, yaml) {
    try {
      put(KEY + name, yaml);
    } catch (err) {
      throw new Error(
        `no room left to save ${name} in this browser -- export the scenes you `
        + `want to keep as files, then delete some here (${err.name})`);
    }
    setIndex([...index(), name]);
    put(LAST, name);
  },

  remove(name) {
    drop(KEY + name);
    setIndex(index().filter((n) => n !== name));
    if (get(LAST) === name) drop(LAST);
  },

  rename(from, to) {
    const text = get(KEY + from);
    if (text === null) throw new Error(`no scene ${from}`);
    if (store.has(to)) throw new Error(`${to} already exists`);
    store.write(to, text);
    store.remove(from);
  },

  /** The scene to reopen on the next visit, so a reload lands where you left. */
  last() {
    const name = get(LAST);
    return name && store.has(name) ? name : (store.list()[0] ?? null);
  },
  setLast(name) { put(LAST, name); },
};

// ---------------------------------------------------------------------------
// the user's own files
// ---------------------------------------------------------------------------
//
// Two tiers, because the good one is not everywhere. Chrome and Edge have the
// File System Access API, which gives a real handle: pick your project's
// .yaml once and every later save writes back into that same file, in place,
// with no dialog and no `scene (3).yaml` in Downloads. Firefox and Safari have
// neither picker, so they get open-by-input and save-by-download, which is the
// same round trip with more clicks.
//
// Handles are held for the session only. Persisting them across a reload is
// possible -- IndexedDB will store one -- but the browser re-prompts for
// permission anyway, and the local copy above already means a reload loses
// nothing. The extra machinery buys one dialog, once.

const YAML_TYPE = {
  description: 'hwcase scene',
  accept: { 'application/x-yaml': ['.yaml', '.yml'] },
};

/** `foo/bar.yaml` -> `bar`, cleaned into something the server will accept. */
export function nameFromFile(filename) {
  const stem = String(filename).split(/[\\/]/).pop().replace(/\.(ya?ml)$/i, '');
  const clean = stem.replace(/[^A-Za-z0-9 ._-]+/g, '-').replace(/^[^A-Za-z0-9]+/, '');
  return clean.slice(0, 64) || 'untitled';
}

function viaInput() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.yaml,.yml,application/x-yaml,text/yaml';
    input.onchange = async () => {
      const file = input.files?.[0];
      resolve(file ? { name: file.name, text: await file.text(), handle: null } : null);
    };
    // Cancelling fires nothing at all in older browsers, so the promise is
    // simply never settled. That is survivable -- it is awaited by a click
    // handler with nothing else to do -- but where `cancel` exists, settle it.
    input.oncancel = () => resolve(null);
    input.click();
  });
}

export const files = {
  get supported() { return typeof window.showSaveFilePicker === 'function'; },

  /** Ask for a file and read it. Null if the user backed out. */
  async open() {
    if (typeof window.showOpenFilePicker === 'function') {
      let handle;
      try {
        [handle] = await window.showOpenFilePicker({ types: [YAML_TYPE], multiple: false });
      } catch { return null; }                 // the user cancelled
      const file = await handle.getFile();
      return { name: file.name, text: await file.text(), handle };
    }
    return viaInput();
  },

  /** Write `text` somewhere the user picks. Returns a handle, or null. */
  async saveAs(name, text) {
    if (files.supported) {
      let handle;
      try {
        handle = await window.showSaveFilePicker({
          suggestedName: `${name}.yaml`, types: [YAML_TYPE],
        });
      } catch { return null; }
      await files.save(handle, text);
      return handle;
    }
    files.download(`${name}.yaml`, text);
    return null;
  },

  /** Write back into an already-picked file. */
  async save(handle, text) {
    const w = await handle.createWritable();
    await w.write(text);
    await w.close();
    return true;
  },

  download(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'application/x-yaml' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    // Revoked a beat later: revoking synchronously races the download in
    // Safari and lands you an empty file.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  },
};
