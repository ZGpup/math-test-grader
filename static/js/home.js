'use strict';

async function load() {
  const courses = await GET('/api/courses');
  const rows = courses.map((c) => h('tr', {},
    h('td', {}, h('a', { href: `course.html?id=${c.id}` }, c.name)),
    h('td', { class: 'num' }, c.students),
    h('td', { class: 'num' }, c.assignments)));
  if (!rows.length) rows.push(h('tr', { class: 'empty' }, h('td', { colspan: 3 }, 'No courses')));
  $('#courses tbody').replaceChildren(...rows);
}

$('#add-course').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('#course-name').value.trim();
  if (!name) return;
  const course = await POST('/api/courses', { name });
  location.href = `course.html?id=${course.id}`;
});

header([['Grader']]);
load();
