// The scene store a hosted editor keeps its project in.
//
// Worth executing rather than reading, because the two things that go wrong
// here are invisible in the source: a browser that refuses to store anything
// (a private window, site data blocked) and a browser that is full. Both fail
// at the call, not at the declaration, and both are the difference between
// "your afternoon is in this tab" and "your afternoon is gone".

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

/** A localStorage faithful enough for the ways this one gets used: string
 *  values, null for absent, and a quota that can be made to bite. */
function fakeStorage({ quota = Infinity } = {}) {
  const map = new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem(k, v) {
      if ([...map.values()].join('').length + String(v).length > quota) {
        const err = new Error('quota');
        err.name = 'QuotaExceededError';
        throw err;
      }
      map.set(k, String(v));
    },
    removeItem: (k) => map.delete(k),
  };
}

// store.js reads window.localStorage once and caches the decision, so each
// test gets a fresh copy of the module rather than a fresh copy of the window.
async function freshStore(storage) {
  globalThis.window = storage === null
    ? { get localStorage() { throw new Error('site data is blocked'); } }
    : { localStorage: storage };
  return import(`../store.js?case=${Math.random()}`);
}

beforeEach(() => { delete globalThis.window; });

test('a scene written is a scene listed and read back', async () => {
  const { store } = await freshStore(fakeStorage());
  store.write('lamp', 'name: lamp\n# why the base is 4mm\n');
  assert.deepEqual(store.list(), ['lamp']);
  assert.match(store.read('lamp'), /why the base is 4mm/);
  assert.equal(store.read('missing'), null);
});

test('the comments are what is stored, not the model', async () => {
  // The whole reason the text is canonical on this side: a scene file's
  // reasoning lives in its comments, and storing a parsed model loses it.
  const { store } = await freshStore(fakeStorage());
  const text = '# the Pi is turned so the SD card clears the wall\nname: box\n';
  store.write('box', text);
  assert.equal(store.read('box'), text);
});

test('renaming keeps the file and frees the old name', async () => {
  const { store } = await freshStore(fakeStorage());
  store.write('a', 'name: a\n');
  store.rename('a', 'b');
  assert.deepEqual(store.list(), ['b']);
  assert.equal(store.read('a'), null);
  store.write('a', 'name: a again\n');
  assert.throws(() => store.rename('a', 'b'), /already exists/);
});

test('deleting the last scene leaves an empty list, not a ghost', async () => {
  const { store } = await freshStore(fakeStorage());
  store.write('only', 'name: only\n');
  store.remove('only');
  assert.deepEqual(store.list(), []);
  assert.equal(store.last(), null);
});

test('a full browser says so instead of losing the scene quietly', async () => {
  const { store } = await freshStore(fakeStorage({ quota: 40 }));
  assert.throws(() => store.write('big', 'x'.repeat(200)),
    /no room left|QuotaExceeded/);
});

test('a browser that stores nothing still runs, and admits it', async () => {
  // A private window is not a reason to refuse to open the editor. It is a
  // reason to say the work dies with the tab, which `durable` is for.
  const { store } = await freshStore(null);
  assert.equal(store.durable, false);
  store.write('session', 'name: session\n');
  assert.deepEqual(store.list(), ['session']);
});

test('a real browser reports itself durable', async () => {
  const { store } = await freshStore(fakeStorage());
  assert.equal(store.durable, true);
});

test('reopening lands on the scene you left', async () => {
  const { store } = await freshStore(fakeStorage());
  store.write('one', 'name: one\n');
  store.write('two', 'name: two\n');
  assert.equal(store.last(), 'two');
  store.remove('two');
  assert.equal(store.last(), 'one');       // not a dangling name
});

test('a filename becomes a name the server will accept', async () => {
  const { nameFromFile, NAME_RE } = await freshStore(fakeStorage());
  for (const [given, want] of [
    ['soundmachine-lnx1.yaml', 'soundmachine-lnx1'],
    ['/home/luna/case v2.YML', 'case v2'],
    ['C:\\projects\\my case.yaml', 'my case'],
    ['.hidden.yaml', 'hidden'],
    ['%%%.yaml', 'untitled'],
  ]) {
    const got = nameFromFile(given);
    assert.equal(got, want, `${given} -> ${got}`);
    assert.ok(NAME_RE.test(got), `${got} would be rejected by the server`);
  }
});

test('the name rule matches the server\'s', async () => {
  // backend/hwcase/api.py: SCENE_NAME
  const { NAME_RE } = await freshStore(fakeStorage());
  assert.ok(NAME_RE.test('sound machine v2.1'));
  assert.ok(!NAME_RE.test('../etc/passwd'));
  assert.ok(!NAME_RE.test(' leading space'));
  assert.ok(!NAME_RE.test(''));
  assert.ok(!NAME_RE.test('x'.repeat(65)));
});
