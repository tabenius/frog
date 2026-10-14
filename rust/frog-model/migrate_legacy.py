#!/usr/bin/env python3
"""Snapshot a legacy AGENTS.db into a new, explicitly migrated model store.

Original tables remain archival only; never run legacy writers on the output.
No source writes, no destination overwrite, no dropped legacy fields.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,120}\Z")

def identity(value, kind):
    value = str(value)
    return value if IDENTIFIER.fullmatch(value) else kind + ":" + hashlib.sha256(value.encode()).hexdigest()

def migrate(source, output, workspace, prefix="frog_model_"):
    source, output = Path(source).resolve(), Path(output).absolute()
    if not IDENTIFIER.fullmatch(workspace):
        raise ValueError("invalid workspace identifier")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prefix):
        raise ValueError("invalid table prefix")
    if output.exists():
        raise FileExistsError("destination already exists; refusing overwrite")
    fd, staging = tempfile.mkstemp(prefix=".frog-migration-", dir=output.parent)
    os.close(fd)
    os.chmod(staging, 0o600)
    try:
        # SQLite backup includes committed WAL pages in a consistent snapshot.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(staging) as dst:
            src.backup(dst)
            # Publish a standalone file: a copied WAL journal mode must not leave
            # migrated pages in a staging-name WAL that won't follow the rename.
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.row_factory = sqlite3.Row
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("source snapshot failed integrity check")
            if dst.execute("SELECT 1 FROM sqlite_master WHERE name=?", (prefix + "schema",)).fetchone():
                raise ValueError("source already contains a model schema")
            dst.execute("PRAGMA foreign_keys=ON")
            schema = Path(__file__).with_name("sql").joinpath("0001_model.sql").read_text()
            schema += Path(__file__).with_name("sql").joinpath("0002_services.sql").read_text()
            dst.executescript("BEGIN IMMEDIATE;\n" + schema.replace("frog_model_", prefix))
            dst.execute(f"CREATE TABLE {prefix}legacy_mapping(kind TEXT, legacy_id TEXT, model_id TEXT, details_json TEXT NOT NULL, PRIMARY KEY(kind,legacy_id))")
            dst.execute(f"INSERT INTO {prefix}workspaces VALUES(?,?)", (workspace, "Migrated legacy workspace"))
            bootstrap = "legacy-migration:v1"
            dst.execute(f"INSERT INTO {prefix}operations(workspace_id,operation_id,request_key,status) VALUES(?,?,?,'applied')", (workspace, bootstrap, "legacy-migration:v1"))
            repos = {}
            used = set()
            for row in dst.execute("SELECT * FROM repos").fetchall():
                repo = identity(row["repo_key"] or row["name"] or row["repo_path"], "repo")
                repos[row["repo_path"]] = repo
                if repo not in used:
                    dst.execute(f"INSERT INTO {prefix}repos VALUES(?,?,?,?)", (workspace, repo, row["name"] or repo, bootstrap))
                    used.add(repo)
                dst.execute(f"INSERT INTO {prefix}legacy_mapping VALUES('repository',?,?,?)", (row["repo_path"], repo, json.dumps(dict(row))))
            tasks = dst.execute("SELECT * FROM tasks").fetchall()
            ids = {row["slug"]: identity(row["slug"], "task") for row in tasks}
            if len(set(ids.values())) != len(ids):
                raise ValueError("task identity collision")
            unscoped = "legacy:unscoped"
            if any(row["repo_path"] is None for row in tasks):
                if unscoped in used:
                    raise ValueError("unscoped repository identity collision")
                dst.execute(f"INSERT INTO {prefix}repos VALUES(?,?,?,?)", (workspace, unscoped, "Workspace tasks", bootstrap))
            status_mapping = {"idea":"todo", "blocked":"todo"}
            mapped_statuses = {}
            for row in tasks:
                repo = unscoped if row["repo_path"] is None else repos[row["repo_path"]]
                status = status_mapping.get(row["workflow_status"], row["workflow_status"])
                if status not in {"todo","in_progress","review","published","done","cancelled"}:
                    raise ValueError("unsupported legacy status: " + str(status))
                if row["priority"] not in {"p0","p1","p2","p3"}:
                    raise ValueError("unsupported legacy priority")
                if status != row["workflow_status"]:
                    mapped_statuses[row["workflow_status"]] = mapped_statuses.get(row["workflow_status"], 0) + 1
                dst.execute(f"INSERT INTO {prefix}tasks(workspace_id,task_id,repo_id,title,priority,status,assignee,revision,last_operation_id,updated_at,status_observed_at) VALUES(?,?,?,?,?,?,?,0,?,?,?)",
                    (workspace, ids[row["slug"]], repo, row["title"], int(row["priority"][1]), status, row["assigned_agent"], bootstrap, row["updated_at"], row["status_confidence_at"]))
                dst.execute(f"INSERT INTO {prefix}legacy_mapping VALUES('task',?,?,?)", (row["slug"], ids[row["slug"]], json.dumps(dict(row))))
                dst.execute(f"INSERT INTO {prefix}task_details(workspace_id,task_id,why,what_text,roi_note) VALUES(?,?,?,?,?)", (workspace,ids[row['slug']],row['why'],row['what_text'],row['roi_note']))
                if row['source'] and row['external_id'] is not None:
                    dst.execute(f"INSERT INTO {prefix}provider_items(workspace_id,source,external_id,task_id) VALUES(?,?,?,?)", (workspace,row['source'],str(row['external_id']),ids[row['slug']]))
            dependencies = 0
            for row in dst.execute("SELECT * FROM task_dependencies WHERE relation='depends_on'").fetchall():
                dst.execute(f"INSERT INTO {prefix}dependencies VALUES(?,?,?)", (workspace, ids[row["task_slug"]], ids[row["depends_on_slug"]]))
                dependencies += 1
            if dst.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("migration foreign-key check failed")
            dst.commit()
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("output integrity check failed")
            report = {"tasks":len(tasks), "repositories":len(used), "legacy_repository_paths":len(repos), "dependencies":dependencies,
                "workspace":workspace, "prefix":prefix, "status_mappings":mapped_statuses,
                "legacy_tables":"retained as archive; do not use legacy writers on this database"}
        with open(staging, "rb") as handle:
            os.fsync(handle.fileno())
        # Atomic publish without replacing any existing destination.
        os.link(staging, output)
        return report
    finally:
        os.unlink(staging)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--table-prefix", default="frog_model_")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.output, args.workspace, args.table_prefix), indent=2))
