#!/usr/bin/env python3
"""Back up and add model services, hydrating metadata from migration archives once.

Never opens the original legacy workspace database. Archived tables are read
only for this explicit one-time conversion, not synchronized on server startup.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3


def upgrade(database, backup, prefix='frog_model_'):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', prefix):
        raise ValueError('invalid table prefix')
    database, backup = Path(database).resolve(), Path(backup).absolute()
    with sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=30) as conn:
        conn.execute(f'SELECT version FROM {prefix}schema').fetchone()
        # Exclusive creation prevents overwriting a previous backup or a symlink.
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(backup) as saved:
            conn.backup(saved)
            if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('backup integrity check failed')
        conn.execute('PRAGMA foreign_keys=ON')
        schema = Path(__file__).with_name('sql').joinpath('0002_services.sql').read_text()
        conn.executescript('BEGIN IMMEDIATE;\n' + schema.replace('frog_model_', prefix))
        try:
            conn.execute(f'CREATE TABLE IF NOT EXISTS {prefix}service_upgrades(name TEXT PRIMARY KEY)')
            done = conn.execute(f"SELECT 1 FROM {prefix}service_upgrades WHERE name='archive-details-v1'").fetchone()
            mapping = conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (prefix+'legacy_mapping',)).fetchone()
            if not done and mapping:
                for task, details in conn.execute(f"SELECT model_id,details_json FROM {prefix}legacy_mapping WHERE kind='task'").fetchall():
                    row = json.loads(details)
                    for (workspace,) in conn.execute(f'SELECT workspace_id FROM {prefix}tasks WHERE task_id=?',(task,)).fetchall():
                        conn.execute(f'INSERT INTO {prefix}task_details(workspace_id,task_id,why,what_text,roi_note) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING', (workspace,task,row.get('why'),row.get('what_text'),row.get('roi_note')))
                        if row.get('source') and row.get('external_id') is not None:
                            conn.execute(f'INSERT INTO {prefix}provider_items(workspace_id,source,external_id,task_id) VALUES(?,?,?,?) ON CONFLICT DO NOTHING',(workspace,row['source'],str(row['external_id']),task))
            conn.execute(f"INSERT INTO {prefix}service_upgrades VALUES('archive-details-v1') ON CONFLICT DO NOTHING")
            if conn.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('upgrade foreign-key check failed')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {'ok':True,'backup':str(backup)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--backup',required=True)
    parser.add_argument('--table-prefix',default='frog_model_')
    args = parser.parse_args()
    print(json.dumps(upgrade(args.database,args.backup,args.table_prefix)))
