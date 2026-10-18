// The catalogue import's review step (M3), as plain functions.
//
// An import used to go straight from a click to a saved part: whichever
// model file sorted first, whichever way up a heuristic guessed, no
// connectors. Now the server measures a preview first and the editor shows
// it -- the file it measured and the others it could have, whether it turned
// the board over, the connectors it proposed from body sizes, every warning
// -- and the import takes the same file and flip, so what is saved is what
// was reviewed.
//
// Nothing here touches the DOM, so the tests run under plain node.

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

/** The query string the preview and the import share. Unset choices are
 *  left to the server's own defaults. */
export function importQuery(choice = {}) {
  const q = new URLSearchParams();
  if (choice.file) q.set('file', choice.file);
  if (choice.flip === true || choice.flip === false) q.set('flip', String(choice.flip));
  const s = q.toString();
  return s ? `?${s}` : '';
}

/** What the reviewer is choosing, as last shown: the measured file and
 *  the way up the preview settled on. */
export function choiceOf(preview) {
  return { file: preview.file, flip: !!preview.flipped };
}

/** The review dialog's body. Ids: imp-file (the model), imp-flip (turned
 *  over). Proposed connectors are listed as what they are: estimates. */
export function reviewHtml(preview) {
  const files = preview.files?.length ? preview.files : [preview.file];
  const options = files.map((f) => `<option value="${esc(f)}"`
    + (f === preview.file ? ' selected' : '') + `>${esc(f)}</option>`).join('');
  const conns = preview.connectors || [];
  const connList = conns.length
    ? '<ul>' + conns.map((c) => `<li><b>${esc(c.name)}</b> (${esc(c.type)}, `
        + `facing ${esc(c.face)}) -- proposed, check it</li>`).join('') + '</ul>'
    : '<p class="note">No connectors recognised. A board with none asks the '
      + 'case for no cable room: add them before you cut.</p>';
  const warns = (preview.warnings || []).map((w) => `<li>${esc(w)}</li>`).join('');
  const outline = preview.part?.outline || {};
  const size = outline.type === 'rect'
    ? `${outline.size[0]} x ${outline.size[1]} mm`
    : `a ${(outline.points || []).length}-point outline`;
  return '<div class="field"><label title="the vendor folder can hold several models; '
    + 'the importer prefers a STEP">model</label>'
    + `<select id="imp-file">${options}</select></div>`
    + '<label class="check" title="which face is up is a guess; turn it over if the '
    + 'parts you expect to face the panel are underneath">'
    + `<input type="checkbox" id="imp-flip"${preview.flipped ? ' checked' : ''}> `
    + 'turned over</label>'
    + `<p>${esc(preview.part?.name || '')}: ${esc(size)}, `
    + `${(preview.part?.holes || []).length} mounting holes.</p>`
    + '<h3>connectors</h3>' + connList
    + (warns ? `<h3>check</h3><ul>${warns}</ul>` : '');
}
