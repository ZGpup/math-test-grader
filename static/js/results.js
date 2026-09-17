'use strict';

const assignmentId = param('id');
let R = null;
let sort = { col: null, dir: 1 };

// Column definitions: title, optional "/max" label, sort value and cell text per row.
function columns() {
  const cols = [
    { title: 'First name', value: (r) => r.first_name.toLowerCase(), cell: (r) => r.first_name || (r.status === 'unmatched' ? 'Unmatched' : '') },
    { title: 'Last name', value: (r) => r.last_name.toLowerCase(), cell: (r) => r.last_name },
    {
      title: 'Grade %', num: true,
      value: (r) => r.percent,
      cell: (r) => (r.status === 'absent' ? 'Absent' : r.percent.toFixed(1)),
    },
  ];
  R.problems.forEach((p, i) => cols.push({
    title: p.label, max: `/${fmt(p.max_points)}`, num: true, problem: p,
    value: (r) => (r.cells.length ? r.cells[i].points : null),
    cell: (r) => (r.cells.length ? fmt(r.cells[i].points) : ''),
    ungraded: (r) => r.cells.length && !r.cells[i].graded,
  }));
  cols.push({
    title: 'Total', max: `/${fmt(R.total_possible)}`, num: true,
    value: (r) => r.total,
    cell: (r) => (r.total === null ? '' : fmt(r.total)),
  });
  return cols;
}

function sortedRows(cols) {
  const rows = R.rows.map((r, i) => ({ r, i }));
  if (sort.col === null) return rows.map((x) => x.r);
  const value = cols[sort.col].value;
  rows.sort((a, b) => {
    const va = value(a.r);
    const vb = value(b.r);
    if (va === null && vb === null) return a.i - b.i;
    if (va === null) return 1; // absent rows always last
    if (vb === null) return -1;
    if (va < vb) return -sort.dir;
    if (va > vb) return sort.dir;
    return a.i - b.i;
  });
  return rows.map((x) => x.r);
}

function render() {
  const cols = columns();
  const head = h('tr', {}, cols.map((c, i) => h('th', {
    class: `${c.num ? 'num' : ''} ${sort.col === i ? 'sorted' : ''}`,
    onclick: () => {
      sort = sort.col === i ? { col: i, dir: -sort.dir } : { col: i, dir: 1 };
      render();
    },
  }, c.title, sort.col === i ? (sort.dir > 0 ? ' ▲' : ' ▼') : '', c.max ? h('span', { class: 'max' }, c.max) : null)));

  const body = sortedRows(cols).map((r) => h('tr', { class: r.status },
    cols.map((c) => {
      const ungraded = c.ungraded && c.ungraded(r);
      const td = h('td', {
        class: [c.num ? 'num' : '', ungraded ? 'ungraded' : '', c.problem && r.submission_id ? 'cell' : ''].join(' '),
        title: ungraded ? 'Ungraded' : null,
      }, c.cell(r));
      if (c.problem && r.submission_id) {
        td.addEventListener('click', () => {
          location.href = `grade.html?id=${assignmentId}&problem=${c.problem.id}&sub=${r.submission_id}`;
        });
      }
      return td;
    })));
  if (!body.length) body.push(h('tr', { class: 'empty' }, h('td', { colspan: cols.length }, 'No students or tests')));
  $('#results').replaceChildren(h('thead', {}, head), h('tbody', {}, body));
}

async function load() {
  R = await GET(`/api/assignments/${assignmentId}/results`);
  header(assignmentCrumbs(R, 'Results'));
  $('#csv').href = `/api/assignments/${assignmentId}/results.csv`;
  render();
}

$('#export').addEventListener('click', async () => {
  const button = $('#export');
  button.disabled = true;
  $('#export-status').replaceChildren(h('span', { class: 'muted' }, 'Exporting'));
  try {
    const r = await POST(`/api/assignments/${assignmentId}/export`);
    $('#export-status').replaceChildren(
      h('span', {}, `Exported ${r.count} ${r.count === 1 ? 'PDF' : 'PDFs'}`),
      r.count ? h('a', { class: 'btn', href: r.zip }, 'Download zip') : null);
  } catch (_) {
    $('#export-status').replaceChildren();
  } finally {
    button.disabled = false;
  }
});

document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && R) load(); });
window.addEventListener('pageshow', (e) => { if (e.persisted) load(); });

load();
