import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'rust/frog-model'
SPEC = importlib.util.spec_from_file_location('upgrade_services',ROOT / 'upgrade_services.py')
UPGRADE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPGRADE)


class ServiceUpgradeTests(unittest.TestCase):
    def test_backup_precedes_upgrade_and_archive_is_read_only(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as directory:
            database, backup = Path(directory)/'model.db', Path(directory)/'backup.db'
            with sqlite3.connect(database) as conn:
                conn.executescript((ROOT/'sql/0001_model.sql').read_text().replace('frog_model_','custom_'))
                conn.execute("INSERT INTO custom_workspaces VALUES('alpha','Alpha')")
                conn.execute("INSERT INTO custom_operations(workspace_id,operation_id,request_key,status) VALUES('alpha','import','key','applied')")
                conn.execute("INSERT INTO custom_repos VALUES('alpha','frog','Frog','import')")
                conn.execute("INSERT INTO custom_tasks(workspace_id,task_id,repo_id,title,priority,status,revision,last_operation_id) VALUES('alpha','one','frog','One',1,'todo',0,'import')")
                conn.execute('CREATE TABLE custom_legacy_mapping(kind TEXT,model_id TEXT,details_json TEXT)')
                original = json.dumps({'why':'Reason','what_text':'Work','roi_note':'ROI','source':'jira','external_id':'Project / 42'})
                conn.execute("INSERT INTO custom_legacy_mapping VALUES('task','one',?)",(original,))
            self.assertTrue(UPGRADE.upgrade(database,backup,'custom_')['ok'])
            with sqlite3.connect(backup) as conn:
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='custom_task_details'").fetchone())
            with sqlite3.connect(database) as conn:
                self.assertEqual(conn.execute('SELECT why,what_text,roi_note FROM custom_task_details').fetchone(),('Reason','Work','ROI'))
                self.assertEqual(conn.execute('SELECT external_id FROM custom_provider_items').fetchone()[0],'Project / 42')
                self.assertEqual(conn.execute('SELECT details_json FROM custom_legacy_mapping').fetchone()[0],original)
                conn.execute("UPDATE custom_task_details SET why='Edited'")
            with self.assertRaises(FileExistsError):
                UPGRADE.upgrade(database,backup,'custom_')
            UPGRADE.upgrade(database,Path(directory)/'second-backup.db','custom_')
            with sqlite3.connect(database) as conn:
                self.assertEqual(conn.execute('SELECT why FROM custom_task_details').fetchone()[0],'Edited')
