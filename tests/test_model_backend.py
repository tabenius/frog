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

    def test_backlog_creation_and_provider_import_preserve_idea(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':0,'status':'idea','priority':1}
        args = SimpleNamespace(workflow_status='idea',git_status='not_started',repo_ref='frog',
            slug='one',title='Backlog idea',priority='p1')
        self.assertTrue(backend.create(args)['ok'])
        self.assertEqual(backend.client.create_task.call_args.kwargs['initial_status'],'idea')
        backend.client.task.return_value = None
        backend.provider_sync_in(None,'dash',[{'external_id':'one','repo_ref':'frog','status':'idea'}])
        self.assertEqual(backend.client.provider_import.call_args.kwargs['status'],'idea')

    def test_backlog_promotion_uses_current_revision_and_rejects_unrepresented_notes(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':7,'status':'idea','priority':1}
        backend.task_status('one',workflow_status='todo')
        self.assertEqual(backend.client.transition_task.call_args.kwargs['expected_revision'],7)
        self.assertEqual(backend.client.transition_task.call_args.kwargs['status'],'todo')
        backend.client.transition_task.reset_mock()
        with self.assertRaises(model_client.ModelError):
            backend.task_status('one',workflow_status='todo',note='Must not be discarded')
        backend.client.transition_task.assert_not_called()
        backend.client.transition_task.side_effect = model_client.ModelError('conflict','stale revision')
        with self.assertRaises(model_client.ModelError):
            backend.task_status('one',workflow_status='todo')

    def test_client_backlog_contract_and_default_creation_compatibility(self):
        client = model_client.ModelClient('http://localhost:9091/')
        with patch.object(client,'write') as write:
            client.create_task('alpha','new',repo_id='frog',task_id='one',title='Default')
            self.assertNotIn('initial_status',write.call_args.args[2])
            client.create_task('alpha','idea',repo_id='frog',task_id='two',title='Idea',initial_status='idea')
            self.assertEqual(write.call_args.args[2]['initial_status'],'idea')
            client.transition_task('alpha','promote',task_id='two',expected_revision=0,status='todo')
            self.assertEqual(write.call_args.args[2]['status'],'todo')
            write.reset_mock()
            with self.assertRaises(model_client.ModelError):
                client.create_task('alpha','bad',repo_id='frog',task_id='three',title='Bad',initial_status='done')
            with self.assertRaises(model_client.ModelError):
                client.transition_task('alpha','skip',task_id='two',expected_revision=0,status='in_progress')
            write.assert_not_called()

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
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), patch.object(Path,'home',return_value=Path(directory)), patch.object(model_backend,'Backend',return_value=backend), patch.object(main_cli.store,'connect',side_effect=AssertionError('legacy fallback')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main_cli.main(['--config',str(Path(directory)/'config.json'),'task','info','one','--json'])
        self.assertNotEqual(code,0)
        self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_model_claim_returns_exact_repo_lease_and_rejects_file_metadata(self):
        backend = self.backend()
        row = {'id':'one','revision':3,'repository':'frog','status':'todo','priority':1,'assignee':None}
        backend.client.task.return_value = row
        backend.rows = Mock(return_value=[row])
        args = SimpleNamespace(slug='one',agent='local-client',file=[],force=False,
                               allow_parallel=False,lock_kind='exclusive')
        result = backend.claim(args)
        self.assertEqual(result['scope'],'frog')
        self.assertEqual(backend.client.acquire_lock.call_args.kwargs['lease_id'],result['lease_id'])
        self.assertEqual(backend.client.claim_task.call_args.kwargs['expected_revision'],3)
        backend.client.claim_task.reset_mock()
        args.file = ['not silently discarded']
        with self.assertRaises(model_client.ModelError):
            backend.claim(args)
        backend.client.claim_task.assert_not_called()

    def test_takeover_passes_observed_owner_revision_and_fenced_lease(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':7,'repository':'frog',
            'status':'review','priority':1,'assignee':'previous-agent'}
        backend.client.locks.return_value = [{'scope':'frog','lease_id':'lease','owner':'local-client'}]
        backend.takeover(SimpleNamespace(slug='one',agent=None,lease_id='lease',reason='Approved recovery'))
        fields = backend.client.take_over_task.call_args.kwargs
        self.assertEqual((fields['expected_revision'],fields['expected_assignee'],fields['lease_id']),
                         (7,'previous-agent','lease'))
        backend.client.take_over_task.reset_mock()
        with self.assertRaises(model_client.ModelError):
            backend.takeover(SimpleNamespace(slug='one',agent=None,lease_id='wrong',reason='Recovery'))
        backend.client.take_over_task.assert_not_called()

    def test_claim_after_released_review_preserves_pending_task(self):
        for status in ('review', 'published'):
            backend = self.backend()
            row = {'id':'one','revision':0,'repository':'frog','status':'todo','priority':1,'assignee':None}
            pending = dict(row, id='parked', repository='mouseion', status=status, assignee='local-client')
            backend.client.task.return_value = row
            backend.rows = Mock(return_value=[row, pending])
            backend.client.locks.return_value = []
            args = SimpleNamespace(slug='one',agent='local-client',file=[],force=False,
                                   allow_parallel=False,lock_kind='exclusive')
            self.assertTrue(backend.claim(args)['ok'])
            backend.client.transition_task.assert_not_called()
            self.assertEqual(pending['status'], status)

    def test_finish_uses_atomic_endpoint_and_never_client_side_transition_or_release(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':3,'repository':'frog','status':'published','assignee':'local-client'}
        backend.client.locks.return_value = [{'scope':'frog','owner':'local-client','lease_id':'exact'}]
        backend.verify = Mock(return_value={'ok':True,'build_operation':'build'})
        backend.task_info = Mock(return_value={'ok':True})
        args = SimpleNamespace(slug='one', agent='local-client', no_verify=False, lease_id='exact')
        self.assertTrue(backend.finish(args)['ok'])
        self.assertEqual(backend.client.finish_task.call_args.kwargs,
                         dict(task_id='one',expected_revision=3,lease_id='exact',build_operation='build'))
        backend.client.transition_task.assert_not_called()
        backend.client.release_lock.assert_not_called()
        backend.verify.return_value = {'ok':True}
        with self.assertRaises(model_client.ModelError):
            backend.finish(args)

    def test_explicit_pause_and_resume_use_observed_revision_and_new_lease(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':3}
        backend.client.work_handoff.return_value = {'items':[{'status':'applied'}]}
        for action in ('pause', 'resume', 'handoff'):
            args = SimpleNamespace(command='task', task_command=action, slug='one',agent='local-client',lease_id='old',reason='Review pause',receiver='reviewer')
            self.assertTrue(backend.dispatch(args)['ok'])
            call = backend.client.work_handoff.call_args.kwargs
            self.assertEqual(call['expected_revision'], 3)
            self.assertEqual(call['action'], action)
            if action == 'resume':
                self.assertNotEqual(call['lease_id'], 'old')
            else:
                self.assertEqual(call['lease_id'], 'old')

    def test_native_parking_delegates_claim_exclusivity_to_atomic_server_guard(self):
        backend = self.backend()
        backend.client.native_work_parking.return_value = True
        row = {'id':'one','revision':0,'repository':'frog','status':'todo','priority':1,'assignee':None}
        backend.client.task.return_value = row
        backend.rows = Mock(return_value=[row, dict(row, id='parked',status='in_progress',assignee='local-client')])
        args = SimpleNamespace(slug='one',agent='local-client',file=[],force=False,allow_parallel=False,lock_kind='exclusive')
        self.assertTrue(backend.claim(args)['ok'])
        backend.client.claim_task.assert_called_once()

    def test_claim_still_refuses_in_progress_or_leased_review(self):
        for status in ('in_progress', 'review', 'published'):
            backend = self.backend()
            row = {'id':'one','revision':0,'repository':'frog','status':'todo','priority':1,'assignee':None}
            backend.client.task.return_value = row
            backend.rows = Mock(return_value=[row, dict(row, id='other', repository='mouseion', status=status, assignee='local-client')])
            backend.client.locks.return_value = [{'scope':'mouseion','owner':'local-client'}]
            args = SimpleNamespace(slug='one',agent='local-client',file=[],force=False,
                                   allow_parallel=False,lock_kind='exclusive')
            with self.assertRaises(model_client.ModelError):
                backend.claim(args)
            backend.client.acquire_lock.assert_not_called()

    def test_finish_verifies_published_revision_and_releases_only_exact_lease(self):
        backend = self.backend()
        backend.client.task.return_value = {'id':'one','revision':9,'repository':'frog',
            'status':'published','priority':1,'assignee':'local-client'}
        backend.client.locks.return_value = [{'scope':'frog','lease_id':'lease','owner':'local-client'}]
        backend.client.verify.return_value = {'build_operation':'build','items':[{'passed':True,'receipt':{'items':[{'status':'applied'}]}}]}
        args = SimpleNamespace(slug='one',agent=None,no_verify=False,lease_id='lease')
        self.assertTrue(backend.finish(args)['ok'])
        self.assertEqual(backend.client.verify.call_args.args[2],'verify')
        self.assertEqual(backend.client.finish_task.call_args.kwargs,
                         {'task_id':'one','expected_revision':9,'lease_id':'lease','build_operation':'build'})
        backend.client.release_lock.assert_not_called()

    def test_finish_cannot_skip_gates_or_complete_a_failed_build(self):
        backend = self.backend()
        row = {'id':'one','revision':9,'repository':'frog','status':'published','priority':1,'assignee':'local-client'}
        backend.client.task.return_value = row
        backend.client.locks.return_value = [{'scope':'frog','lease_id':'lease','owner':'local-client'}]
        args = SimpleNamespace(slug='one',agent=None,no_verify=True,lease_id='lease')
        with self.assertRaises(model_client.ModelError):
            backend.finish(args)
        args.no_verify = False
        backend.client.verify.return_value = {'items':[{'passed':False,'receipt':{'items':[{'status':'applied'}]}}]}
        self.assertFalse(backend.finish(args)['ok'])
        backend.client.transition_task.assert_not_called()
        backend.client.release_lock.assert_not_called()
        row['status'] = 'review'
        with self.assertRaises(model_client.ModelError):
            backend.finish(args)

    def test_status_done_cannot_bypass_finish(self):
        backend = self.backend()
        with self.assertRaises(model_client.ModelError):
            backend.status(SimpleNamespace(slug='one',git_status=None,note=None,workflow_status='done'))
        backend.client.transition_task.assert_not_called()

    def test_takeover_history_pages_by_revision(self):
        client = model_client.ModelClient('http://localhost:9091/')
        first = [{'revision':i+1} for i in range(200)]
        with patch.object(client,'read',side_effect=[{'items':first},{'items':[{'revision':201}]}]) as read:
            self.assertEqual(len(client.task_takeovers('alpha','one')),201)
            self.assertEqual(read.call_args.args[2]['after'],200)

    def test_mcp_lifecycle_tools_route_to_model_dispatch(self):
        backend = self.backend()
        backend.dispatch = Mock(return_value={'ok':True})
        with patch.object(mcp_server,'_MODEL_OPTIONS',{'enabled':True}), \
             patch.object(model_backend,'Backend',return_value=backend), \
             patch.object(mcp_server,'_with_conn',side_effect=AssertionError('legacy access')):
            mcp_server._call_local('frog_task_takeover',
                {'slug':'one','lease_id':'lease','reason':'Approved recovery'},None)
            takeover = backend.dispatch.call_args.args[0]
            self.assertEqual((takeover.task_command,takeover.slug,takeover.lease_id,takeover.reason),
                             ('takeover','one','lease','Approved recovery'))
            mcp_server._call_local('frog_task_finish',
                {'slug':'one','lease_id':'lease','verify':False},None)
            finish = backend.dispatch.call_args.args[0]
            self.assertEqual((finish.task_command,finish.lease_id,finish.no_verify),
                             ('finish','lease',True))
            mcp_server._call_local('frog_task_takeovers',{'slug':'one'},None)
            history = backend.dispatch.call_args.args[0]
            self.assertEqual((history.task_command,history.slug),('takeovers','one'))

    def test_legacy_storage_refuses_model_only_lifecycle_arguments(self):
        with patch.object(mcp_server,'_MODEL_OPTIONS',{'enabled':False}), \
             patch.object(mcp_server,'_with_conn',side_effect=lambda db, fn: fn(None)):
            result = mcp_server._call_local('frog_task_takeover',
                {'slug':'one','lease_id':'lease','reason':'Approved recovery'},None)
            self.assertFalse(result['ok'])
            result = mcp_server._call_local('frog_task_finish',
                {'slug':'one','lease_id':'lease'},None)
            self.assertFalse(result['ok'])
