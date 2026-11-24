#!/usr/bin/env python3
"""Generate/check Dash's vendored contract from Frog's canonical JSON.

No network, no SSH, no adjacent checkout required at runtime. CI can provide
the two checkouts explicitly and use --check to fail on any catalog drift.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dash', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    data = (ROOT / 'src/ragbaz_frog/mcp_contract.json').read_bytes()
    destination = args.dash / 'shared/frog-mcp-contract.json'
    lock = args.dash / 'shared/frog-mcp-contract.lock.json'
    metadata = (json.dumps({'source': 'tabenius/frog', 'path': 'src/ragbaz_frog/mcp_contract.json',
                           'sha256': hashlib.sha256(data).hexdigest()}, indent=2) + '\n').encode()
    if args.check:
        if not destination.is_file() or destination.read_bytes() != data or not lock.is_file() or lock.read_bytes() != metadata:
            parser.exit(1, 'MCP contract drift: run sync-mcp-contract.py without --check\n')
    else:
        if not (args.dash / 'package.json').is_file() or not destination.parent.is_dir():
            parser.error('--dash must name the Dash checkout with its existing shared/ directory')
        destination.write_bytes(data)
        lock.write_bytes(metadata)
    print('MCP contract synchronized: ' + hashlib.sha256(data).hexdigest())

if __name__ == '__main__':
    main()
