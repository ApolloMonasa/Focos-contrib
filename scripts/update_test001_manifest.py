#!/usr/bin/env python3
"""生成/核对分析实现清单；同时更新 CRITERIA 的实现身份与自身哈希。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from test001.config import actual_implementation_files
from test001.errors import InputError

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('scripts/test001.py', 'scripts/test001', 'tests/test001/criteria/v1/adapters')
CRITERIA = ('regional-2025-log-v1', 'regional-2025-log-v2')
CRITERIA_DIR = ROOT / 'tests/test001/criteria/v1'
MANIFEST = CRITERIA_DIR / 'implementation.sha256'


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def criteria_hash(data: dict) -> str:
    payload = {key: value for key, value in data.items() if key != 'criteria_hash'}
    return hashlib.sha256(canonical(payload)).hexdigest()


def files():
    return actual_implementation_files(ROOT, list(ROOTS))


def build_manifest() -> bytes:
    rows = []
    for relative, path in sorted(files().items()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f'{digest}  {relative}\n')
    return ''.join(rows).encode('utf-8')


def update(*, check: bool) -> int:
    manifest = build_manifest()
    manifest_hash = hashlib.sha256(manifest).hexdigest()
    if check:
        if not MANIFEST.exists() or MANIFEST.read_bytes() != manifest:
            print('implementation.sha256 is stale', file=sys.stderr)
            return 1
    else:
        MANIFEST.write_bytes(manifest)
    for criteria_id in CRITERIA:
        path = CRITERIA_DIR / f'{criteria_id}.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        wanted = json.loads(json.dumps(data))
        wanted['implementation']['sha256'] = manifest_hash
        for spec in wanted['adapters'].values():
            if spec['kind'] == 'subprocess':
                adapter = CRITERIA_DIR / spec['entry']
                spec['sha256'] = hashlib.sha256(adapter.read_bytes()).hexdigest()
        wanted['criteria_hash'] = criteria_hash(wanted)
        if wanted != data:
            if check:
                print(f'{criteria_id} identity is stale', file=sys.stderr)
                return 1
            path.write_text(json.dumps(wanted, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                            encoding='utf-8')
    if check:
        print('implementation manifest and criteria hashes are current')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只核对，不写文件')
    args = parser.parse_args(argv)
    try:
        return update(check=args.check)
    except InputError as exc:
        print(f'update_test001_manifest: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
