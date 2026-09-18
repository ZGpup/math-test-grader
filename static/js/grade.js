'use strict';

const assignmentId = param('id');
let G = null; // grading state: problems (with comments), submissions (roster order), annotations
let pid = null; // current problem id
let sidx = 0; // current submission index
let page = 0; // page offset within the current submission
let editing = null; // id of the comment being edited
let keyView = false; // the answer key column is open beside the student's page
let keyScrolledFor = null; // the problem the key column was last scrolled to
let comments = new Map(); // comment id -> comment
const graded = new Set(); // "submissionId:problemId"

const DED_HINT = 'Points taken off. A negative number gives points instead, for bonus questions.';
const key = (sid, problemId) => `${sid}:${problemId}`;
const problem = () => G.problems.find((p) => p.id === pid);
const sub = () => G.submissions[sidx];
const mappedPage = (s = sub(), p = problem()) => s.page_map[p.page];
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const sheet = $('#sheet');
const img = $('#page');
const keyViewer = $('#key-viewer');

function indexComments() {
  comments = new Map(G.problems.flatMap((p) => p.comments.map((c) => [c.id, c])));
}

function appliedComments(s) {
  return new Set(G.annotations.filter((a) => a.submission_id === s.id).map((a) => a.comment_id));
}

// Same rule as app/db.py score_table: each placement of a comment deducts.
function scoreFor(s, p) {
  let deduction = 0;
  for (const a of G.annotations) {
    if (a.submission_id !== s.id) continue;
    const c = comments.get(a.comment_id);
    if (c && c.problem_id === p.id) deduction += c.deduction;
  }
  return Math.max(0, p.max_points - deduction);
}

let noticeTimer = null;
function notice(text) {
  $('#notice').textContent = text;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $('#notice').textContent = ''; }, 3000);
}

// ------------------------------------------------------------------ answer key

// The key is a column of its own that scrolls beside the student's work: every page is there, so
// a key of any length is read by scrolling. The pages are built once, when the screen loads, and
// their images are fetched right away even though the column starts closed -- a page with no image
// yet has no height, and scrolling to one of them would land nowhere.
const hasKey = () => !!G.answer_key && G.answer_key_pages > 0;

function buildKeyPages() {
  if (!hasKey()) return;
  $('#key-pages').replaceChildren(...Array.from({ length: G.answer_key_pages }, (_, i) =>
    h('div', { class: 'sheet key-sheet' },
      h('img', { src: pageUrl(G.answer_key, i), alt: '', draggable: false }),
      h('div', { class: 'key-cap' }, `p. ${i + 1}`))));
}

// A key usually runs page for page with the blank test, so a problem's answers are on the key page
// sitting where the problem does, clamped to the key's last page. That is where the column scrolls
// to when the key opens and when the problem changes; scrolling it by hand is left alone.
function scrollKeyToProblem() {
  const target = $('#key-pages').children[clamp(problem().page, 0, G.answer_key_pages - 1)];
  if (target) target.scrollIntoView({ block: 'start' });
}

function toggleKey() {
  if (!hasKey()) return;
  keyView = !keyView;
  render();
}

function renderKey() {
  const on = keyView && hasKey();
  const button = $('#answer-key');
  button.hidden = !hasKey();
  button.classList.toggle('on', on);
  button.title = `${on ? 'Hide' : 'Show'} the answer key (a)`;
  keyViewer.hidden = !on;
  $('#workspace').classList.toggle('with-key', on);
  if (!on) {
    keyScrolledFor = null;
    return;
  }
  if (keyScrolledFor !== pid) {
    scrollKeyToProblem();
    keyScrolledFor = pid;
  }
}

// ------------------------------------------------------------------ rendering

function render() {
  const p = problem();
  const s = sub();
  history.replaceState(null, '', `?id=${assignmentId}&problem=${pid}&sub=${s.id}`);
  renderTabs();
  $('#student').textContent = studentName(s) || 'Unmatched';
  $('#student').classList.toggle('muted', !s.student_id);
  $('#position').textContent = `${sidx + 1}/${G.submissions.length}`;
  $('#prev-sub').disabled = sidx === 0;
  $('#next-sub').disabled = sidx === G.submissions.length - 1;

  const src = pageSrc(s, page);
  if (img.getAttribute('src') !== src) img.src = src;
  img.className = flipClass(pageAt(s, page));
  $('#page-label').textContent = `Page ${page + 1}/${s.page_count}`;
  $('#prev-page').disabled = page === 0;
  $('#next-page').disabled = page === s.page_count - 1;

  renderKey();
  renderAnnotations();
  renderScore();
  renderComments();
  preload(p);
}

function renderTabs() {
  const n = G.submissions.length;
  $('#tabs').replaceChildren(...G.problems.map((p) => {
    const done = G.submissions.filter((s) => graded.has(key(s.id, p.id))).length;
    return h('button', { class: p.id === pid ? 'active' : '', title: `Problem ${p.label}`, onclick: () => selectProblem(p.id) },
      p.label, h('span', { class: 'count' }, `${done}/${n}`));
  }));
}

function renderScore() {
  const p = problem();
  const s = sub();
  const isGraded = graded.has(key(s.id, p.id));
  $('#score').textContent = `${fmt(scoreFor(s, p))} / ${fmt(p.max_points)}`;
  $('#graded-state').textContent = isGraded ? 'Graded' : 'Ungraded';
  $('#graded-state').classList.toggle('on', isGraded);
  $('#graded-state').title = isGraded ? 'Mark ungraded' : 'Mark graded';
}

// Points off, or points on when the deduction is negative. The unit keeps a bare
// number from reading as part of the comment's math (see .ann .ded, drawn as a superscript).
function points(d) {
  return d > 0 ? `−${fmt(d)}pts` : `+${fmt(-d)}pts`;
}

function annContent(c) {
  const box = tex('div', c.text, { class: 'ann' });
  if (c.deduction) box.append(h('span', { class: c.deduction < 0 ? 'ded bonus' : 'ded' }, points(c.deduction)));
  return box;
}

function renderAnnotations() {
  for (const el of sheet.querySelectorAll('.ann')) el.remove();
  const s = sub();
  for (const a of G.annotations) {
    if (a.submission_id !== s.id || a.page !== page) continue;
    const c = comments.get(a.comment_id);
    if (c) sheet.append(annBox(a, c));
  }
}

function annBox(a, c) {
  const box = annContent(c);
  box.style.left = `${a.x * 100}%`;
  box.style.top = `${a.y * 100}%`;
  if (c.problem_id !== pid) {
    const other = G.problems.find((p) => p.id === c.problem_id);
    box.classList.add('other');
    box.title = other ? `Problem ${other.label}` : '';
    return box;
  }
  const remove = h('button', { class: 'x', title: 'Remove' }, '×');
  remove.addEventListener('pointerdown', (e) => e.stopPropagation());
  remove.addEventListener('click', (e) => {
    e.stopPropagation();
    removeAnnotation(a);
  });
  box.append(remove);
  box.addEventListener('pointerdown', (e) => startMove(e, box, a));
  return box;
}

function renderComments() {
  const p = problem();
  const applied = appliedComments(sub());
  $('#comments').replaceChildren(...p.comments.map((c, i) =>
    (editing === c.id ? editRow(c) : commentRow(c, i, applied.has(c.id)))));
}

function commentRow(c, i, applied) {
  const li = h('li', { class: applied ? 'applied' : '', draggable: true },
    h('span', { class: 'key' }, i < 9 ? i + 1 : ''),
    tex('span', c.text, { class: 'text' }),
    h('span', { class: c.deduction < 0 ? 'ded bonus' : 'ded' }, c.deduction ? points(c.deduction) : ''),
    h('span', { class: 'tools' },
      h('button', { class: 'link', onclick: (e) => { e.stopPropagation(); editing = c.id; renderComments(); } }, 'Edit'),
      h('button', { class: 'link', onclick: (e) => { e.stopPropagation(); deleteComment(c); } }, 'Delete')));
  li.addEventListener('click', () => placeAtDefault(c));
  li.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('application/x-comment', String(c.id));
    e.dataTransfer.effectAllowed = 'copy';
  });
  return li;
}

function usesOf(c) {
  return new Set(G.annotations.filter((a) => a.comment_id === c.id).map((a) => a.submission_id)).size;
}

function editRow(c) {
  const text = h('input', { type: 'text', value: c.text, autocomplete: 'off', spellcheck: 'false' });
  const preview = h('div', { class: 'preview' });
  const ded = h('input', { type: 'number', step: 'any', value: String(c.deduction), title: DED_HINT });
  const uses = usesOf(c);
  text.addEventListener('input', () => renderTex(preview, text.value));
  renderTex(preview, c.text);
  const cancel = () => { editing = null; renderComments(); };
  const form = h('form', { class: 'comment-form' },
    text, preview,
    h('div', { class: 'row' },
      h('label', { title: DED_HINT }, 'Points off ', ded),
      uses ? h('span', { class: 'muted' }, `Used on ${uses}`) : null,
      h('span', { class: 'spacer' }),
      h('button', { type: 'button', onclick: cancel }, 'Cancel'),
      h('button', { class: 'primary' }, 'Save')));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const deduction = parseFloat(ded.value || '0');
    if (!text.value.trim() || !Number.isFinite(deduction)) return showError('A comment needs text and a number of points');
    await PATCH(`/api/comments/${c.id}`, { text: text.value.trim(), deduction });
    c.text = text.value.trim();
    c.deduction = deduction;
    editing = null;
    render();
  });
  form.addEventListener('keydown', (e) => { if (e.key === 'Escape') cancel(); });
  setTimeout(() => text.focus());
  return h('li', { class: 'editing' }, form);
}

function preload(p) {
  for (const j of [sidx + 1, sidx - 1]) {
    const s = G.submissions[j];
    if (s) new Image().src = pageSrc(s, mappedPage(s, p));
  }
}

// ------------------------------------------------------------------ placing comments

function imageRect() {
  return img.getBoundingClientRect();
}

// Size of a comment box as fractions of the page.
function measure(c) {
  const rect = imageRect();
  if (!rect.width || !rect.height) return { w: 0.3, h: 0.04 };
  const box = annContent(c);
  box.style.visibility = 'hidden';
  box.style.left = '0';
  box.style.top = '0';
  sheet.append(box);
  const size = { w: box.offsetWidth / rect.width, h: box.offsetHeight / rect.height };
  box.remove();
  return size;
}

// Top-right corner, below the boxes already on the right half of this page.
function defaultSpot(c) {
  const rect = imageRect();
  const size = measure(c);
  let y = 0.02;
  if (rect.width && rect.height) {
    for (const el of sheet.querySelectorAll('.ann')) {
      if ((el.offsetLeft + el.offsetWidth) / rect.width > 0.5) {
        y = Math.max(y, (el.offsetTop + el.offsetHeight) / rect.height + 0.006);
      }
    }
  }
  if (y + size.h > 0.98) y = 0.02;
  return { x: clamp(0.98 - size.w, 0, 1), y };
}

async function placeAnnotation(c, x, y) {
  const s = sub();
  const a = await POST(`/api/submissions/${s.id}/annotations`, { comment_id: c.id, page, x, y });
  G.annotations.push(a);
  if (sub() === s) render();
}

function placeAtDefault(c) {
  if (c.problem_id !== pid) return;
  const spot = defaultSpot(c);
  return placeAnnotation(c, spot.x, spot.y);
}

async function removeAnnotation(a) {
  await DELETE(`/api/annotations/${a.id}`);
  G.annotations = G.annotations.filter((x) => x !== a);
  render();
}

function startMove(e, box, a) {
  if (e.button !== 0) return;
  e.preventDefault();
  const rect = imageRect();
  const startX = e.clientX;
  const startY = e.clientY;
  const maxX = Math.max(0, 1 - box.offsetWidth / rect.width);
  const maxY = Math.max(0, 1 - box.offsetHeight / rect.height);
  let x = a.x;
  let y = a.y;
  let moved = false;
  box.setPointerCapture(e.pointerId);
  box.classList.add('dragging');
  const onMove = (ev) => {
    moved = moved || Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) > 2;
    x = clamp(a.x + (ev.clientX - startX) / rect.width, 0, maxX);
    y = clamp(a.y + (ev.clientY - startY) / rect.height, 0, maxY);
    box.style.left = `${x * 100}%`;
    box.style.top = `${y * 100}%`;
  };
  const onUp = async () => {
    box.removeEventListener('pointermove', onMove);
    box.removeEventListener('pointerup', onUp);
    box.removeEventListener('pointercancel', onUp);
    box.classList.remove('dragging');
    if (!moved) return;
    a.x = x;
    a.y = y;
    await PATCH(`/api/annotations/${a.id}`, { x, y });
  };
  box.addEventListener('pointermove', onMove);
  box.addEventListener('pointerup', onUp);
  box.addEventListener('pointercancel', onUp);
}

sheet.addEventListener('dragover', (e) => {
  if (!e.dataTransfer.types.includes('application/x-comment')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
  sheet.classList.add('drop-target');
});
sheet.addEventListener('dragleave', (e) => {
  if (!sheet.contains(e.relatedTarget)) sheet.classList.remove('drop-target');
});
sheet.addEventListener('drop', (e) => {
  sheet.classList.remove('drop-target');
  const c = comments.get(parseInt(e.dataTransfer.getData('application/x-comment'), 10));
  if (!c || c.problem_id !== pid) return;
  e.preventDefault();
  const rect = imageRect();
  const size = measure(c);
  const x = clamp((e.clientX - rect.left) / rect.width, 0, Math.max(0, 1 - size.w));
  const y = clamp((e.clientY - rect.top) / rect.height, 0, Math.max(0, 1 - size.h));
  placeAnnotation(c, x, y);
});

// ------------------------------------------------------------------ comments

async function deleteComment(c) {
  const uses = usesOf(c);
  if (uses > 0) {
    const ok = await confirmBox(
      `This comment is used on ${uses} ${uses === 1 ? 'submission' : 'submissions'}. Delete it and remove it from all of them?`);
    if (!ok) return;
  }
  await DELETE(`/api/comments/${c.id}`);
  const p = G.problems.find((x) => x.id === c.problem_id);
  p.comments = p.comments.filter((x) => x !== c);
  G.annotations = G.annotations.filter((a) => a.comment_id !== c.id);
  indexComments();
  render();
}

$('#nc-text').addEventListener('input', () => renderTex($('#nc-preview'), $('#nc-text').value));
$('#nc-text').addEventListener('keydown', (e) => { if (e.key === 'Escape') e.target.blur(); });
$('#new-comment').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('#nc-text').value.trim();
  const deduction = parseFloat($('#nc-ded').value || '0');
  if (!text) return;
  if (!Number.isFinite(deduction)) return showError('Points off must be a number');
  const p = problem();
  const c = await POST(`/api/problems/${p.id}/comments`, { text, deduction });
  p.comments.push(c);
  indexComments();
  $('#nc-text').value = '';
  $('#nc-ded').value = '0';
  renderTex($('#nc-preview'), '');
  document.activeElement.blur();
  if (pid === p.id) await placeAtDefault(c);
  else render();
});

// ------------------------------------------------------------------ navigation

function go(i) {
  sidx = clamp(i, 0, G.submissions.length - 1);
  page = mappedPage();
  editing = null;
  render();
}

function selectProblem(id) {
  pid = id;
  const firstUngraded = G.submissions.findIndex((s) => !graded.has(key(s.id, pid)));
  go(firstUngraded >= 0 ? firstUngraded : sidx);
}

async function setGraded(s, p, value) {
  const k = key(s.id, p.id);
  if (value) graded.add(k);
  else graded.delete(k);
  try {
    await PUT(`/api/submissions/${s.id}/graded/${p.id}`, { graded: value });
  } catch (err) {
    if (value) graded.delete(k);
    else graded.add(k);
    render();
    throw err;
  }
}

// Mark graded and advance. Updates locally first so fast repeated presses never skip a student.
function next() {
  if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  const s = sub();
  const p = problem();
  setGraded(s, p, true).catch(() => {});
  if (sidx < G.submissions.length - 1) return go(sidx + 1);
  const firstUngraded = G.submissions.findIndex((x) => !graded.has(key(x.id, p.id)));
  if (firstUngraded >= 0) {
    go(firstUngraded);
    notice('Back to first ungraded');
  } else {
    render();
    notice(`Problem ${p.label} graded`);
  }
}

$('#next').addEventListener('click', next);
$('#graded-state').addEventListener('click', async () => {
  const s = sub();
  const p = problem();
  await setGraded(s, p, !graded.has(key(s.id, p.id)));
  render();
});
$('#prev-sub').addEventListener('click', () => go(sidx - 1));
$('#next-sub').addEventListener('click', () => go(sidx + 1));
$('#prev-page').addEventListener('click', () => { page = Math.max(0, page - 1); render(); });
$('#next-page').addEventListener('click', () => { page = Math.min(sub().page_count - 1, page + 1); render(); });
$('#answer-key').addEventListener('click', toggleKey);

document.addEventListener('keydown', (e) => {
  if (!G || !G.submissions.length || !G.problems.length) return;
  if (modalOpen() || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest('input, textarea, select')) return;
  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    go(sidx - 1);
  } else if (e.key === 'ArrowRight') {
    e.preventDefault();
    go(sidx + 1);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    next();
  } else if (e.key === 'a' || e.key === 'A') {
    e.preventDefault();
    toggleKey();
  } else if (/^[1-9]$/.test(e.key)) {
    const c = problem().comments[parseInt(e.key, 10) - 1];
    if (c) placeAtDefault(c);
  }
});

// ------------------------------------------------------------------ load

async function load() {
  G = await GET(`/api/assignments/${assignmentId}/grading`);
  header(assignmentCrumbs(G, 'Grade'));
  for (const [s, p] of G.graded) graded.add(key(s, p));
  indexComments();
  if (!G.problems.length || !G.submissions.length) {
    $('#student').textContent = G.problems.length ? 'No tests' : 'No problems';
    for (const el of document.querySelectorAll('button, input')) el.disabled = true;
    return;
  }
  buildKeyPages();
  const requestedProblem = parseInt(param('problem'), 10);
  pid = G.problems.some((p) => p.id === requestedProblem) ? requestedProblem : G.problems[0].id;
  const requestedSub = G.submissions.findIndex((s) => String(s.id) === param('sub'));
  const firstUngraded = G.submissions.findIndex((s) => !graded.has(key(s.id, pid)));
  go(requestedSub >= 0 ? requestedSub : Math.max(0, firstUngraded));
}

load();
