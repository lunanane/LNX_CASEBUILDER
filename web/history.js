// Undo/redo over whole-scene snapshots.
//
// The scene is a few kilobytes of plain JSON, so snapshotting all of it is far
// simpler than tracking individual edits -- and it cannot drift out of sync
// with the document the way a command log can. The only care needed is
// coalescing: a drag mutates the scene every frame, and each frame must not
// become its own undo step.

const LIMIT = 100;

export function createHistory(limit = LIMIT) {
  const past = [];
  const future = [];
  let coalesceKey = null;

  const snap = (scene) => JSON.stringify(scene);

  return {
    /** Record the state *before* a change. `key` coalesces a run of edits:
     *  repeated calls with the same key only keep the first. */
    push(scene, key = null) {
      if (key !== null && key === coalesceKey) return false;
      coalesceKey = key;
      past.push(snap(scene));
      if (past.length > limit) past.shift();
      future.length = 0;
      return true;
    },

    /** End a coalesced run, so the next edit starts a fresh step. */
    seal() {
      coalesceKey = null;
    },

    undo(current) {
      if (!past.length) return null;
      future.push(snap(current));
      coalesceKey = null;
      return JSON.parse(past.pop());
    },

    redo(current) {
      if (!future.length) return null;
      past.push(snap(current));
      coalesceKey = null;
      return JSON.parse(future.pop());
    },

    clear() {
      past.length = 0;
      future.length = 0;
      coalesceKey = null;
    },

    get canUndo() { return past.length > 0; },
    get canRedo() { return future.length > 0; },
    get depth() { return past.length; },
  };
}
