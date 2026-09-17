'use strict';

const batchId = param('batch');
let S = null; // {id, name, course, batch, slots, tests}
let flat = []; // every page of the scan in order; the columns are this list cut into tests

const slots = () => S.batch.pages_per_test;

// ------------------------------------------------------------------ rendering

function render() {
  const b = S.batch;
  $('#file').textContent = b.filename;
  $('#count').textContent = `${plural(b.tests, 'test', 'tests')} of ${plural(slots(), 'page', 'pages')}`;
  $('#checked-state').replaceChildren(b.checked
    ? h('span', {}, '✓ Order checked')
    : h('span', { class: 'warning' }, 'Order not checked yet'));
  $('#warning').replaceChildren(b.annotations
    ? h('span', { class: 'warning' },
      `${plural(b.annotations, 'comment is', 'comments are')} already placed on these tests. ` +
      'Moving a page now leaves them on the slot they were placed on.')
    : '');
  if (!b.tests) {
    $('#board').replaceChildren(h('p', { class: 'muted' }, 'This file has no full test in it.'));
    return;
  }
  $('#board').replaceChildren(h('div', { class: 'organize' },
    labelColumn(), S.tests.map((t, i) => testColumn(t, i))));
}

function labelColumn() {
  const rows = [];
  for (let k = 0; k < slots(); k++) {
    rows.push(h('div', { class: 'slot' },
      h('div', {}, `Page ${k + 1}`),
      h('div', { class: 'muted' }, S.slots[k] || ''),
      h('div', { class: 'row' },
        h('button', { class: 'link', title: 'Turn this row upside down', onclick: () => flipRow(k, 'upside_down') }, 'All 180°'),
        h('button', { class: 'link', title: 'Mirror this row', onclick: () => flipRow(k, 'mirrored') }, 'All ⇄'))));
  }
  return h('div', { class: 'col labels' }, h('div', { class: 'head' }), rows);
}

function testColumn(t, i) {
  const cells = [];
  for (let k = 0; k < slots(); k++) cells.push(cell(i * slots() + k));
  return h('div', { class: 'col' },
    h('div', { class: 'head' },
      h('strong', {}, `Test ${i + 1}`),
      h('div', { class: 'muted' }, studentName(t))),
    cells);
}

function cell(i) {
  const p = flat[i];
  const flipButton = (field, label, title) => h('button', {
    class: p[field] ? 'on' : '',
    title,
    onclick: (e) => { e.stopPropagation(); flip(i, field); },
  }, label);
  const el = h('div', { class: 'cell', draggable: true },
    h('img', {
      src: pageUrl(S.batch.key, p.scan_page, true), alt: '', loading: 'lazy', draggable: false, class: flipClass(p),
    }),
    h('span', { class: 'pnum' }, `p. ${p.scan_page + 1}`),
    h('span', { class: 'tools' },
      flipButton('upside_down', '180°', 'Upside down'),
      flipButton('mirrored', '⇄', 'Mirrored')));
  el.addEventListener('click', () => showImage(pageUrl(S.batch.key, p.scan_page), flipClass(p)));
  el.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('application/x-page', String(i));
    e.dataTransfer.effectAllowed = 'move';
    el.classList.add('dragging');
  });
  el.addEventListener('dragend', () => el.classList.remove('dragging'));
  el.addEventListener('dragover', (e) => {
    if (!e.dataTransfer.types.includes('application/x-page')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    el.classList.add('drop-target');
  });
  el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    el.classList.remove('drop-target');
    move(parseInt(e.dataTransfer.getData('application/x-page'), 10), i);
  });
  return el;
}

// ------------------------------------------------------------------ editing

// Show the new order at once, then save it. The server has the last word.
async function apply(next) {
  flat = next;
  render();
  try {
    S = await PUT(`/api/batches/${batchId}/pages`, { pages: flat });
    flat = S.tests.flatMap((t) => t.pages);
    render();
  } catch (err) {
    await load();
  }
}

// Pull the page out and put it back in at `to`; everything in between shifts along.
function move(from, to) {
  if (!(from >= 0) || from === to) return;
  const next = [...flat];
  next.splice(to, 0, next.splice(from, 1)[0]);
  apply(next);
}

function flip(i, field) {
  apply(flat.map((p, k) => (k === i ? { ...p, [field]: p[field] ? 0 : 1 } : p)));
}

// Turn a whole row: all of it back the right way up if it is already flipped, otherwise all flipped.
function flipRow(slot, field) {
  const inRow = (k) => k % slots() === slot;
  const value = flat.some((p, k) => inRow(k) && !p[field]) ? 1 : 0;
  apply(flat.map((p, k) => (inRow(k) ? { ...p, [field]: value } : p)));
}

$('#reset').addEventListener('click', async () => {
  if (!await confirmBox('Put every page back in the order it was scanned and undo all flips?', 'Reset')) return;
  apply([...flat]
    .sort((a, b) => a.scan_page - b.scan_page)
    .map((p) => ({ scan_page: p.scan_page, upside_down: 0, mirrored: 0 })));
});

$('#confirm').addEventListener('click', async () => {
  await PATCH(`/api/batches/${batchId}`, { checked: true });
  location.href = `assignment.html?id=${S.id}`;
});

// ------------------------------------------------------------------ load

async function load() {
  S = await GET(`/api/batches/${batchId}/pages`);
  flat = S.tests.flatMap((t) => t.pages);
  header(assignmentCrumbs(S, 'Page order'));
  render();
}

load();
