'use strict';

const assignmentId = param('id');
let M = null; // {name, course, cover_page, submissions (scan order), students}
let idx = 0; // current submission
let page = 0; // page offset within the current submission

const current = () => M.submissions[idx];
const coverOffset = (s) => s.page_map[M.cover_page];

function show(i) {
  idx = Math.max(0, Math.min(M.submissions.length - 1, i));
  page = coverOffset(current());
  render();
}

function nextUnmatched(from) {
  const n = M.submissions.length;
  for (let k = 1; k <= n; k++) {
    const i = (from + k) % n;
    if (!M.submissions[i].student_id) return i;
  }
  return -1;
}

function filteredStudents() {
  const q = $('#filter').value.trim().toLowerCase();
  if (!q) return M.students;
  return M.students.filter((st) =>
    `${st.first_name} ${st.last_name}`.toLowerCase().includes(q) || studentName(st).toLowerCase().includes(q));
}

function render() {
  const subs = M.submissions;
  const matched = subs.filter((s) => s.student_id).length;
  $('#status').textContent = `Matched ${matched}/${subs.length}`;
  const diff = subs.length - M.students.length;
  $('#warning').replaceChildren(diff === 0 ? '' : h('span', { class: 'warning' },
    diff < 0
      ? `${subs.length} tests, ${M.students.length} students: ${-diff} absent`
      : `${subs.length} tests, ${M.students.length} students: ${diff} extra`));

  if (!subs.length) {
    $('#current').textContent = 'No tests';
    return;
  }
  const s = current();
  history.replaceState(null, '', `?id=${assignmentId}&sub=${s.id}`);
  $('#current').textContent = studentName(s) || 'Unmatched';
  $('#current').classList.toggle('muted', !s.student_id);
  $('#position').textContent = `${idx + 1}/${subs.length}`;
  $('#unmatch').disabled = !s.student_id;

  $('#page').src = pageSrc(s, page);
  $('#page').className = flipClass(pageAt(s, page));
  $('#page-label').textContent = `Page ${page + 1}/${s.page_count}`;
  $('#prev-page').disabled = page <= 0;
  $('#next-page').disabled = page >= s.page_count - 1;

  $('#subs').replaceChildren(...subs.map((sub, i) => {
    const li = h('li', { class: i === idx ? 'current' : '', onclick: () => show(i) },
      h('img', {
        src: pageSrc(sub, coverOffset(sub), true), alt: '', loading: 'lazy',
        class: flipClass(pageAt(sub, coverOffset(sub))),
      }),
      h('span', { class: sub.student_id ? '' : 'unmatched' }, `${i + 1}. `, studentName(sub) || 'Unmatched'));
    return li;
  }));
  $('#subs').children[idx].scrollIntoView({ block: 'nearest' });
  renderRoster();
}

function renderRoster() {
  const s = current();
  const taken = new Set(M.submissions.filter((x) => x.student_id).map((x) => x.student_id));
  const list = filteredStudents();
  const firstHit = $('#filter').value.trim() ? (list.find((st) => !taken.has(st.id)) || list[0]) : null;
  const items = list.map((st) => {
    let cls = taken.has(st.id) ? 'matched' : '';
    if (s && s.student_id === st.id) cls = 'current';
    if (st === firstHit) cls += ' first-hit';
    return h('li', { class: cls, onclick: () => assign(st.id) }, studentName(st));
  });
  if (!M.students.length) items.push(h('li', { class: 'matched' }, 'Roster is empty'));
  $('#roster').replaceChildren(...items);
  return firstHit;
}

async function assign(studentId) {
  const s = current();
  if (!s) return;
  const r = await PUT(`/api/submissions/${s.id}/student`, { student_id: studentId });
  M.submissions = r.submissions;
  $('#filter').value = '';
  const next = nextUnmatched(idx);
  if (next >= 0) show(next);
  else render();
  $('#filter').focus();
}

$('#unmatch').addEventListener('click', async () => {
  const r = await PUT(`/api/submissions/${current().id}/student`, { student_id: null });
  M.submissions = r.submissions;
  render();
});

$('#prev-page').addEventListener('click', () => { page--; render(); });
$('#next-page').addEventListener('click', () => { page++; render(); });

$('#filter').addEventListener('input', renderRoster);
$('#filter').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const hit = renderRoster();
    if (hit) assign(hit.id);
  } else if (e.key === 'Escape') {
    $('#filter').value = '';
    renderRoster();
  }
});

document.addEventListener('keydown', (e) => {
  if (modalOpen() || !M || !M.submissions.length) return;
  const inInput = e.target.closest('input, textarea, select');
  if (e.key === 'ArrowUp' || (e.key === 'ArrowLeft' && !inInput)) {
    e.preventDefault();
    show(idx - 1);
  } else if (e.key === 'ArrowDown' || (e.key === 'ArrowRight' && !inInput)) {
    e.preventDefault();
    show(idx + 1);
  }
});

async function load() {
  M = await GET(`/api/assignments/${assignmentId}/submissions`);
  header(assignmentCrumbs(M, 'Match names'));
  const requested = M.submissions.findIndex((s) => String(s.id) === param('sub'));
  const firstUnmatched = M.submissions.findIndex((s) => !s.student_id);
  if (M.submissions.length) show(requested >= 0 ? requested : Math.max(0, firstUnmatched));
  else render();
  $('#filter').focus();
}

load();
