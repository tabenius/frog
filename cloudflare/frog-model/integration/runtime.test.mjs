import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { Miniflare, convertV4MiniflareOptions } from 'miniflare'

test('real workerd/D1: Rust WASM, scoped auth, atomic batches and competing claims', async () => {
  const tokens = { alice: 'runtime-alice-token-not-deployed-123456789', bob: 'runtime-bob-token-not-deployed-12345678900', beta: 'runtime-beta-token-not-deployed-1234567890' }
  const hash = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(value=>value.toString(16).padStart(2,'0')).join('')
  const clients = await Promise.all(Object.entries(tokens).map(async ([actor,token])=>({actor,token_sha256:await hash(token),workspaces:{[actor==='beta'?'beta':'alpha']:'writer'}})))
  const build=fileURLToPath(new URL('../build/worker/',import.meta.url))
  const modules=[{type:'ESModule',path:build+'index.js'},...readdirSync(build).filter(name=>name.endsWith('.wasm')).map(name=>({type:'CompiledWasm',path:build+name}))]
  const runtime = new Miniflare(convertV4MiniflareOptions({ port:0, workers:[{ name:'frog-model', modules, modulesRoot:build,
    compatibilityDate:'2026-09-15',
    d1Databases:{DB:'frog-model-runtime-test'}, bindings:{FROG_CLIENTS:JSON.stringify(clients)} }] }))
  try {
    const db = await runtime.getD1Database('DB')
    const sql=readFileSync(new URL('../migrations/0001_model.sql',import.meta.url),'utf8')
    await db.batch(sql.split(';').filter(value=>value.trim()).map(value=>db.prepare(value)))
    await db.prepare("INSERT INTO frog_model_workspaces VALUES('alpha','Alpha'),('beta','Beta')").run()
    async function call(workspace,actor,operation,command){
      return runtime.dispatchFetch(`https://frog.example/v1/workspaces/${workspace}/${command?'operations':'board'}`,{
        method:command?'POST':'GET',headers:{Authorization:`Bearer ${tokens[actor]}`,'Content-Type':'application/json'},
        body:command?JSON.stringify({operation_id:operation,command}):undefined})
    }
    for(const [workspace,actor] of [['alpha','alice'],['beta','beta']]){
      assert.equal((await call(workspace,actor,'repo',{kind:'register_repo',repo_id:'frog',title:'Frog'})).status,200)
      assert.equal((await call(workspace,actor,'task',{kind:'create_task',repo_id:'frog',task_id:'one',title:'One task',priority:0})).status,200)
    }
    assert.equal((await call('beta','alice')).status,404)
    const command={kind:'claim_task',task_id:'one',expected_revision:0}
    const claims=await Promise.all([call('alpha','alice','alice-claim',command),call('alpha','bob','bob-claim',command)])
    assert.deepEqual(claims.map(value=>value.status).sort(),[200,409])
    const winner=claims[0].status===200?'alice':'bob'
    assert.equal((await call('alpha',winner,`${winner}-claim`,command)).status,200)
    assert.equal((await call('alpha',winner,`${winner}-claim`,{...command,expected_revision:1})).status,409)
    const alpha=await (await call('alpha','alice')).json(), beta=await (await call('beta','beta')).json()
    assert.equal(alpha.items[0].assignee,winner);assert.equal(beta.items[0].status,'todo')
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM frog_model_events WHERE workspace_id='alpha' AND kind='task.claimed'").first()).n,1)
    await db.prepare("UPDATE frog_model_tasks SET revision=4294967295 WHERE workspace_id='beta'").run()
    assert.equal((await call('beta','beta','overflow',{kind:'claim_task',task_id:'one',expected_revision:4294967295})).status,503)
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM frog_model_operations WHERE workspace_id='beta' AND operation_id='overflow'").first()).n,0)
  } finally { await runtime.dispose() }
})
