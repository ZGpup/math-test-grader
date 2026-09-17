'use strict';

const assignmentId = param('id');
let A = null; // assignment detail from the API
let pending = []; // [{file, ppt}] scans chosen but not uploaded yet
let uploading = false;

const KIND_LABELS = { cover: 'Cover', problem: 'Problem', none: 'None' };
const MODE_LABELS = { identity: 'one-to-one', odd: 'odd scan pages', custom: 'custom' };

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
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
  renderTotals();
  $('#pages').replaceChildren(...A.pages.map(pageCard));
}

function pageCard(p) {
  const select = h('select', { onchange: (e) => setKind(p.index, e.target.value) },
    Object.entries(KIND_LABELS).map(([k, label]) => h('option', { value: k, selected: p.kind === k }, label)));
  const fields = h('div', { class: 'fields' }, h('span', { class: 'muted' }, `p. ${p.index + 1}`), select);
  if (p.kind === 'problem') {
    const label = h('input', { value: p.problem.label, autocomplete: 'off' });
    const points = h('input', { type: 'number', min: '0', step: 'any', value: String(p.problem.max_points) });
    const save = () => saveProblem(p.index, label, points);
    label.addEventListener('change', save);
    points.addEventListener('change', save);
    fields.append(h('span', {}, 'Label'), label, h('span', {}, 'Points'), points);
  }
  return h('div', { class: `page-card ${p.kind}` },
    h('img', { src: pageUrl(A.blank_key, p.index, true), alt: '', onclick: () => showImage(pageUrl(A.blank_key, p.index)) }),
    fields);
}

async function setKind(index, kind) {
  const page = A.pages[index];
  if (page.kind === 'problem' && page.problem.comments > 0) {
    const ok = await confirmBox(
      `Problem ${page.problem.label} has ${plural(page.problem.comments, 'comment', 'comments')}. Remove the problem and its comments?`,
      'Remove');
    if (!ok) return renderPages();
  }
  try {
    A = await PUT(`/api/assignments/${assignmentId}/pages/${index}`, { kind });
  } finally {
    render();
  }
}

async function saveProblem(index, labelInput, pointsInput) {
  const points = parseFloat(pointsInput.value);
  if (!labelInput.value.trim() || !(points >= 0)) {
    labelInput.value = A.pages[index].problem.label;
    pointsInput.value = A.pages[index].problem.max_points;
    return;
  }
  // Update data without rebuilding the cards, so focus stays where the user tabbed to.
  A = await PUT(`/api/assignments/${assignmentId}/pages/${index}`,
    { kind: 'problem', label: labelInput.value.trim(), max_points: points });
  renderTotals();
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

// ------------------------------------------------------------------ scans

function renderPending() {
  if (!pending.length) return $('#pending').replaceChildren();
  const rows = pending.map((item, i) => h('tr', {},
    h('td', {}, item.file.name),
    h('td', {}, h('input', {
      type: 'number', min: '1', value: String(item.ppt), disabled: uploading,
      oninput: (e) => { item.ppt = parseInt(e.target.value, 10); },
    })),
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
      if (item.ppt >= 1) form.append('pages_per_test', String(item.ppt));
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
        `${b.page_count} pages is not divisible by ${b.pages_per_test}: ${plural(b.leftover, 'leftover page', 'leftover pages')} ignored`) : null,
      h('span', { class: 'spacer' }),
      h('button', { onclick: () => deleteBatch(b) }, 'Delete')),
    h('details', { open: b.mode === 'custom' },
      h('summary', {}, `Page mapping: ${MODE_LABELS[b.mode]}`),
      mappingTable(b)));
}

function mappingTable(b) {
  const pages = A.pages.filter((p) => p.kind !== 'none');
  const options = (current) => Array.from({ length: b.pages_per_test },
    (_, i) => h('option', { value: String(i), selected: current === i }, i + 1));
  return h('div', { class: 'map' }, h('table', { class: 'grid compact' },
    h('tr', {}, h('th', {}, 'Blank page'), pages.map((p) => h('td', {}, p.index + 1))),
    h('tr', {}, h('th', {}, 'Problem'),
      pages.map((p) => h('td', {}, p.kind === 'cover' ? 'Cover' : p.problem.label))),
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

function renderActions() {
  const p = A.progress;
  const links = { '#go-match': 'match', '#go-grade': 'grade', '#go-results': 'results' };
  for (const [sel, page] of Object.entries(links)) {
    const enabled = p.submissions > 0 && (page !== 'grade' || A.problems.length > 0);
    $(sel).classList.toggle('disabled', !enabled);
    if (enabled) $(sel).href = `${page}.html?id=${assignmentId}`;
    else $(sel).removeAttribute('href');
  }
  $('#matched').textContent = `Matched ${p.matched}/${p.submissions}`;
  $('#graded').textContent = `Graded ${p.graded}/${p.gradable}`;
  const warn = [];
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
  renderBatches();
  renderPending();
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
  await DELETE(`/api/assignments/${assignmentId}`);
  location.href = `course.html?id=${A.course.id}`;
});

// Progress changes while grading in another tab or after navigating back.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && A && !pending.length) load();
});
window.addEventListener('pageshow', (e) => { if (e.persisted) load(); });

load();
