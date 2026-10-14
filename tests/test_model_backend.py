import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ragbaz_frog import model_backend, model_client, provider_adapters, mcp_server


class ModelBackendTests(unittest.TestCase):
    def backend(self):
        backend = object.__new__(model_backend.Backend)
        backend.workspace = 'alpha'
        backend.client = Mock()
        backend.client.task_details.return_value = {}
        backend.client.provider_outbox.return_value = []
        return backend

    def test_settings_default_and_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=Path(directory)):
            config = Path(directory) / 'config.json'
            self.assertTrue(model_backend.settings(config_path=str(config))['enabled'])
            config.write_text(json.dumps({'model_client':{'enabled':False,'url':'http://localhost:9091/','workspace':'configured','token_file':'config-token'}}))
            self.assertFalse(model_backend.settings(config_path=str(config))['enabled'])
            with patch.dict(os.environ, {'FROG_MODEL_CLIENT':'true','FROG_MODEL_WORKSPACE':'environment','FROG_MODEL_TOKEN_FILE':'env-token'}):
                options = model_backend.settings(config_path=str(config), enabled=False, workspace='option', token_file='option-token')
                self.assertFalse(options['enabled'])
                self.assertEqual(options['workspace'],'option')
                self.assertEqual(options['token_file'],'option-token')
                self.assertFalse(model_backend.settings(config_path=str(config), legacy_db=True)['enabled'])
                self.assertTrue(model_backend.settings(config_path=str(config), legacy_db=True, enabled=True)['enabled'])
            config.write_text(json.dumps({'model_client':{'enabled':'false'}}))
            with self.assertRaises(ValueError):
                model_backend.settings(config_path=str(config))

    def test_edit_uses_revision_and_preserves_metadata(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':7,'status':'review','priority':1}
        self.assertTrue(backend.task_edit(slug='one',title='Changed',why='Reason')['ok'])
        self.assertEqual(backend.client.edit_task.call_args.kwargs['expected_revision'],7)
        self.assertEqual(backend.client.edit_task.call_args.kwargs['why'],'Reason')
        with self.assertRaises(model_client.ModelError):
            backend.task_edit(slug='one',unknown='not silently discarded')

    def test_provider_identity_roundtrip_and_preflight(self):
        backend = self.backend()
        backend.client.task.return_value = None
        result = backend.provider_sync_in(None,'jira',[{'external_id':'Project / 42','repo_ref':'frog','title':'Imported'}])
        self.assertTrue(result['ok'])
        self.assertEqual(backend.client.provider_import.call_args.kwargs['external_id'],'Project / 42')
        backend.client.provider_import.reset_mock()
        with self.assertRaises(model_client.ModelError):
            backend.provider_sync_in(None,'jira',[{'external_id':'valid','repo_ref':'frog'},{'external_id':'bad','unknown':'unsupported'}])
        backend.client.provider_import.assert_not_called()

    def test_provider_adapters_use_injected_backend_not_sqlite(self):
        backend = self.backend()
        backend.provider_sync_in = Mock(return_value={'ok':True})
        with patch.object(provider_adapters, 'pull', return_value={'ok':True,'items':[{'external_id':'42','title':'Item'}]}), patch.object(provider_adapters.store,'provider_sync_in',side_effect=AssertionError('legacy access')):
            result = provider_adapters.sync(None,'jira',{'repo_ref':'frog'},direction='pull',storage=backend)
        self.assertTrue(result['ok'])
        self.assertEqual(backend.provider_sync_in.call_args.args[2][0]['repo_ref'],'frog')

    def test_mcp_edit_routes_to_model_without_opening_legacy(self):
        backend = self.backend()
        backend.task_edit = Mock(return_value={'ok':True})
        with patch.object(mcp_server,'_MODEL_OPTIONS',{'enabled':True}), patch.object(model_backend,'Backend',return_value=backend), patch.object(mcp_server,'_with_conn',side_effect=AssertionError('legacy access')):
            result = mcp_server._call_local('frog_task_edit',{'slug':'one','title':'Changed'},None)
        self.assertTrue(result['ok'])
        backend.task_edit.assert_called_once_with(slug='one',title='Changed')

    def test_lock_dispatch_preserves_scope_and_rejects_ignored_metadata(self):
        backend = self.backend()
        args = SimpleNamespace(command='lock',lock_command='acquire',scope_key='repo:build',repo_ref=None,
            file=[],force=False,pid=None,reason=None,eta_minutes=None,agent='local-client',lock_kind='exclusive',lease_seconds=60)
        result = backend.dispatch(args)
        self.assertEqual(result['scope'],'repo:build')
        self.assertEqual(backend.client.acquire_lock.call_args.kwargs['scope'],'repo:build')
        args.reason = 'must not be discarded'
        with self.assertRaises(model_client.ModelError):
            backend.dispatch(args)

    def test_service_lists_paginate_without_truncation(self):
        client = model_client.ModelClient('http://localhost:9091/')
        first = [{'external_id':str(i)} for i in range(200)]
        with patch.object(client,'read',side_effect=[{'items':first},{'items':[{'external_id':'next'}]}]) as read:
            self.assertEqual(len(client.provider_outbox('alpha','jira')),201)
            self.assertEqual(read.call_args.args[2]['after'],'199')

    def test_failed_or_stale_verification_is_not_success(self):
        backend = self.backend()
        for passed, status in [(False,'applied'),(True,'conflict')]:
            backend.client.verify.return_value = {'items':[{'passed':passed,'receipt':{'items':[{'status':status}]}}]}
            self.assertFalse(backend.verify('one','test')['ok'])

    def test_cli_failure_does_not_fallback_to_legacy(self):
        import contextlib
        import io
        from ragbaz_frog import main_cli
        backend = self.backend()
        backend.dispatch = Mock(side_effect=model_client.ModelError('unreachable','model unavailable'))
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as directory, patch.dict(os.environ,{},clear=True), patch.object(Path,'home',return_value=Path(directory)), patch.object(model_backend,'Backend',return_value=backend), patch.object(main_cli.store,'connect',side_effect=AssertionError('legacy fallback')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main_cli.main(['--config',str(Path(directory)/'config.json'),'task','info','one','--json'])
        self.assertNotEqual(code,0)
        self.assertFalse(json.loads(output.getvalue())['ok'])
