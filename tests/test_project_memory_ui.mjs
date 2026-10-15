/* Exercise the production memory UI functions with controlled asynchronous API responses. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../app/ui/app.js', import.meta.url), 'utf8');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: '', textContent: '', hidden: false,
    classList: {
      toggle(name, hidden) { if (name === 'hidden') elements.get(id).hidden = hidden; },
    },
    insertAdjacentHTML(_position, html) { this.innerHTML = html + this.innerHTML; },
  });
  return elements.get(id);
}
const task = { id: 'task', workdir: '/workspace' };
const context = vm.createContext({
  S: { state: { tasks: [task], task_latest: { task: { id: 'run', status: 'running' } } }, detailTaskKey: 'task' },
  $: element, t: value => value, esc: value => String(value ?? ''), jsq: value => String(value ?? ''),
});
context.window = context;
vm.runInContext(source.slice(source.indexOf('function currentProjectMemoryTaskId('),
  source.indexOf('window.decideProjectMemory =')), context);
vm.runInContext(source.slice(source.indexOf('let MEMORY_CENTER ='),
  source.indexOf('function memoryStatusLabel(')) +
  '\nfunction renderMemoryCenter() { globalThis.centerData = MEMORY_CENTER.data; }', context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const row = content => ({ id: content, content, version: 1, status: 'pending', verified: true });
let requests = 0;
context.api = async () => { requests++; return { entries: [row('VISIBLE-MEMORY')] }; };
context.renderProjectMemoryControls('task');
await settle();
assert.match(element('rd-project-memory-body').innerHTML, /VISIBLE-MEMORY/);
context.renderProjectMemoryControls('task');
assert.equal(requests, 1, 'unchanged details must use cached memory');
context.S.state.task_latest.task.status = 'done';
context.renderProjectMemoryControls('task');
await settle();
assert.equal(requests, 2, 'completion must refresh an initially empty or stale panel');
context.S.state.task_latest.task.project_memory_id = 'persisted-after-done';
context.renderProjectMemoryControls('task');
await settle();
assert.equal(requests, 3, 'persistence after the done event must refresh again');
context.S.state.tasks = [];
context.S.state.archived_tasks = [task];
context.renderProjectMemoryControls('task');
assert.equal(element('rd-project-memory').hidden, false, 'archived task memory stays accessible');
task.workdir = '/new-workspace';
context.renderProjectMemoryControls('task');
await settle();
assert.equal(requests, 4, 'workspace changes invalidate the detail cache');
context.S.state.tasks.push({ id: 'neighbor', workdir: task.workdir });
context.renderProjectMemoryControls('task');
await settle();
const beforeNeighbor = requests;
context.S.state.task_latest.neighbor = { id: 'neighbor-run', status: 'done', project_memory_id: 'neighbor-memory' };
context.renderProjectMemoryControls('task');
await settle();
assert.equal(requests, beforeNeighbor + 1, 'another task saving memory in this workspace refreshes the panel');
context.S.detailTaskKey = null;
context.S.lastRunTask = task;
const beforeManual = requests;
await context.loadProjectMemory();
assert.equal(requests, beforeManual + 1, 'archived run manual refresh resolves its task');
context.api = async url => {
  assert.match(url, /tasks\/task\/memory\/preview/);
  return { text: 'ARCHIVED-PREVIEW', entries: [], budget: {} };
};
await context.previewProjectMemory();
assert.match(element('rd-project-memory-body').innerHTML, /ARCHIVED-PREVIEW/);
context.S.detailTaskKey = 'task';

context.api = async () => { throw new Error('temporary'); };
await context.loadProjectMemory('task');
context.api = async () => { requests++; return { entries: [row('RECOVERED')] }; };
context.renderProjectMemoryControls('task');
await settle();
assert.match(element('rd-project-memory-body').innerHTML, /RECOVERED/, 'failed reads are retryable');

let pending = [];
context.api = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));
const oldDetail = context.loadProjectMemory('task');
const newDetail = context.loadProjectMemory('task');
pending[1].resolve({ entries: [row('LATEST-DETAIL')] });
await newDetail;
pending[0].reject(new Error('STALE-FAILURE'));
await oldDetail;
assert.match(element('rd-project-memory-body').innerHTML, /LATEST-DETAIL/);
assert.doesNotMatch(element('rd-project-memory-body').innerHTML, /STALE-FAILURE/);

pending = [];
const oldCenter = context.loadMemoryCenter();
const newCenter = context.loadMemoryCenter();
pending[1].resolve({ projects: [{ name: 'LATEST-CENTER' }] });
await newCenter;
pending[0].resolve({ projects: [] });
await oldCenter;
assert.equal(context.centerData.projects[0].name, 'LATEST-CENTER', 'late empty response cannot erase fresh results');

let centerRequests = 0;
context.api = async () => { centerRequests++; return { projects: [] }; };
context.S.tab = 'memory';
context.memoryCenterMaybeRefresh();
assert.equal(centerRequests, 0, 'unchanged state does not reload the center');
context.S.state.task_latest.task.project_memory_id = 'new-memory';
context.memoryCenterMaybeRefresh();
await settle();
assert.equal(centerRequests, 1, 'new persisted memory refreshes an open memory center');
context.memoryCenterMaybeRefresh();
assert.equal(centerRequests, 1);
console.log('PASS: completion refresh, persistence refresh, archive visibility, workspace changes, retries, stale responses and live center refresh');
