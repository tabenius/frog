"""Model-first presentation adapter shared by CLI, TUI and MCP.

No SQL, filesystem state access or automatic legacy fallback. Unsupported
legacy capabilities are explicit errors until the server contract grows.
"""
import json
import os
import uuid
from pathlib import Path
from .model_client import ModelClient, ModelError, read_token

def settings(*, config_path=None, enabled=None, url=None, workspace=None, token_file=None, legacy_db=False):
    from . import config
    data = {}
    sidecar = Path.home() / '.frog/model-server/config.json'
    if sidecar.exists():
        data.update(json.loads(sidecar.read_text()))
    path = config.config_path(config_path)
    if path.exists():
        data.update(config._load_config_file(path).get('model_client', {}))
    choice = data.get('enabled', data.get('auto_use_model_client', True))
    if 'FROG_MODEL_CLIENT' in os.environ:
        raw = os.environ['FROG_MODEL_CLIENT'].lower()
        if raw not in ('1','true','yes','0','false','no'):
            raise ValueError('FROG_MODEL_CLIENT must be true or false')
        choice = raw in ('1','true','yes')
    if enabled is not None:
        choice = enabled
    elif legacy_db:
        choice = False  # --db is an explicit legacy storage selection.
    if not isinstance(choice, bool):
        raise ValueError('model_client.enabled must be a boolean')
    return {'enabled': choice,
        'url': url or os.environ.get('FROG_MODEL_URL') or data.get('url') or data.get('model_server_url') or 'http://localhost:9091/',
        'workspace': workspace or os.environ.get('FROG_MODEL_WORKSPACE') or data.get('workspace', 'local'),
        'token_file': token_file or os.environ.get('FROG_MODEL_TOKEN_FILE') or (None if os.environ.get('FROG_MODEL_TOKEN') else data.get('token_file')),
        'timeout': data.get('timeout_seconds', 15)}

class Backend:
    def __init__(self, options):
        token = read_token(options['token_file'])
        self.client = ModelClient(options['url'], token=token, timeout=options['timeout'])
        self.workspace = options['workspace']
        self.url = options['url']

    def close(self):
        pass

    def __getattr__(self, name):
        def unsupported(*args, **kwargs):
            raise ModelError('unsupported_operation', f'{name} is not supported by the model API; use --legacy-storage explicitly')
        return unsupported

    @staticmethod
    def task_row(row):
        return dict(row, slug=row['id'], repo_path=row.get('repository'),
            workflow_status=row['status'], priority=f"p{row['priority']}",
            assigned_agent=row.get('assignee'), git_status='unknown',
            unmet_deps=row.get('unmet_dependencies', []))

    def rows(self, repo=None):
        result, after = [], None
        while True:
            rows = self.client.board(self.workspace, repo=repo, limit=200, after=after)
            result.extend(rows)
            if len(rows) < 200:
                return result
            cursor = f"{rows[-1]['priority']}:{rows[-1]['id']}"
            if cursor == after:
                raise ModelError('wrong_schema', 'server pagination did not advance')
            after = cursor

    def board_snapshot(self, conn=None, *, recent_limit=14):
        columns = {lane: [] for lane in ('idea','blocked','in_progress','done')}
        ready = []
        for row in self.rows():
            columns[row['lane']].append(self.task_row(row))
            if row['status'] == 'todo' and not row['unmet_dependencies']:
                ready.append(row['id'])
        events = self.event_rows()
        recent = [dict(e, id=int(e['cursor']), summary=e['kind'], task_slug=e['entity_id'], actor=None)
            for e in events[-recent_limit:]]
        return dict(ok=True,columns=columns,ready=ready,recent=recent,
            counts={k:len(v) for k,v in columns.items()},max_event_id=int(events[-1]['cursor']) if events else 0)

    def event_rows(self):
        result, after = [], '0'
        while True:
            rows = self.client.events(self.workspace,after=after,limit=200)
            result.extend(rows)
            if len(rows) < 200:
                return result
            cursor = str(rows[-1]['cursor'])
            if int(cursor) <= int(after):
                raise ModelError('wrong_schema','event pagination did not advance')
            after = cursor

    def repo_tree_snapshot(self, conn=None):
        rows = self.client.repositories(self.workspace)
        if len(rows) >= 200:
            raise ModelError('unsupported_operation','repository view exceeds current API pagination; refusing a truncated snapshot')
        repos = [dict(repo_path=r['id'], name=r['title'], repo_key=r['id'], units=[], actions=[], status='active', third_party=False)
            for r in rows]
        return dict(ok=True,repos=repos,root='model',tree={'name':'model','path':'model','dirs':[],'repos':repos},boxes=[],local_box=None)

    def task_list(self, conn=None, *, repo_ref=None, workflow_status=None, assigned_agent=None, include_done=False):
        rows = [self.task_row(row) for row in self.rows(repo_ref)]
        if workflow_status:
            rows = [r for r in rows if r['workflow_status'] in workflow_status]
        elif not include_done:
            rows = [r for r in rows if r['workflow_status'] not in ('done','cancelled')]
        if assigned_agent:
            rows = [r for r in rows if r['assigned_agent'] == assigned_agent]
        return dict(ok=True,tasks=rows,repo_path=repo_ref,include_done=include_done)

    def task_info(self, conn=None, slug=None):
        row = self.client.task(self.workspace,slug)
        if row:
            row = dict(row, **self.client.task_details(self.workspace,slug))
        return dict(ok=True,task=self.task_row(row)) if row else dict(ok=False,error='task not found')

    def create(self, args):
        extras = ('why','what','roi_note','assigned_agent','delegation_current','delegation_other','parent_task_slug','file')
        if any(getattr(args,k,None) for k in extras) or args.workflow_status not in ('idea','todo') or args.git_status != 'not_started':
            raise ModelError('unsupported_operation','model create does not support legacy metadata/status overrides; no fields were discarded')
        if not args.repo_ref:
            raise ModelError('invalid_request','model create requires --repo <repository ID>')
        result = self.client.create_task(self.workspace,uuid.uuid4().hex,repo_id=args.repo_ref,task_id=args.slug,title=args.title,priority=args.priority)
        return self.task_info(slug=args.slug)

    def task_edit(self, conn=None, slug=None, *, title=None, priority=None, actor=None,
                  repo_ref=None, why=None, what=None, roi_note=None, **kwargs):
        if actor not in (None, 'local-client'):
            raise ModelError('unsupported_operation','the server authenticates this token as local-client; arbitrary actors are not supported')
        if any(v is not None for v in kwargs.values()):
            raise ModelError('unsupported_operation','unsupported task edit fields')
        row = self.client.task(self.workspace,slug)
        if not row:
            return dict(ok=False,error='task not found')
        self.client.edit_task(self.workspace,uuid.uuid4().hex,task_id=slug,
            expected_revision=row['revision'],title=title,priority=priority,
            repo_id=repo_ref,why=why,what_text=what,roi_note=roi_note)
        return self.task_info(slug=slug)

    def provider_sync_in(self, conn, source, items):
        import hashlib
        if not isinstance(items, list):
            raise ModelError('invalid_request','provider items must be a JSON array')
        allowed = {'external_id','title','status','priority','repo_ref','why','what'}
        for item in items:
            if not isinstance(item, dict) or 'external_id' not in item or item['external_id'] is None:
                raise ModelError('invalid_request','each provider item requires external_id')
            if set(item) - allowed:
                raise ModelError('unsupported_operation','provider metadata is not yet represented; no fields discarded')
            if str(item.get('status','todo')).lower() not in ('open','idea','todo','in_progress','review','published','closed','done','cancelled'):
                raise ModelError('invalid_request','unsupported provider status')
            if item.get('priority','p3') not in ('p0','p1','p2','p3'):
                raise ModelError('invalid_request','unsupported provider priority')
        created, updated = [], []
        owned = {str(row['external_id']):row['id'] for row in self.client.provider_outbox(self.workspace,source)}
        for item in items:
            external = str(item['external_id'])
            # Contract identifiers cannot expose arbitrary provider strings.
            external_key = hashlib.sha256(external.encode()).hexdigest()
            task_id = owned.get(external) or f'{source}:{external_key}'
            existing = self.client.task(self.workspace,task_id)
            repo = item.get('repo_ref') or (existing or {}).get('repository')
            if not repo:
                raise ModelError('invalid_request','provider items require repo_ref')
            raw_status = str(item.get('status','todo')).lower()
            statuses = {'open':'todo','idea':'todo','todo':'todo','in_progress':'in_progress','review':'review','published':'published','closed':'done','done':'done','cancelled':'cancelled'}
            if raw_status not in statuses:
                raise ModelError('invalid_request','unsupported provider status')
            priority = item.get('priority','p3')
            self.client.provider_import(self.workspace,uuid.uuid4().hex,source=source,
                external_id=external,repo_id=repo,task_id=task_id,title=item.get('title') or task_id,
                priority=int(priority[1]),status=statuses[raw_status],expected_revision=(existing or {}).get('revision'),
                why=item.get('why'),what_text=item.get('what'))
            (updated if existing else created).append(task_id)
        return dict(ok=True,source=source,created=created,updated=updated)

    def provider_outbox(self, conn, source):
        rows = self.client.provider_outbox(self.workspace,source)
        return dict(ok=True,source=source,outbox=[dict(r,slug=r['id'],workflow_status=r['status'],priority=f"p{r['priority']}") for r in rows])

    def verify(self, slug, target):
        result = self.client.verify(self.workspace,slug,target,uuid.uuid4().hex)['items'][0]
        accepted = result['receipt']['items'][0]['status'] == 'applied'
        return dict(ok=result['passed'] and accepted,verification=result)

    def dispatch(self, args):
        if args.command == 'provider':
            if args.provider_command == 'pull':
                return self.provider_sync_in(None,args.source,json.loads(Path(args.file).read_text()))
            if args.provider_command == 'outbox':
                return self.provider_outbox(None,args.source)
            if args.provider_command == 'sync':
                from . import provider_adapters
                return provider_adapters.sync(None,args.source,provider_adapters.load_config(args.config_file),direction=args.direction,storage=self)
        if args.command == 'lock':
            if args.lock_command == 'list':
                if args.include_inactive or args.status not in (None, 'active'):
                    raise ModelError('unsupported_operation','model lock list reports active leases only')
                rows = self.client.locks(self.workspace)
                if args.repo_ref:
                    rows = [row for row in rows if row['scope'] == args.repo_ref]
                return {'ok':True,'locks':rows}
            if args.lock_command == 'acquire':
                if args.file or args.force or args.pid or args.reason or args.eta_minutes or args.repo_ref:
                    raise ModelError('unsupported_operation','model leases use --scope-key; repo/file/PID/force/reason/ETA metadata are not supported')
                if args.agent != 'local-client' or args.lock_kind != 'exclusive':
                    raise ModelError('unsupported_operation','model leases require --agent local-client --lock-kind exclusive')
                lease = uuid.uuid4().hex
                self.client.acquire_lock(self.workspace,uuid.uuid4().hex,scope=args.scope_key,lease_id=lease,ttl_seconds=args.lease_seconds)
                return dict(ok=True,scope=args.scope_key,lease_id=lease,owner='local-client')
            if args.lock_command == 'release':
                if args.force or args.reason or args.agent not in (None,'local-client'):
                    raise ModelError('unsupported_operation','model release does not support force/reason/actor overrides')
                row = next((r for r in self.client.locks(self.workspace) if r['lease_id']==str(args.lock_id)),None)
                if not row:
                    return dict(ok=False,error='active lease not found')
                self.client.release_lock(self.workspace,uuid.uuid4().hex,scope=row['scope'],lease_id=row['lease_id'])
                return dict(ok=True,released=row['lease_id'])
            if args.lock_command == 'renew':
                if args.eta_minutes:
                    raise ModelError('unsupported_operation','model leases do not support ETA metadata')
                row = next((r for r in self.client.locks(self.workspace) if r['lease_id']==str(args.lock_id)),None)
                if not row:
                    return dict(ok=False,error='active lease not found')
                self.client.acquire_lock(self.workspace,uuid.uuid4().hex,scope=row['scope'],lease_id=row['lease_id'],ttl_seconds=args.lease_seconds)
                return dict(ok=True,scope=row['scope'],lease_id=row['lease_id'])
        if args.command == 'task':
            if args.task_command == 'list':
                statuses = [s for value in args.workflow_status for s in value.split(',')]
                if args.not_done:
                    statuses = ['todo','in_progress','review','published']
                return self.task_list(repo_ref=args.repo_ref, workflow_status=statuses,assigned_agent=args.assigned_agent,include_done=args.json or args.all)
            if args.task_command == 'info':
                return self.task_info(slug=args.slug)
            if args.task_command == 'create':
                return self.create(args)
            if args.task_command == 'edit':
                return self.task_edit(slug=args.slug,title=args.title,priority=args.priority,
                    actor=args.actor,repo_ref=args.repo_ref,why=args.why,what=args.what,roi_note=args.roi_note)
            if args.task_command == 'verify':
                return self.verify(args.slug,args.target)
        if args.command == 'repo' and args.repo_command == 'list':
            return {'ok':True,'repos':self.repo_tree_snapshot()['repos']}
        raise ModelError('unsupported_operation',f'{args.command} operation is not supported by the model contract; select --legacy-storage explicitly')
