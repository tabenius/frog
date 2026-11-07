import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from _util import fresh_db
from ragbaz_frog import mcp_server
from ragbaz_frog.mcp_contract import contract, shared_specs, validate_arguments
from test_mcp_server import _rpc_tool

ROOT = Path(__file__).resolve().parents[1]


class McpContract(unittest.TestCase):
    def test_wire_specs_use_the_shared_contract_without_schema_regeneration(self):
        # Actual stdio protocol, not merely the source registry.
        from test_mcp_server import _mcp_lines, _by_id
        lines, _ = _mcp_lines([
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        ])
        tools = {tool['name']: tool for tool in _by_id(lines, 2)['result']['tools']}
        for spec in shared_specs():
            for key in ('description', 'inputSchema'):
                self.assertEqual(tools[spec['name']][key], spec[key])
            for key, value in spec['annotations'].items():
                self.assertEqual(tools[spec['name']]['annotations'][key], value)
        self.assertFalse(tools['frog_task_create']['annotations']['readOnlyHint'])

    def test_shared_validator_refuses_unsupported_schema_vocabulary(self):
        # Adding a schema feature requires implementing its stdlib validator too.
        for spec in shared_specs():
            schema = spec['inputSchema']
            self.assertFalse(set(schema) - {'type', 'properties', 'required', 'additionalProperties'})
            for rule in schema['properties'].values():
                self.assertFalse(set(rule) - {'type', 'enum', 'format', 'minimum', 'maximum'})
                self.assertIn(rule['type'], ('string', 'integer'))
                if 'format' in rule:
                    self.assertEqual(rule['format'], 'uuid')
        for args in ([], {'limit': True}, {'limit': 101}, {'limit': 0}, {'unknown': 1}):
            with self.assertRaises(ValueError):
                validate_arguments('frog_log_tail', args)

    def test_dash_artifact_matches_when_both_checkouts_are_present(self):
        target = ROOT.parent / 'ragbaz-dash/shared/frog-mcp-contract.json'
        if not target.exists():
            self.skipTest('Standalone checkout: run bin/sync-mcp-contract.py --dash CHECKOUT --check in integration CI')
        source = ROOT / 'src/ragbaz_frog/mcp_contract.json'
        self.assertEqual(target.read_bytes(), source.read_bytes())
        lock = json.loads(target.with_name('frog-mcp-contract.lock.json').read_text())
        self.assertEqual(lock['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_read_write_local_roundtrip_through_actual_stdio(self):
        db = fresh_db()
        created = _rpc_tool('frog_task_create', {'slug': 'shared-local', 'title': 'Native write'}, db=db)
        self.assertFalse(created.get('isError'), created)
        listed = _rpc_tool('dash_list_instances', db=db)
        instance = json.loads(listed['content'][0]['text'])['instances'][0]
        self.assertEqual(instance['service'], 'frog')
        read = _rpc_tool('dash_read_frog', {'instanceId': instance['id']}, db=db)
        payload = json.loads(read['content'][0]['text'])
        self.assertEqual(payload['source'], 'local-frog')
        self.assertIn('shared-local', [item['id'] for item in payload['data']['items']])
        edited = _rpc_tool('frog_task_edit', {'slug': 'shared-local', 'title': 'Edited via MCP'}, db=db)
        self.assertFalse(edited.get('isError'), edited)
        reread = json.loads(_rpc_tool('dash_read_frog', {'instanceId': instance['id']}, db=db)['content'][0]['text'])
        self.assertEqual(reread['data']['items'][0]['title'], 'Edited via MCP')

    def test_local_aliases_do_not_pretend_to_be_rebekah(self):
        db = fresh_db()
        with patch.object(mcp_server, '_DB_OVERRIDE', db), patch.object(mcp_server, '_MODEL_OPTIONS', {'enabled': False}):
            instance = mcp_server.call_tool('dash_list_instances', {}, config_path=None)['instances'][0]
            missing = mcp_server.call_tool('dash_read_frog', {'instanceId': '11111111-1111-4111-8111-111111111111'}, config_path=None)
            self.assertEqual(missing['error'], 'not_found')
            unavailable = mcp_server.call_tool('dash_read_view', {'instanceId': instance['id'], 'view': 'oversight'}, config_path=None)
            self.assertEqual(unavailable['error'], 'view_unavailable')
            invalid = mcp_server.call_tool('dash_read_frog', {'instanceId': 'not-a-uuid'}, config_path=None)
            self.assertEqual(invalid['error'], 'invalid_arguments')
