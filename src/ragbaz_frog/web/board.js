'use strict';
// Shared read-only renderer: local Frog, authenticated gateway, or a private snapshot.
const SCHEMA = 'frog.kanban-projection.v0';
const params = new URLSearchParams(location.search);
const source = params.get('source') || 'api/v1/kanban';
const board = document.getElementById('board');
const freshness = document.getElementById('freshness');
const search = document.getElementById('search');
const repository = document.getElementById('repository');
const closed = document.getElementById('closed');
const assignee = document.getElementById('assignee');
const laneFilter = document.getElementById('lane');
const priority = document.getElementById('priority');
const sort = document.getElementById('sort');
const theme = document.getElementById('theme');
const ready = document.getElementById('ready');
const inspector = document.getElementById('inspector');
const navStyle = document.getElementById('nav-style');
const autoHide = document.getElementById('auto-hide');
const topbar = document.getElementById('topbar');
const storyMode = document.getElementById('story-mode');
const startView = document.getElementById('start-view');
const slides = document.getElementById('repo-slides');
const stories = new Map();
let currentView = 'start', slideIndex = 0;
let lastStartSignature = '', lastBoardSignature = '';
const viewInputs = [search, repository, assignee, laneFilter, priority, sort, theme, closed, ready, navStyle, autoHide, storyMode];
let selectedTask = null;
let inspectedCards = '';
let snapshot = null, request = null;
let credential = '';
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function timestamp(value) {
  if (!value) return 'unknown';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'unknown' : date.toISOString().slice(0, 19).replace('T', ' ') + ' UTC';
}
function saveView() {
  const view = Object.fromEntries(viewInputs.map(input => [input.id, input.type === 'checkbox' ? input.checked : input.value]));
  try { localStorage.setItem('frog.board.view.v0', JSON.stringify(view)); } catch { /* Local-only view remains usable. */ }
}
let savedView = {};
try { savedView = JSON.parse(localStorage.getItem('frog.board.view.v0') || '{}'); } catch { /* Ignore malformed local preferences. */ }
if (savedView && typeof savedView === 'object') for (const input of viewInputs) {
  if (input.type === 'checkbox' && typeof savedView[input.id] === 'boolean') input.checked = savedView[input.id];
  else if (typeof savedView[input.id] === 'string' && (input === search || [...input.options].some(option => option.value === savedView[input.id]))) input.value = savedView[input.id];
}
function applyTheme() {
  if (theme.value === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme.value;
  topbar.dataset.buttons = navStyle.value;
  if (!autoHide.checked) topbar.classList.remove('auto-hidden');
}
applyTheme();
function taskButton(id) {
  const card = snapshot.cards.find(card => card.id === id);
  const button = element('button', card ? `Open ${card.title} → · ${card.status}` : `${id} · missing task`, 'dependency');
  button.type = 'button'; button.dataset.task = id;
  button.addEventListener('click', () => focusTask(id));
  return button;
}
function inspect(id) {
  currentView = 'task';
  selectedTask = id;
  inspectedCards = JSON.stringify(snapshot.cards);
  const card = snapshot.cards.find(card => card.id === id);
  startView.hidden = true;
  const crumb = document.getElementById('breadcrumb');
  crumb.replaceChildren(element('span', 'Board'), element('span', ' / ', 'crumb-separator'), element('span', card ? card.title : id));
  crumb.lastChild.setAttribute('aria-current', 'page');
  board.hidden = true; inspector.hidden = false;
  for (const node of [document.querySelector('.toolbar'), document.getElementById('count'), document.getElementById('board-hint')]) node.hidden = true;
  const back = element('button', '← Back to board', 'back-button'); back.type = 'button';
  back.addEventListener('click', showBoard);
  inspector.replaceChildren(back, element('p', 'Task details · dependency links open another task · Escape returns to your board', 'meta'), element('h2', card ? card.title : 'Missing dependency'));
  if (!card) { inspector.append(element('p', `No Frog task record for ${id}.`)); return; }
  inspector.append(element('p', card.id, 'task-id'), element('span', card.priority, `priority ${card.priority}`), element('p', `${card.status} · ${card.repository || 'workspace'} · ${card.assignee || 'unassigned'}`));
  inspector.append(element('p', `Status observed: ${timestamp(card.status_observed_at)}`), element('p', `Updated: ${timestamp(card.updated_at)}`));
  for (const [label, ids] of [['Unmet dependencies', card.unmet_dependencies], ['All direct dependencies', card.dependencies || []], ['Blocks these tasks', card.blocks || []]]) {
    inspector.append(element('h3', label));
    if (!ids.length) inspector.append(element('p', 'None recorded', 'empty'));
    else for (const dependency of ids) inspector.append(taskButton(dependency));
  }
  const graph = element('details', undefined, 'dependency-tree');
  graph.append(element('summary', 'Expand dependency tree'));
  let visited = 0;
  function tree(taskId, ancestors) {
    const node = element('li');
    node.append(taskButton(taskId));
    if (ancestors.has(taskId)) { node.append(element('span', ' · dependency cycle', 'stale')); return node; }
    if (++visited > 100) { node.append(element('span', ' · tree limit reached', 'empty')); return node; }
    const task = snapshot.cards.find(item => item.id === taskId);
    if (task?.dependencies?.length) {
      const next = new Set([...ancestors, taskId]);
      const list = element('ul');
      for (const child of task.dependencies) { if (visited > 100) break; list.append(tree(child, next)); }
      node.append(list);
    }
    return node;
  }
  const treeRoot = element('ul'); treeRoot.append(tree(id, new Set())); graph.append(treeRoot); inspector.append(graph);
}
function focusTask(id) {
  inspect(id); inspector.focus(); window.scrollTo({ top: 0 });
}
function showBoard() {
  const previous = selectedTask; selectedTask = null;
  currentView = 'board'; startView.hidden = true;
  inspector.hidden = true; board.hidden = false;
  const crumb = element('span', 'Board'); crumb.setAttribute('aria-current', 'page');
  document.getElementById('breadcrumb').replaceChildren(crumb);
  for (const node of [document.querySelector('.toolbar'), document.getElementById('count'), document.getElementById('board-hint')]) node.hidden = false;
  render();
  ([...board.querySelectorAll('.open-task')].find(node => node.dataset.task === previous) || board).focus();
}
function showStart() {
  selectedTask = null; currentView = 'start'; startView.hidden = false; inspector.hidden = true; board.hidden = true;
  for (const node of [document.querySelector('.toolbar'), document.getElementById('count'), document.getElementById('board-hint')]) node.hidden = true;
  const crumb = element('span', 'Start'); crumb.setAttribute('aria-current', 'page'); document.getElementById('breadcrumb').replaceChildren(crumb);
  renderStart();
}
async function loadStory(repo) {
  if (stories.has(repo.id)) return;
  stories.set(repo.id, { loading: true });
  try {
    const response = await fetch(`api/v1/repository?id=${encodeURIComponent(repo.id)}`, { cache: 'no-store', signal: AbortSignal.timeout(5000), headers: credential ? { Authorization: `Bearer ${credential}` } : {} });
    if (!response.ok) throw Error('Repository history unavailable');
    const story = await response.json();
    if (story.schema !== 'frog.repository-story.v0' || story.id !== repo.id || typeof story.description !== 'string' || !Array.isArray(story.commits)) throw Error('Invalid repository story');
    stories.set(repo.id, story);
  } catch { stories.set(repo.id, { description: 'Repository history unavailable. You can still explore its Frog tasks.', commits: [], history_status: 'unavailable' }); }
  renderStart();
}
function renderStart() {
  if (!snapshot) return;
  const repos = snapshot.repositories || [...new Set(snapshot.cards.map(card => card.repository).filter(Boolean))].sort().map(name => ({ id: name, title: name, repository: name }));
  slideIndex = Math.max(0, Math.min(slideIndex, repos.length - 1));
  document.getElementById('slide-position').textContent = repos.length ? `${slideIndex + 1} / ${repos.length}` : 'No repositories registered';
  document.getElementById('slide-prev').disabled = slideIndex === 0;
  document.getElementById('slide-next').disabled = slideIndex >= repos.length - 1;
  const signature = JSON.stringify([repos, snapshot.cards, storyMode.value, [...stories]]);
  if (signature === lastStartSignature) { if (currentView === 'start' && repos[slideIndex]) loadStory(repos[slideIndex]); return; }
  lastStartSignature = signature;
  const scrollPosition = slides.scrollLeft;
  slides.replaceChildren(...repos.map(repo => {
    const section = element('article', undefined, 'repo-slide'); section.dataset.repo = repo.id;
    const story = stories.get(repo.id);
    section.append(element('p', 'Repository · read-only live workspace', 'eyebrow'), element('h3', repo.title), element('p', story?.description || 'Loading repository description…', 'repo-description'));
    const tasks = snapshot.cards.filter(card => card.repository === repo.repository);
    const current = tasks.filter(card => card.lane === 'in_progress');
    const next = tasks.filter(card => card.lane === 'idea').sort((a,b) => a.priority.localeCompare(b.priority));
    section.append(element('p', `${tasks.length} tasks · ${current.length} at hand · ${next.length} ready / ideas`, 'meta'));
    const openBoard = element('button', 'Open repository board →'); openBoard.type = 'button';
    openBoard.addEventListener('click', () => { repository.value = repo.repository; showBoard(); }); section.append(openBoard);
    if (storyMode.value === 'commits') {
      section.append(element('h4', 'Recent commits · not inferred task completion'));
      if (!story?.commits?.length) section.append(element('p', story?.loading ? 'Loading Git history…' : 'No Git history available', 'empty'));
      for (const commit of story?.commits || []) section.append(element('p', `${commit.commit} · ${timestamp(commit.committed_at)} · ${commit.title}`, 'commit-entry'));
    } else {
      const items = storyMode.value === 'done' ? tasks.filter(card => card.lane === 'done') : storyMode.value === 'next' ? next : current;
      section.append(element('h4', storyMode.value === 'done' ? 'Completed in Frog' : storyMode.value === 'next' ? 'Next up · dependency-ready / ideas' : 'Tasks at hand · including review'));
      if (!items.length) section.append(element('p', 'No matching tasks recorded. Explore the repository board for blockers and other work.', 'empty'));
      for (const task of items.slice(0, 8)) section.append(taskButton(task.id));
      if (items.length > 8) section.append(element('p', `${items.length - 8} more on the repository board`, 'meta'));
    }
    section.append(element('h4', 'Continue exploring'), ...current.slice(0,1).map(task => taskButton(task.id)), ...next.slice(0,1).map(task => taskButton(task.id)));
    return section;
  }));
  slides.scrollLeft = scrollPosition;
  if (currentView === 'start' && repos[slideIndex]) loadStory(repos[slideIndex]);
}
function render() {
  if (!snapshot) return;
  const signature = JSON.stringify([snapshot.cards, snapshot.lanes, viewInputs.map(input => input.type === 'checkbox' ? input.checked : input.value)]);
  if (signature === lastBoardSignature) return;
  lastBoardSignature = signature;
  const query = search.value.trim().toLowerCase();
  const focused = document.activeElement?.dataset.task;
  const cards = snapshot.cards.filter(card => (closed.checked || card.lane !== 'done') && (!repository.value || card.repository === repository.value) && (!assignee.value || (card.assignee || '(unassigned)') === assignee.value) && (!laneFilter.value || card.lane === laneFilter.value) && (!priority.value || card.priority === priority.value) && (!ready.checked || card.ready) && [card.id, card.title, card.repository, card.assignee, ...(card.tags || [])].join(' ').toLowerCase().includes(query));
  const ranks = { p0: 0, critical: 0, urgent: 0, p1: 1, high: 1, p2: 2, normal: 2, medium: 2, p3: 3, low: 3 };
  cards.sort((a, b) => (sort.value === 'title' ? a.title.localeCompare(b.title) : sort.value === 'updated' ? String(b.updated_at).localeCompare(String(a.updated_at)) : sort.value === 'created' ? String(b.created_at).localeCompare(String(a.created_at)) : (ranks[a.priority] ?? 4) - (ranks[b.priority] ?? 4)) || a.id.localeCompare(b.id));
  document.getElementById('count').textContent = `${cards.length} shown / ${snapshot.cards.length} recorded tasks · priorities: P0 critical, P1 high, P2 normal, P3 low`;
  const nodes = snapshot.lanes.map(lane => {
    const section = element('section', undefined, 'lane');
    section.dataset.lane = lane.id;
    const items = cards.filter(card => card.lane === lane.id);
    section.append(element('h2', `${lane.title} · ${items.length}`));
    if (!items.length) section.append(element('p', lane.id === 'done' && !closed.checked ? 'Closed work hidden' : 'No matching tasks', 'empty'));
    for (const card of items) {
      const details = element('article', undefined, 'card');
      const meta = element('span', undefined, 'meta');
      meta.append(element('span', card.priority || 'unranked', `priority ${card.priority}`), element('span', ` · ${card.repository || 'workspace'}`));
      const open = element('button', undefined, 'open-task'); open.type = 'button'; open.dataset.task = card.id;
      open.append(element('strong', card.title), element('span', 'Open task →', 'open-label'));
      open.addEventListener('click', () => focusTask(card.id));
      details.append(meta, open, element('span', card.status, 'status'));
      details.append(element('p', `Assigned: ${card.assignee || 'unassigned'}${card.ready ? ' · dependency-ready' : ''}`));
      if (card.unmet_dependencies.length) {
        const unmet = element('div', undefined, 'links'); unmet.append(element('p', 'Blocked by · open blocker:'));
        for (const id of card.unmet_dependencies) unmet.append(taskButton(id));
        details.append(unmet);
      }
      if (card.tags?.length) details.append(element('p', card.tags.join(' · '), 'tags'));
      section.append(details);
    }
    return section;
  });
  board.replaceChildren(...nodes);
  if (selectedTask && inspectedCards !== JSON.stringify(snapshot.cards)) inspect(selectedTask);
  if (focused && !selectedTask) [...board.querySelectorAll('.open-task')].find(node => node.dataset.task === focused)?.focus();
}
function validate(data) {
  if (!data || data.schema !== SCHEMA || data.source !== 'frog' || data.read_only !== true || !Array.isArray(data.cards) || !Array.isArray(data.lanes) || data.lanes.length !== 4 || new Set(data.lanes.map(l => l.id)).size !== 4 || Number.isNaN(Date.parse(data.observed_at))) throw Error('Unsupported or invalid Frog projection');
  const lanes = new Set(['idea', 'blocked', 'in_progress', 'done']);
  for (const lane of data.lanes) if (!lane || !lanes.has(lane.id) || typeof lane.title !== 'string') throw Error('Invalid lane');
  const ids = new Set();
  for (const card of data.cards) {
    if (!card || typeof card.id !== 'string' || ids.has(card.id) || typeof card.title !== 'string' || !lanes.has(card.lane) || !Array.isArray(card.unmet_dependencies) || card.unmet_dependencies.some(id => typeof id !== 'string')) throw Error('Invalid task card');
    for (const key of ['dependencies', 'blocks', 'tags']) if (card[key] !== undefined && (!Array.isArray(card[key]) || card[key].some(value => typeof value !== 'string'))) throw Error('Invalid task links');
    ids.add(card.id);
  }
  return data;
}
function showFreshness(error = null) {
  const age = snapshot ? (Date.now() - Date.parse(snapshot.observed_at)) / 1000 : null;
  const stale = error || age === null || age > 30 || age < -5;
  freshness.className = stale ? 'stale' : 'fresh';
  freshness.textContent = `${error ? 'Unavailable: ' + error + '. ' : ''}${snapshot ? `Source captured ${timestamp(snapshot.observed_at)} · revision ${snapshot.revision} · ${stale ? 'STALE / last known' : 'current capture'}` : 'No verified projection loaded.'}`;
}
let lastError = null;
let streamController = null;
let reconnectDelay = 1000;
function clearPrivateProjection() {
  snapshot = null; selectedTask = null; stories.clear();
  lastStartSignature = ''; lastBoardSignature = '';
  board.replaceChildren(); slides.replaceChildren(); inspector.replaceChildren();
  document.getElementById('count').textContent = ''; showStart();
  document.getElementById('unlock').hidden = false;
}
function acceptSnapshot(data) {
  snapshot = validate(data); lastError = null;
  document.getElementById('unlock').hidden = true;
  const selected = repository.value || savedView.repository || '';
  repository.replaceChildren(new Option('All repositories', ''), ...[...new Set(snapshot.cards.map(c => c.repository).filter(Boolean))].sort().map(name => new Option(name, name)));
  repository.value = selected;
  const assigned = assignee.value || savedView.assignee || '';
  assignee.replaceChildren(new Option('All assignees', ''), ...[...new Set(snapshot.cards.map(c => c.assignee || '(unassigned)'))].sort().map(name => new Option(name, name)));
  assignee.value = assigned; savedView = {};
  render(); showFreshness(); renderStart();
}
async function connectStream() {
  if (streamController || document.hidden || params.has('source')) return;
  const controller = new AbortController(); streamController = controller;
  let watchdog;
  try {
    const response = await fetch('api/v1/events', { cache: 'no-store', signal: controller.signal, headers: credential ? { Authorization: `Bearer ${credential}` } : {} });
    if (response.status === 401) clearPrivateProjection();
    if (!response.ok || !response.headers.get('content-type')?.startsWith('text/event-stream')) throw Error(`Live stream HTTP ${response.status}`);
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let pending = '';
    const keepAlive = () => { clearTimeout(watchdog); watchdog = setTimeout(() => controller.abort(), 45000); };
    keepAlive();
    try {
      while (!controller.signal.aborted) {
        const { value, done } = await reader.read();
        if (done) throw Error('Live stream disconnected');
        keepAlive(); pending += decoder.decode(value, { stream: true });
        if (pending.length > 2 * 1024 * 1024) throw Error('Live frame too large');
        let end;
        while ((end = pending.indexOf('\n\n')) !== -1) {
          const frame = pending.slice(0, end); pending = pending.slice(end + 2);
          const event = frame.split('\n').find(line => line.startsWith('event: '))?.slice(7);
          const data = frame.split('\n').find(line => line.startsWith('data: '))?.slice(6);
          if (event === 'projection' && data) { acceptSnapshot(JSON.parse(data)); reconnectDelay = 1000; }
          if (event === 'unavailable') { lastError = 'source unavailable'; showFreshness(lastError); }
        }
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  } catch (error) {
    if (!document.hidden) { lastError = error.name === 'AbortError' ? 'live stream timed out' : error.message; showFreshness(lastError); }
  } finally {
    clearTimeout(watchdog); if (streamController === controller) streamController = null;
    // Reconnection backoff, not periodic board polling.
    if (!document.hidden) { setTimeout(connectStream, reconnectDelay + Math.random() * 500); reconnectDelay = Math.min(reconnectDelay * 2, 30000); }
  }
}
async function refresh() {
  request?.abort();
  const controller = new AbortController(); request = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const url = new URL(source, location.href);
    if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) throw Error('Projection source must be same-origin');
    const response = await fetch(url, { cache: 'no-store', signal: controller.signal, headers: credential ? { Authorization: `Bearer ${credential}` } : {} });
    if (response.status === 401) { streamController?.abort(); clearPrivateProjection(); }
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    const data = validate(await response.json());
    if (request !== controller) return;
    acceptSnapshot(data); connectStream();
  } catch (error) {
    if (request !== controller) return;
    lastError = error.name === 'AbortError' ? 'request timed out' : error.message;
    showFreshness(lastError);
  } finally { clearTimeout(timeout); }
}
document.getElementById('refresh').addEventListener('click', refresh);
document.getElementById('nav-board').addEventListener('click', showBoard);
const startButton = element('button', undefined, 'nav-action'); startButton.type = 'button'; startButton.title = 'Open start view'; startButton.setAttribute('aria-label', 'Open start view');
startButton.append(element('span', '⌂', 'nav-icon'), element('span', 'Start', 'nav-label')); startButton.firstChild.setAttribute('aria-hidden', 'true');
startButton.addEventListener('click', showStart); topbar.querySelector('nav').prepend(startButton);
for (const [id, delta] of [['slide-prev', -1], ['slide-next', 1]]) document.getElementById(id).addEventListener('click', () => { const cards = [...slides.children]; slideIndex = Math.max(0, Math.min(cards.length - 1, slideIndex + delta)); slides.scrollTo({ left: cards[slideIndex]?.offsetLeft - slides.offsetLeft, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); renderStart(); });
let slideTimer;
slides.addEventListener('scroll', () => { clearTimeout(slideTimer); slideTimer = setTimeout(() => { const cards = [...slides.children]; slideIndex = cards.reduce((best, node, index) => Math.abs(node.offsetLeft - slides.offsetLeft - slides.scrollLeft) < Math.abs(cards[best].offsetLeft - slides.offsetLeft - slides.scrollLeft) ? index : best, 0); renderStart(); }, 180); }, { passive: true });
document.getElementById('open-settings').addEventListener('click', () => document.getElementById('settings').showModal());
document.getElementById('close-settings').addEventListener('click', () => document.getElementById('settings').close());
let previousScroll = window.scrollY;
window.addEventListener('scroll', () => {
  const current = Math.max(0, window.scrollY);
  if (Math.abs(current - previousScroll) < 8 && current > 60) return;
  const hide = autoHide.checked && current > previousScroll && current > 100 && !topbar.contains(document.activeElement) && !document.getElementById('settings').open;
  topbar.classList.toggle('auto-hidden', hide); previousScroll = current;
}, { passive: true });
topbar.addEventListener('focusin', () => topbar.classList.remove('auto-hidden'));
document.getElementById('unlock-button').addEventListener('click', () => {
  streamController?.abort(); streamController = null;
  credential = document.getElementById('token').value;
  document.getElementById('token').value = '';
  refresh();
});
for (const input of viewInputs) input.addEventListener('input', () => { applyTheme(); saveView(); render(); if (input === storyMode) renderStart(); });
document.getElementById('reset-view').addEventListener('click', () => { search.value = ''; repository.value = ''; assignee.value = ''; laneFilter.value = ''; priority.value = ''; ready.checked = false; closed.checked = false; sort.value = 'priority'; theme.value = 'system'; applyTheme(); saveView(); render(); });
document.addEventListener('keydown', event => {
  if (document.getElementById('settings').open) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
  if (event.key === '/') { event.preventDefault(); search.focus(); }
  if (event.key.toLowerCase() === 'r') { event.preventDefault(); refresh(); }
  if (event.key === 'Escape' && selectedTask) { event.preventDefault(); showBoard(); }
  if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    const current = document.activeElement;
    const column = current?.closest('.lane'); if (!column) return;
    let target;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { const summaries = [...column.querySelectorAll('.open-task')]; target = summaries[summaries.indexOf(current) + (event.key === 'ArrowDown' ? 1 : -1)]; }
    else { const columns = [...board.querySelectorAll('.lane')]; target = columns[columns.indexOf(column) + (event.key === 'ArrowRight' ? 1 : -1)]?.querySelector('.open-task'); }
    if (target) { event.preventDefault(); target.focus(); }
  }
});
document.addEventListener('visibilitychange', () => { if (document.hidden) { streamController?.abort(); } else { refresh(); } });
window.addEventListener('pagehide', () => { streamController?.abort(); request?.abort(); });
showStart(); refresh();
