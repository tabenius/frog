"""Local adapters for the shared hosted/local MCP read vocabulary.

This is a Frog workspace instance, not a fabricated Rebekah connection. Reads
use the same selected storage backend as native Frog read/write tools.
"""
from __future__ import annotations

import datetime as dt
import uuid


def read_local_instance(name: str, args: dict, identity: str, call) -> dict:
    instance_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'frog:' + identity))
    instance = {'id': instance_id, 'name': 'Local Frog workspace', 'service': 'frog'}
    if args.get('instanceId', instance_id) != instance_id:
        return {'ok': False, 'error': 'not_found'}
    if name == 'dash_list_instances':
        return {'ok': True, 'instances': [instance], 'source': 'local-frog'}
    if name == 'dash_read_instance':
        return {'ok': True, 'instance': instance, 'capabilities': ['read', 'write'], 'source': 'local-frog'}
    if name == 'dash_read_audit':
        result = call('frog_log_tail', {'limit': 100})
        return dict(result, source='local-frog-event-log')
    if name == 'dash_read_view':
        if args['view'] != 'system':
            return {'ok': False, 'error': 'view_unavailable',
                    'message': 'This is a local Frog workspace, not a Rebekah gateway.', 'source': 'local-frog'}
        return {'ok': True, 'view': 'system', 'data': call('frog_status', {}), 'source': 'local-frog'}
    if name == 'dash_read_frog':
        result = call('frog_task_list', {})
        if not result.get('ok'):
            return result
        observed = dt.datetime.now(dt.UTC).isoformat().replace('+00:00', 'Z')
        items = [{'id': task['slug'], 'title': task['title'], 'status': task['workflow_status'],
                  'priority': task['priority'], 'repository': task.get('repo_path'),
                  'assignee': task.get('assigned_agent')}
                 for task in result['tasks']]
        return {'ok': True, 'view': 'frog-kanban', 'source': 'local-frog',
                'data': {'schema': 'frog.local-tasks.v1', 'status': 'ready', 'readOnly': True,
                         'sourceObservedAt': observed, 'stale': False, 'items': items}}
    return {'ok': False, 'error': 'unsupported_tool'}
