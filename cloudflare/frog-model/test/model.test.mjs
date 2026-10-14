import assert from 'node:assert/strict'
import { test, beforeEach, afterEach } from 'node:test'
import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { DatabaseSync } from 'node:sqlite'
import { createWorker } from '../worker.js'
import { wasmPlanner } from '../model.js'

const wasm = readFileSync(new URL('../../../rust/frog-model/target/wasm32-unknown-unknown/release/frog_model.wasm', import.meta.url))
const plan = wasmPlanner(new WebAssembly.Instance(new WebAssembly.Module(wasm), {}))
const worker = createWorker(plan)
const token = 'local-test-token-never-deployed-123456789'
const secondToken = 'local-second-token-never-deployed-12345'
const viewerToken = 'local-viewer-token-never-deployed-123456'
const hash = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))].map(v => v.toString(16).padStart(2,'0')).join('')
let raw, env
beforeEach(async () => {
  raw = new DatabaseSync(':memory:'); raw.exec('PRAGMA foreign_keys=ON')
  raw.exec(readFileSync(new URL('../../../rust/frog-model/sql/0001_model.sql', import.meta.url), 'utf8'))
  raw.exec("INSERT INTO frog_model_workspaces VALUES('alpha','Alpha'),('beta','Beta')")
  const db = { prepare(sql) {
    const prepared = raw.prepare(sql)
    return { sql, params: [], bind(...params) { this.params = params; return this }, async first() { return prepared.get(...this.params) || null } }
  }, async batch(statements) {
    raw.exec('BEGIN IMMEDIATE')
    try {
      const results = statements.map(item => {
        const prepared = raw.prepare(item.sql)
        return { success: true, results: prepared.columns().length ? prepared.all(...item.params) : (prepared.run(...item.params), []) }
      })
      raw.exec('COMMIT'); return results
    } catch (error) { raw.exec('ROLLBACK'); throw error }
  } }
  env = { FROG_CLIENTS: JSON.stringify([
    { actor: 'alice', token_sha256: await hash(token), workspaces: { alpha: 'writer' } },
    { actor: 'bob', token_sha256: await hash(secondToken), workspaces: { alpha: 'writer' } },
    { actor: 'viewer', token_sha256: await hash(viewerToken), workspaces: { alpha: 'viewer', beta: 'writer' } },
  ]), DB: { withSession(constraint) { assert.equal(constraint, 'first-primary'); return db } } }
})
afterEach(() => raw.close())
function call(resource, body, credential = token, workspace = 'alpha', headers = {}) {
  return worker.fetch(new Request(`https://frog.example/v1/workspaces/${workspace}/${resource}`, {
    method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${credential}`, 'Content-Type': 'application/json', ...headers },
    body: body ? JSON.stringify(body) : undefined,
  }), env)
}
const mutate = (operation_id, command, credential, workspace) => call('operations', { operation_id, command }, credential, workspace)
async function seed(credential=token,workspace='alpha') {
  assert.equal((await mutate('repo', { kind:'register_repo', repo_id:'frog', title:'Frog' },credential,workspace)).status,200)
  assert.equal((await mutate('create', { kind:'create_task', repo_id:'frog', task_id:'one', title:'One task',priority:0 },credential,workspace)).status,200)
}

test('actual Rust WASM and native planner produce identical plans', () => {
  const request = { workspace_id:'alpha',actor:'alice',operation_id:'repo',command:{kind:'register_repo',repo_id:'frog',title:"Quotes ' are bound"} }
  const native=spawnSync(new URL('../../../rust/frog-model/target/debug/frog-model-plan',import.meta.url).pathname,[],{input:JSON.stringify(request),encoding:'utf8'})
  assert.equal(native.status,0); assert.deepEqual(plan(request),JSON.parse(native.stdout))
})
test('workspace membership and read/write roles fail closed', async () => {
  assert.equal((await call('board',null,'wrong')).status,401)
  assert.equal((await call('board',null,token,'beta')).status,404)
  assert.equal((await mutate('repo',{kind:'register_repo',repo_id:'frog',title:'Frog'},viewerToken)).status,403)
  assert.equal((await call('board',null,token,'alpha',{Origin:'https://evil.example'})).status,403)
  assert.equal((await worker.fetch(new Request('https://frog.example/v1/workspaces/alpha/board'),{})).status,401)
})
test('same task/repo IDs across workspaces remain independent; multiple repos are supported', async () => {
  await seed(); await seed(viewerToken,'beta')
  await mutate('second-repo',{kind:'register_repo',repo_id:'nostoi',title:'Nostoi'})
  await mutate('second-task',{kind:'create_task',repo_id:'nostoi',task_id:'two',title:'Second task',priority:1})
  const board=await (await call('board?repo=nostoi')).json()
  assert.equal(board.items.length,1);assert.equal(board.items[0].id,'two')
  const claim=await mutate('claim',{kind:'claim_task',task_id:'one',expected_revision:0})
  assert.equal(claim.status,200)
  const beta=await (await call('board',null,viewerToken,'beta')).json()
  assert.equal(beta.items[0].status,'todo')
})
test('competing claims, idempotent retries and changed idempotency payloads', async () => {
  await seed()
  const command={kind:'claim_task',task_id:'one',expected_revision:0}
  const responses=await Promise.all([mutate('claim-a',command),mutate('claim-b',command)])
  assert.deepEqual(responses.map(response=>response.status).sort(),[200,409])
  assert.equal((await mutate('claim-a',command)).status,200)
  assert.equal((await mutate('claim-a',{...command,expected_revision:1})).status,409)
  const events=await (await call('events')).json()
  assert.equal(events.items.filter(event=>event.kind==='task.claimed').length,1)
})
test('no caller SQL, actor or workspace injection; bounded inputs', async () => {
  for (const body of [ { operation_id:'x',command:{kind:'repositories',limit:1} },
    { operation_id:'x',command:{kind:'register_repo',repo_id:'frog',title:'Frog'},actor:'admin' },
    { operation_id:'x',command:{kind:'register_repo',repo_id:'frog',title:'Frog',sql:'DROP TABLE tasks'} },
  ]) assert.equal((await call('operations',body)).status,400)
  assert.equal((await call('operations',{operation_id:'big',command:{kind:'register_repo',repo_id:'frog',title:'x'.repeat(17000)}})).status,413)
  assert.equal((await call('board?limit=1000')).status,400)
})
test('D1 transaction failure rolls back receipts, task updates and events', async () => {
  await seed();raw.exec("UPDATE frog_model_tasks SET revision=4294967295")
  assert.equal((await mutate('overflow',{kind:'claim_task',task_id:'one',expected_revision:4294967295})).status,503)
  assert.equal(raw.prepare("SELECT COUNT(*) AS n FROM frog_model_operations WHERE operation_id='overflow'").get().n,0)
  assert.equal(raw.prepare("SELECT status FROM frog_model_tasks").get().status,'todo')
})
test('review and published are not done; advancing requires matching owner/revision', async () => {
  await seed();await mutate('claim',{kind:'claim_task',task_id:'one',expected_revision:0})
  assert.equal((await mutate('skip',{kind:'transition_task',task_id:'one',expected_revision:1,status:'done'})).status,409)
  for (const [revision,status] of [[1,'review'],[2,'published'],[3,'done']]) {
    assert.equal((await mutate(status,{kind:'transition_task',task_id:'one',expected_revision:revision,status})).status,200)
    const board=await (await call('board')).json();assert.equal(board.items[0].lane,status==='done'?'done':'in_progress')
  }
})

test('dependencies arrive as real lists, gate claims and unblock when met', async () => {
  await seed()
  await mutate('two',{kind:'create_task',repo_id:'frog',task_id:'two',title:'Two',priority:2})
  assert.equal((await mutate('ghost',{kind:'add_dependency',task_id:'one',depends_on:'absent'})).status,409)
  assert.equal((await mutate('link',{kind:'add_dependency',task_id:'one',depends_on:'two'})).status,200)
  const board=await (await call('board')).json()
  const one=board.items.find(task=>task.id==='one')
  assert.deepEqual(one.unmet_dependencies,['two']);assert.deepEqual(one.dependencies,['two']);assert.equal(one.lane,'blocked')
  assert.deepEqual(board.items.find(task=>task.id==='two').blocks,['one'])
  assert.equal((await mutate('blocked',{kind:'claim_task',task_id:'one',expected_revision:0})).status,409)
  // Removing the edge twice is applied both times but records one change only.
  assert.equal((await mutate('unlink',{kind:'remove_dependency',task_id:'one',depends_on:'two'})).status,200)
  assert.equal((await mutate('unlink-again',{kind:'remove_dependency',task_id:'one',depends_on:'two'})).status,200)
  const events=await (await call('events')).json()
  assert.equal(events.items.filter(event=>event.kind==='task.dependency_removed').length,1)
  assert.equal((await mutate('claim-now',{kind:'claim_task',task_id:'one',expected_revision:0})).status,200)
})

test('cancellation, single-task reads and board paging', async () => {
  await seed()
  assert.equal((await mutate('claim',{kind:'claim_task',task_id:'one',expected_revision:0})).status,200)
  assert.equal((await mutate('foreign',{kind:'cancel_task',task_id:'one',expected_revision:1},secondToken)).status,409)
  assert.equal((await mutate('cancel',{kind:'cancel_task',task_id:'one',expected_revision:1})).status,200)
  const task=await (await call('tasks?task=one')).json()
  assert.equal(task.items[0].status,'cancelled');assert.deepEqual(task.items[0].dependencies,[])
  await mutate('b',{kind:'create_task',repo_id:'frog',task_id:'b',title:'B',priority:1})
  const page=await (await call('board?limit=1')).json()
  assert.equal(page.items.length,1)
  const next=await (await call(`board?limit=1&after=${encodeURIComponent(`${page.items[0].priority}:${page.items[0].id}`)}`)).json()
  assert.equal(next.items.length,1);assert.notEqual(next.items[0].id,page.items[0].id)
  assert.equal((await call('board?limit=1&after=nonsense')).status,400)
  assert.equal((await call('tasks')).status,400)
})
