#!/usr/bin/env python3
"""Vendor committed synthetic contract fixtures; no runtime repository coupling."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/task-observations.v1.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true", help="replace only previously inventoried unmodified generated fixtures")
    args = parser.parse_args()
    if subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--", str(SOURCE)], text=True).strip():
        parser.error("fixture source must be committed and unchanged")
    commit = subprocess.check_output(["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", str(SOURCE)], text=True).strip()
    raw = SOURCE.read_bytes()
    metadata = {"schema": "ragbaz.fixture-vendor/v1", "source": "https://github.com/tabenius/frog", "source_path": "contracts/task-observations.v1.json", "source_commit": commit, "sha256": hashlib.sha256(raw).hexdigest()}
    manifest = (json.dumps(metadata, indent=2) + "\n").encode()
    for target in args.targets:
        directory = target.resolve() / "tests/fixtures"
        if args.update:
            previous = json.loads((directory / "task-observations.provenance.json").read_text())
            existing = (directory / SOURCE.name).read_bytes()
            if previous.get("source") != metadata["source"] or previous.get("source_path") != metadata["source_path"] or hashlib.sha256(existing).hexdigest() != previous.get("sha256"):
                parser.error(f"existing fixture provenance or content was modified: {directory}")
        for name, content in ((SOURCE.name, raw), ("task-observations.provenance.json", manifest)):
            path = directory / name
            if path.exists():
                if path.read_bytes() != content:
                    if not args.update or args.check:
                        parser.error(f"refusing to overwrite differing fixture: {path}")
                    path.write_bytes(content)
            elif args.check:
                parser.error(f"missing vendored fixture: {path}")
            else:
                directory.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        print(f"{'checked' if args.check else 'vendored'} {target} at {commit}")


if __name__ == "__main__":
    main()
