const identifier = /^[A-Za-z0-9._:-]{1,120}$/
const reply = (status, body) => Response.json(body, { status, headers: {
  'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
  'Cross-Origin-Resource-Policy': 'same-origin', 'Referrer-Policy': 'no-referrer',
} })

async function principal(request, env) {
  const authorization = request.headers.get('Authorization') || ''
  if (!authorization.startsWith('Bearer ') || authorization.length < 39 || authorization.length > 4103) return null
  let clients
  if (typeof env.FROG_CLIENTS !== 'string' || env.FROG_CLIENTS.length > 65536) return null
  try { clients = JSON.parse(env.FROG_CLIENTS) } catch { return null }
  if (!Array.isArray(clients) || clients.length > 100) return null
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(authorization.slice(7))))].map(value => value.toString(16).padStart(2, '0')).join('')
  for (const client of clients) {
    if (!client || typeof client.actor !== 'string' || !identifier.test(client.actor) || !/^[a-f0-9]{64}$/.test(client.token_sha256 || '') || !client.workspaces || typeof client.workspaces !== 'object' || Array.isArray(client.workspaces)) continue
    let difference = 0
    for (let index = 0; index < 64; index++) difference |= digest.charCodeAt(index) ^ client.token_sha256.charCodeAt(index)
    if (difference === 0) return client
  }
  return null
}

async function boundedJson(request) {
  if (!(request.headers.get('Content-Type') || '').toLowerCase().startsWith('application/json')) throw Error('invalid_json')
  if (!request.body) throw Error('invalid_json')
  const reader = request.body.getReader(), parts = []; let total = 0
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      total += value.length
      if (total > 16384) throw Error('request_too_large')
      parts.push(value)
    }
    const bytes = new Uint8Array(total); let offset = 0
    for (const part of parts) { bytes.set(part, offset); offset += part.length }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

export function createWorker(planOperation) {
  return { async fetch(request, env) {
    const url = new URL(request.url)
    const origin = request.headers.get('Origin')
    if (origin && origin !== url.origin) return reply(403, { error: 'foreign_origin' })
    const client = await principal(request, env)
    if (!client) return reply(401, { error: 'authentication_required' })
    const route = /^\/v1\/workspaces\/([A-Za-z0-9._:-]{1,120})\/(board|repositories|events|operations)$/.exec(url.pathname)
    if (!route) return reply(404, { error: 'not_found' })
    const [, workspace, resource] = route
    const role = Object.hasOwn(client.workspaces, workspace) ? client.workspaces[workspace] : null
    if (!['viewer', 'writer'].includes(role)) return reply(404, { error: 'not_found' })
    let input
    if (resource === 'operations') {
      if (request.method !== 'POST') return reply(405, { error: 'method_not_allowed' })
      if (role !== 'writer') return reply(403, { error: 'write_not_allowed' })
      try {
        const body = await boundedJson(request)
        if (!body || Array.isArray(body) || typeof body !== 'object' || Object.keys(body).some(key => !['operation_id','command'].includes(key))) throw Error('invalid_request')
        if (!['register_repo','create_task','claim_task','transition_task'].includes(body.command?.kind)) throw Error('invalid_request')
        input = { workspace_id: workspace, actor: client.actor, operation_id: body.operation_id, command: body.command }
      } catch (error) { return reply(error.message === 'request_too_large' ? 413 : 400, { error: 'invalid_request' }) }
    } else {
      if (request.method !== 'GET') return reply(405, { error: 'method_not_allowed' })
      const limit = Number(url.searchParams.get('limit') || '100')
      const command = resource === 'board' ? { kind: 'board', repo_id: url.searchParams.get('repo'), limit }
        : resource === 'events' ? { kind: 'events', after: url.searchParams.get('after') || '0', limit }
          : { kind: 'repositories', limit }
      input = { workspace_id: workspace, actor: client.actor, operation_id: 'query', command }
    }
    let plan
    try { plan = planOperation(input) } catch { return reply(400, { error: 'invalid_request' }) }
    if (!env.DB || typeof env.DB.withSession !== 'function') return reply(503, { error: 'storage_unavailable' })
    try {
      const db = env.DB.withSession('first-primary')
      const schema = await db.prepare('SELECT version FROM frog_model_schema').first()
      if (schema?.version !== 1) return reply(503, { error: 'unsupported_schema' })
      const statements = plan.statements.map(statement => db.prepare(statement.sql).bind(...statement.params))
      // D1 batch is a transaction, including the operation receipt and event.
      const results = await db.batch(statements)
      if (results.some(result => result.success === false)) throw Error('batch_failed')
      const items = results.at(-1)?.results || []
      const conflict = plan.result_kind === 'mutation' && items[0]?.status !== 'applied'
      return reply(conflict ? 409 : 200, { schema: 'frog.model-result.v1', workspace_id: workspace, kind: plan.result_kind, items })
    } catch { return reply(503, { error: 'storage_unavailable' }) }
  } }
}
