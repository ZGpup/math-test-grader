'use strict';

const courseId = param('id');
let saveTimer = null;
let savedText = null;

function renderStudents(students) {
  $('#roster-count').textContent = students.length ? `(${students.length})` : '';
  const rows = students.map((s, i) => h('tr', {},
    h('td', { class: 'num muted' }, i + 1),
    h('td', {}, s.first_name),
    h('td', {}, s.last_name)));
  if (!rows.length) rows.push(h('tr', { class: 'empty' }, h('td', { colspan: 3 }, 'No students')));
  $('#students tbody').replaceChildren(...rows);
}

function renderAssignments(assignments) {
  const rows = assignments.map((a) => {
    const p = a.progress;
    return h('tr', {},
      h('td', {}, h('a', { href: `assignment.html?id=${a.id}` }, a.name)),
      h('td', { class: 'num' }, fmt(a.total_points)),
      h('td', { class: 'num' }, p.submissions),
      h('td', { class: 'num' }, `${p.matched}/${p.submissions}`),
      h('td', { class: 'num' }, `${p.graded}/${p.gradable}`));
  });
  if (!rows.length) rows.push(h('tr', { class: 'empty' }, h('td', { colspan: 5 }, 'No assignments')));
  $('#assignments tbody').replaceChildren(...rows);
}

async function saveRoster() {
  clearTimeout(saveTimer);
  const text = $('#roster').value;
  if (text === savedText) return;
  savedText = text;
  const r = await PUT(`/api/courses/${courseId}/roster`, { text });
  if (text === $('#roster').value) renderStudents(r.students);
}

async function load() {
  const course = await GET(`/api/courses/${courseId}`);
  header([['Grader', '/'], [course.name]]);
  $('#name').value = course.name;
  $('#roster').value = course.roster_text;
  savedText = course.roster_text;
  renderStudents(course.students);
  renderAssignments(course.assignments);
}

$('#roster').addEventListener('input', () => {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveRoster, 500);
});
$('#roster').addEventListener('blur', saveRoster);
window.addEventListener('pagehide', () => {
  const text = $('#roster').value;
  if (text === savedText) return;
  fetch(`/api/courses/${courseId}/roster`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
    keepalive: true,
  });
});

$('#name').addEventListener('change', async (e) => {
  const name = e.target.value.trim();
  if (!name) return;
  await PATCH(`/api/courses/${courseId}`, { name });
  header([['Grader', '/'], [name]]);
});

$('#delete').addEventListener('click', async () => {
  if (!await confirmBox('Delete this course, its roster, assignments and scans?')) return;
  await DELETE(`/api/courses/${courseId}`);
  location.href = '/';
});

$('#add-assignment').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('#assignment-name').value.trim();
  if (!name) return;
  const a = await POST(`/api/courses/${courseId}/assignments`, { name });
  location.href = `assignment.html?id=${a.id}`;
});

load();
