'use strict';

const assignmentId = param('id');
let A = null; // assignment detail from the API
let pending = []; // [{file, ppt}] scans chosen but not uploaded yet
let uploading = false;

const MODE_LABELS = { identity: 'one-to-one', odd: 'odd scan pages', custom: 'custom' };

// What a blank page holds, for the mapping table and the answer key: the cover mark and every
// problem on it. A page may be both, which is the quiz with the name on top and problems below.
function pageRole(p, prefix = '') {
  return [p.cover ? 'Cover' : null, ...p.problems.map((pr) => `${prefix}${pr.label}`)].filter(Boolean).join(', ');
}

// ------------------------------------------------------------------ blank test

function renderTotals() {
  $('#total-points').textContent = A.blank_pages ? `Total ${fmt(A.total_points)} points` : '';
}

function renderPages() {
  const hasBatches = A.batches.length > 0;
  $('#blank-button').firstChild.textContent = A.blank_key ? 'Replace PDF' : 'Upload PDF';
  $('#blank-button').classList.toggle('disabled', hasBatches);
  $('#blank-file').disabled = hasBatches;
  $('#blank-status').textContent = A.blank_key
    ? `${plural(A.blank_pages, 'page', 'pages')}${hasBatches ? ' (delete scans to replace)' : ''}`
    : '';
  $('#pages-hint').textContent = A.blank_pages
    ? 'Tick “Cover” on the page the student writes their name on — it can hold problems as well, '
      + 'and a test needs no cover page at all. Any page takes as many problems as it has on it.'
    : '';
  renderTotals();
  $('#pages').replaceChildren(...A.pages.map(pageCard));
}

// A page is the cover, or carries problems, or both, or neither: nothing here rules anything out.
// The cover is only the page the name is read from and the exported score box goes on.
function pageCard(p) {
  const coverBox = h('input', {
    type: 'checkbox', checked: p.cover, onchange: (e) => setCover(p.index, e.target.checked),
  });
  return h('div', { class: `page-card${p.cover ? ' cover' : ''}${p.problems.length ? '' : ' empty'}` },
    h('img', { src: pageUrl(A.blank_key, p.index, true), alt: '', onclick: () => showImage(pageUrl(A.blank_key, p.index)) }),
    h('div', { class: 'fields' },
      h('div', { class: 'head' },
        h('span', { class: 'muted' }, `p. ${p.index + 1}`),
        h('span', { class: 'spacer' }),
        h('label', { class: 'check', title: 'The page the student writes their name on' }, coverBox, 'Cover')),
      p.problems.map((pr, i) => problemRow(p, pr, i)),
      h('button', { class: 'link add', onclick: () => addProblem(p.index) }, '+ Add problem')));
}

function problemRow(page, pr, i) {
  const label = h('input', { value: pr.label, autocomplete: 'off', title: 'Problem label' });
  const points = h('input', { type: 'number', min: '0', step: 'any', value: String(pr.max_points), title: 'Points' });
  const save = () => saveProblem(pr, label, points);
  label.addEventListener('change', save);
  points.addEventListener('change', save);
  const move = (delta, glyph, title) => h('button', {
    class: 'link', title, disabled: i + delta < 0 || i + delta >= page.problems.length,
    onclick: () => moveProblem(pr, i + delta),
  }, glyph);
  return h('div', { class: 'prob' }, label, points,
    h('span', { class: 'tools' },
      page.problems.length > 1 ? move(-1, '↑', 'Move up') : null,
      page.problems.length > 1 ? move(1, '↓', 'Move down') : null,
      h('button', { class: 'link', title: 'Remove this problem', onclick: () => removeProblem(pr) }, '×')));
}

async function setCover(index, on) {
  try {
    A = await PUT(`/api/assignments/${assignmentId}/cover`, { page: on ? index : null });
  } finally {
    render();
  }
}

async function addProblem(index) {
  try {
    A = await POST(`/api/assignments/${assignmentId}/problems`, { page: index });
  } finally {
    render();
  }
}

async function removeProblem(pr) {
  if (pr.comments > 0) {
    const ok = await confirmBox(
      `Problem ${pr.label} has ${plural(pr.comments, 'comment', 'comments')}. Remove the problem and its comments?`,
      'Remove');
    if (!ok) return;
  }
  try {
    A = await DELETE(`/api/problems/${pr.id}`);
  } finally {
    render();
  }
}

async function moveProblem(pr, position) {
  try {
    A = await PATCH(`/api/problems/${pr.id}`, { position });
  } finally {
    render();
  }
}

async function saveProblem(pr, labelInput, pointsInput) {
  const points = parseFloat(pointsInput.value);
  if (!labelInput.value.trim() || !(points >= 0)) {
    labelInput.value = pr.label;
    pointsInput.value = pr.max_points;
    return;
  }
  // Update data without rebuilding the cards, so focus stays where the user tabbed to.
  // Everything else that names the problem is redrawn, since none of it holds the caret.
  A = await PATCH(`/api/problems/${pr.id}`, { label: labelInput.value.trim(), max_points: points });
  renderTotals();
  renderAnswerKey();
  renderBatches();
}

$('#blank-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  $('#blank-status').textContent = 'Rendering pages';
  try {
    A = await POST(`/api/assignments/${assignmentId}/blank`, form);
  } finally {
    render();
  }
});

// ------------------------------------------------------------------ answer key

// Any page count is fine: the whole key scrolls beside the student's work while grading, and it
// opens on the page sitting where the problem sits in the test. A key whose pages are gone counts
// as no key, so nothing ever reports a page count it doesn't have.
function renderAnswerKey() {
  const hasKey = A.answer_key && A.answer_key_pages > 0;
  $('#key-button').firstChild.textContent = hasKey ? 'Replace PDF' : 'Upload PDF';
  $('#key-delete').hidden = !hasKey;
  $('#key-status').textContent = hasKey
    ? `${plural(A.answer_key_pages, 'page', 'pages')}, shown beside the student's work while grading`
    : "Optional. Shown beside the student's work while grading";
  $('#key-pages').replaceChildren(
    ...(hasKey ? Array.from({ length: A.answer_key_pages }, (_, i) => keyCard(i)) : []));
}

// What the test has on the same page, which is where the key opens while grading that problem.
function answers(index) {
  const p = A.pages[index];
  return p ? pageRole(p, 'Problem ') : '';
}

function keyCard(index) {
  const label = answers(index);
  return h('div', { class: 'page-card' },
    h('img', {
      src: pageUrl(A.answer_key, index, true), alt: '',
      onclick: () => showImage(pageUrl(A.answer_key, index)),
    }),
    h('div', { class: 'fields' },
      h('span', { class: 'muted' }, `p. ${index + 1}`),
      h('span', { class: label ? '' : 'muted' }, label || '—')));
}

$('#key-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  $('#key-status').textContent = 'Rendering pages';
  try {
    A = await POST(`/api/assignments/${assignmentId}/answer_key`, form);
  } finally {
    render();
  }
});

$('#key-delete').addEventListener('click', async () => {
  if (!await confirmBox('Remove the answer key?', 'Remove')) return;
  A = await DELETE(`/api/assignments/${assignmentId}/answer_key`);
  render();
});

// ------------------------------------------------------------------ scans

// Pages per test belongs to the scan as a whole, so only the very first PDF asks for it.
// Every later PDF carries on from the last page of the one before.
function startsTheScan(i) {
  return !A.batches.length && i === 0;
}

function renderPending() {
  if (!pending.length) return $('#pending').replaceChildren();
  const rows = pending.map((item, i) => h('tr', {},
    h('td', {}, item.file.name),
    h('td', {}, startsTheScan(i)
      ? h('input', {
        type: 'number', min: '1', value: String(item.ppt), disabled: uploading,
        oninput: (e) => { item.ppt = parseInt(e.target.value, 10); },
      })
      : h('span', { class: 'muted' }, 'continues the scan')),
    h('td', {}, h('button', {
      class: 'link', disabled: uploading, onclick: () => { pending.splice(i, 1); renderPending(); },
    }, 'Remove'))));
  const upload = h('button', { class: 'primary', disabled: uploading, onclick: uploadPending }, 'Upload');
  $('#pending').replaceChildren(
    h('table', { class: 'grid compact' },
      h('thead', {}, h('tr', {}, h('th', {}, 'File'), h('th', {}, 'Pages per test'), h('th', {}))),
      h('tbody', {}, rows)),
    h('div', { class: 'row', style: 'margin-top: 8px' }, upload));
}

async function uploadPending() {
  if (uploading) return;
  uploading = true;
  renderPending();
  const total = pending.length;
  let done = 0;
  try {
    while (pending.length) {
      const item = pending[0];
      $('#scan-status').textContent = `Uploading ${done + 1}/${total}`;
      const form = new FormData();
      form.append('file', item.file);
      if (startsTheScan(0) && item.ppt >= 1) form.append('pages_per_test', String(item.ppt));
      A = await POST(`/api/assignments/${assignmentId}/batches`, form);
      pending.shift();
      done++;
      render();
    }
  } finally {
    uploading = false;
    $('#scan-status').textContent = '';
    render();
  }
}

$('#scan-files').addEventListener('change', (e) => {
  for (const file of e.target.files) pending.push({ file, ppt: A.blank_pages });
  e.target.value = '';
  renderPending();
});

function renderBatches() {
  const ready = A.blank_pages > 0;
  $('#scan-button').firstChild.textContent = A.batches.length ? 'Add more pages' : 'Add PDFs';
  $('#scan-button').classList.toggle('disabled', !ready);
  $('#scan-files').disabled = !ready;
  if (!ready) $('#scan-status').textContent = 'Upload the blank test first';
  else if ($('#scan-status').textContent === 'Upload the blank test first') $('#scan-status').textContent = '';
  $('#batches').replaceChildren(...A.batches.map(batchBlock));
}

function batchBlock(b) {
  const ppt = h('input', { type: 'number', min: '1', value: String(b.pages_per_test) });
  ppt.addEventListener('change', () => changePagesPerTest(b, ppt));
  return h('div', { class: 'batch' },
    h('div', { class: 'row' },
      h('strong', {}, b.filename),
      h('span', { class: 'muted' }, plural(b.page_count, 'page', 'pages')),
      h('label', {}, 'Pages per test ', ppt),
      h('span', {}, plural(b.tests, 'test', 'tests')),
      b.leftover ? h('span', { class: 'warning' },
        `${plural(b.leftover, 'page', 'pages')} past the last full test: the next PDF you add carries on from there`) : null,
      h('span', { class: 'spacer' }),
      h('button', { onclick: () => deleteBatch(b) }, 'Delete')),
    h('div', { class: 'row', style: 'margin-top: 6px' },
      h('a', { class: 'btn', href: `organize.html?id=${assignmentId}&batch=${b.id}` }, 'Organize pages'),
      b.checked
        ? h('span', { class: 'muted' }, '✓ Page order checked')
        : h('span', { class: 'warning' }, 'Page order not checked yet')),
    h('details', { open: b.mode === 'custom' },
      h('summary', {}, `Page mapping: ${MODE_LABELS[b.mode]}`),
      mappingTable(b)));
}

function mappingTable(b) {
  const pages = A.pages.filter((p) => p.cover || p.problems.length);
  const options = (current) => Array.from({ length: b.pages_per_test },
    (_, i) => h('option', { value: String(i), selected: current === i }, i + 1));
  return h('div', { class: 'map' }, h('table', { class: 'grid compact' },
    h('tr', {}, h('th', {}, 'Blank page'), pages.map((p) => h('td', {}, p.index + 1))),
    h('tr', {}, h('th', {}, 'Problem'), pages.map((p) => h('td', {}, pageRole(p)))),
    h('tr', {}, h('th', {}, 'Scan page'),
      pages.map((p) => h('td', {}, h('select', {
        onchange: (e) => setMapping(b, p.index, parseInt(e.target.value, 10)),
      }, options(b.page_map[p.index])))))));
}

async function setMapping(b, blankIndex, scanOffset) {
  const page_map = [...b.page_map];
  page_map[blankIndex] = scanOffset;
  try {
    A = await PATCH(`/api/batches/${b.id}`, { page_map });
  } finally {
    render();
  }
}

async function changePagesPerTest(b, input) {
  const value = parseInt(input.value, 10);
  if (!(value >= 1)) {
    input.value = b.pages_per_test;
    return;
  }
  if (b.matched || b.annotations) {
    const ok = await confirmBox(
      `Re-split ${b.filename}? This discards ${plural(b.matched, 'name match', 'name matches')} and ` +
      `${plural(b.annotations, 'placed comment', 'placed comments')} in this file.`, 'Re-split');
    if (!ok) {
      input.value = b.pages_per_test;
      return;
    }
  }
  try {
    A = await PATCH(`/api/batches/${b.id}`, { pages_per_test: value });
  } finally {
    render();
  }
}

async function deleteBatch(b) {
  let message = `Delete ${b.filename} and its ${plural(b.tests, 'test', 'tests')}?`;
  if (b.matched || b.annotations) {
    message += ` ${plural(b.matched, 'name match', 'name matches')} and ` +
      `${plural(b.annotations, 'placed comment', 'placed comments')} will be lost.`;
  }
  if (!await confirmBox(message)) return;
  A = await DELETE(`/api/batches/${b.id}`);
  render();
}

// ------------------------------------------------------------------ actions

// Anonymous grading hides the names on the grading screen and goes through the tests in scan
// order instead of roster order, which would name them by their place in the queue anyway.
// It changes nothing about matching, the results table or the exports.
function renderAnonymous() {
  $('#anonymous').checked = A.anonymous;
  $('#anonymous-note').textContent = A.anonymous
    ? 'Grading shows “Test 1”, “Test 2”… in scan order. Match names, results and exports are unchanged'
    : 'Hide the names while grading and take the tests in scan order';
}

$('#anonymous').addEventListener('change', async (e) => {
  const anonymous = e.target.checked;
  try {
    await PATCH(`/api/assignments/${assignmentId}`, { anonymous });
    A.anonymous = anonymous;
  } finally {
    renderAnonymous();
  }
});

function renderActions() {
  const p = A.progress;
  // Names and grades hang off page slots, so the page order is settled first.
  const unchecked = A.batches.filter((b) => !b.checked);
  const links = { '#go-match': 'match', '#go-grade': 'grade', '#go-results': 'results' };
  for (const [sel, page] of Object.entries(links)) {
    let enabled = p.submissions > 0 && (page !== 'grade' || A.problems.length > 0);
    if (unchecked.length && page !== 'results') enabled = false;
    $(sel).classList.toggle('disabled', !enabled);
    if (enabled) $(sel).href = `${page}.html?id=${assignmentId}`;
    else $(sel).removeAttribute('href');
  }
  $('#matched').textContent = `Matched ${p.matched}/${p.submissions}`;
  $('#graded').textContent = `Graded ${p.graded}/${p.gradable}`;
  const warn = [];
  if (unchecked.length) {
    warn.push(`Check the page order of ${unchecked.map((b) => b.filename).join(', ')} first`);
  }
  if (p.submissions && p.submissions !== p.students) {
    const diff = Math.abs(p.submissions - p.students);
    warn.push(p.submissions < p.students
      ? `${plural(p.submissions, 'test', 'tests')}, ${plural(p.students, 'student', 'students')}: ${diff} absent`
      : `${plural(p.submissions, 'test', 'tests')}, ${plural(p.students, 'student', 'students')}: ${diff} extra`);
  }
  $('#count-warning').replaceChildren(...warn.map((w) => h('span', { class: 'warning' }, w)));
}

function render() {
  header(assignmentCrumbs(A));
  if (document.activeElement !== $('#name')) $('#name').value = A.name;
  renderPages();
  renderAnswerKey();
  renderBatches();
  renderPending();
  renderAnonymous();
  renderActions();
}

async function load() {
  A = await GET(`/api/assignments/${assignmentId}`);
  render();
}

$('#name').addEventListener('change', async (e) => {
  const name = e.target.value.trim();
  if (!name) return;
  await PATCH(`/api/assignments/${assignmentId}`, { name });
  A.name = name;
  header(assignmentCrumbs(A));
});

$('#delete').addEventListener('click', async () => {
  if (!await confirmBox('Delete this assignment, its scans, comments and grades?')) return;
  const p = A.progress;
  const comments = A.problems.reduce((n, problem) => n + problem.comments, 0);
  const ok = await typedConfirmBox(
    `Warning: all work on ${A.name} will be lost, including ` +
    `${plural(A.batches.length, 'scan file', 'scan files')}, ${plural(p.submissions, 'test', 'tests')}, ` +
    `${plural(p.matched, 'name match', 'name matches')}, ${plural(p.graded, 'graded problem', 'graded problems')} ` +
    `and ${plural(comments, 'comment', 'comments')}. This cannot be undone. Are you sure?`);
  if (!ok) return;
  await DELETE(`/api/assignments/${assignmentId}`);
  location.href = `course.html?id=${A.course.id}`;
});

// Progress changes while grading in another tab or after navigating back.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && A && !pending.length) load();
});
window.addEventListener('pageshow', (e) => { if (e.persisted) load(); });

load();
