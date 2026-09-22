'use strict';

// ------------------------------------------------------------------ API

let pendingRequests = 0;

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const mutating = method !== 'GET';
  if (mutating) setSaveState(++pendingRequests);
  try {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let message = `${r.status} ${r.statusText}`;
      try {
        const j = await r.json();
        message = typeof j.detail === 'string' ? j.detail : j.detail.map((d) => d.msg).join('; ');
      } catch (_) { /* not JSON */ }
      showError(message);
      throw Object.assign(new Error(message), { shown: true });
    }
    return await r.json();
  } catch (err) {
    if (err instanceof TypeError) {
      showError('Server not reachable');
      err.shown = true;
    }
    throw err;
  } finally {
    if (mutating) setSaveState(--pendingRequests);
  }
}

// API errors are already shown in the error bar; don't also report them as uncaught.
window.addEventListener('unhandledrejection', (e) => {
  if (e.reason && e.reason.shown) e.preventDefault();
});

const GET = (url) => api('GET', url);
const POST = (url, body) => api('POST', url, body);
const PUT = (url, body) => api('PUT', url, body);
const PATCH = (url, body) => api('PATCH', url, body);
const DELETE = (url) => api('DELETE', url);

function setSaveState(pending) {
  const el = document.querySelector('#top .save-state');
  if (el) el.textContent = pending > 0 ? 'Saving' : 'Saved';
}

let errorTimer = null;
function showError(message) {
  let bar = document.getElementById('error-bar');
  if (!bar) {
    bar = h('div', { id: 'error-bar' });
    document.body.append(bar);
  }
  bar.textContent = message;
  bar.hidden = false;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => { bar.hidden = true; }, 6000);
}

// ------------------------------------------------------------------ DOM

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const $ = (sel, root = document) => root.querySelector(sel);

function param(name) {
  return new URLSearchParams(location.search).get(name);
}

// Breadcrumb header. crumbs: [[label, href], ...]; the last one is the current page.
function header(crumbs) {
  const top = document.getElementById('top');
  top.replaceChildren();
  crumbs.forEach(([label, href], i) => {
    if (i > 0) top.append(h('span', { class: 'sep' }, '/'));
    const last = i === crumbs.length - 1;
    top.append(last || !href ? h('span', { class: last ? 'current' : '' }, label) : h('a', { href }, label));
  });
  top.append(h('span', { class: 'save-state' }, 'Saved'));
  document.title = crumbs.map((c) => c[0]).reverse().join(' - ');
}

function assignmentCrumbs(a, page) {
  const crumbs = [['Grader', '/'], [a.course.name, `course.html?id=${a.course.id}`]];
  if (page) crumbs.push([a.name, `assignment.html?id=${a.id}`], [page]);
  else crumbs.push([a.name]);
  return crumbs;
}

// ------------------------------------------------------------------ formatting

function fmt(n) {
  return String(Math.round(n * 100) / 100);
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function studentName(s) {
  if (!s || (!s.first_name && !s.last_name)) return '';
  return s.first_name ? `${s.last_name}, ${s.first_name}` : s.last_name;
}

function pageUrl(key, index, thumb = false) {
  return `/api/pages/${key}/${index}.png${thumb ? '?thumb=1' : ''}`;
}

// A test's page at a page offset: which scan page it is and how it was turned on the organize screen.
// Falls back to plain scan order, so a test without a stored page list still shows its pages.
function pageAt(s, offset) {
  const pages = s.pages || [];
  return pages[Math.max(0, Math.min(offset, pages.length - 1))]
    || { scan_page: s.first_page + offset, upside_down: 0, mirrored: 0 };
}

function pageSrc(s, offset, thumb = false) {
  return pageUrl(s.key, pageAt(s, offset).scan_page, thumb);
}

// Which page of a test carries the student's name: the mapped cover page, or the first page when
// the assignment has no cover page (coverPage is -1). app/pdf.py cover_offset does the same for
// the export.
function coverOffset(s, coverPage) {
  const at = coverPage >= 0 ? s.page_map[coverPage] : 0;
  return Math.max(0, Math.min(at || 0, s.page_count - 1));
}

// Classes that turn an image the way its page was fixed on the organize screen.
function flipClass(p) {
  return `flip${p && p.upside_down ? ' upside' : ''}${p && p.mirrored ? ' mirrored' : ''}`;
}

// Render text with $...$ math via KaTeX. app/export.py splits text the same way.
function renderTex(el, text) {
  el.replaceChildren();
  for (const part of text.split(/(\$[^$]+\$)/)) {
    if (!part) continue;
    if (part.length > 2 && part.startsWith('$') && part.endsWith('$')) {
      const span = document.createElement('span');
      try {
        katex.render(part.slice(1, -1), span, { throwOnError: true, displayMode: false });
      } catch (_) {
        span.textContent = part;
      }
      el.append(span);
    } else {
      el.append(document.createTextNode(part));
    }
  }
  return el;
}

function tex(tag, text, attrs = {}) {
  return renderTex(h(tag, attrs), text);
}

// ------------------------------------------------------------------ modals

function modalOpen() {
  return !!document.querySelector('.modal-backdrop');
}

function openModal(content, cls = '') {
  const modal = h('div', { class: `modal ${cls}` }, content);
  const backdrop = h('div', { class: 'modal-backdrop' }, modal);
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey, true);
  };
  const onKey = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      backdrop.dispatchEvent(new Event('dismiss'));
    }
  };
  backdrop.addEventListener('mousedown', (e) => {
    if (e.target === backdrop) {
      close();
      backdrop.dispatchEvent(new Event('dismiss'));
    }
  });
  document.addEventListener('keydown', onKey, true);
  document.body.append(backdrop);
  return { backdrop, modal, close };
}

// Resolves true if confirmed.
function confirmBox(message, okLabel = 'Delete') {
  return new Promise((resolve) => {
    const ok = h('button', { class: 'primary' }, okLabel);
    const cancel = h('button', {}, 'Cancel');
    const { backdrop, close } = openModal([h('div', {}, message), h('div', { class: 'buttons' }, cancel, ok)]);
    backdrop.addEventListener('dismiss', () => resolve(false));
    ok.addEventListener('click', () => { close(); resolve(true); });
    cancel.addEventListener('click', () => { close(); resolve(false); });
    ok.focus();
  });
}

// Resolves true only after the user types `word` (case-insensitive) and confirms.
function typedConfirmBox(message, word = 'yes', okLabel = 'Delete') {
  return new Promise((resolve) => {
    const input = h('input', { type: 'text', autocomplete: 'off', spellcheck: 'false' });
    const ok = h('button', { class: 'primary', disabled: true }, okLabel);
    const cancel = h('button', {}, 'Cancel');
    const matches = () => input.value.trim().toLowerCase() === word;
    const { backdrop, close } = openModal([
      h('div', { class: 'warning' }, message),
      h('label', { class: 'typed-confirm' }, `Type “${word}” to confirm`, input),
      h('div', { class: 'buttons' }, cancel, ok),
    ]);
    const finish = (value) => { close(); resolve(value); };
    backdrop.addEventListener('dismiss', () => resolve(false));
    input.addEventListener('input', () => { ok.disabled = !matches(); });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && matches()) finish(true); });
    ok.addEventListener('click', () => { if (matches()) finish(true); });
    cancel.addEventListener('click', () => finish(false));
    input.focus();
  });
}

function showImage(src, cls = '') {
  openModal(h('img', { src, alt: '', class: cls }), 'image');
}
