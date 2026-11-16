#!/usr/bin/env python3
"""校验不可变历史证据并用当前判据回放；不覆写原摘要，不把旧环境标为新协议通过。"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory import ROOT, REFERENCE_LOCK, digest


def unpack(archive, destination, expected_hash):
    if digest(archive) != expected_hash:
        raise ValueError('evidence archive SHA256 mismatch')
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        paths = [PurePosixPath(m.name) for m in members]
        if (len({str(p) for p in paths}) != len(paths) or
                any(p.is_absolute() or '..' in p.parts or not p.parts or
                    p.parts[0] != 'test-001-evidence-2026-11-08' for p in paths) or
                any(not (m.isfile() or m.isdir()) for m in members) or
                sum(m.size for m in members) > 20_000_000):
            raise ValueError('invalid evidence archive layout')
        for member in members:
            target = destination/member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as handle:
                    target.write_bytes(handle.read())
    root = destination/'test-001-evidence-2026-11-08'
    checked = set()
    for line in (root/'SHA256SUMS').read_text().splitlines():
        sha, name = line.split(maxsplit=1)
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or name in checked:
            raise ValueError('invalid checksum entry')
        if digest(root/name) != sha:
            raise ValueError('evidence file SHA256 mismatch: ' + name)
        checked.add(name)
    files = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()} - {'SHA256SUMS'}
    if checked != files or len(checked) != 38:
        raise ValueError('incomplete evidence checksum manifest')
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT/'target/test001/replay')
    parser.add_argument('--judge-dir', type=Path, default=ROOT/'third_party/oskernel-testsuits-cooperation/judge')
    args = parser.parse_args(argv)
    lock = json.loads(REFERENCE_LOCK.read_text())['evidence']
    args.out.mkdir(parents=True, exist_ok=True)
    archive = args.archive or args.out/lock['asset']
    if not archive.exists():
        if args.archive:
            raise ValueError('explicit evidence archive missing')
        subprocess.run(['gh', 'release', 'download', lock['tag'], '-R', lock['repo'],
                        '-p', lock['asset'], '-D', str(args.out)], check=True, timeout=60)
    root = unpack(archive, args.out/'original', lock['sha256'])
    samples = sorted((root/'results').glob('*/summary.json'))
    if len(samples) != 15 or len(list((root/'logs').glob('*.log'))) != 7:
        raise ValueError('unexpected number of historical logs/results')
    rows = []
    for path in samples:
        original = json.loads(path.read_text())
        log = root/'logs'/PureWindowsPath(original['meta']['log']).name
        out = args.out/'current'/path.parent.name
        cmd = [sys.executable, '-B', str(ROOT/'scripts/run_tests.py'), '--log', str(log),
               '--out', str(out), '--reference', 'legacy', '--libc', original['meta']['libc'],
               '--inventory', str(ROOT/'tests/inventory.json'), '--judge-dir', str(args.judge_dir),
               '--team-judge-dir', str(ROOT/'scripts/judge_team')]
        for group in original['groups']:
            cmd += ['--group', group['group']]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode not in (0, 1) or not (out/'summary.json').exists():
            raise ValueError(f'replay failed: {path.parent.name}: {proc.stderr}')
        current = json.loads((out/'summary.json').read_text())
        expected_totals = {k: v for k, v in original['totals'].items() if k != 'score'}
        row = {'sample': path.parent.name, 'log_sha256': digest(log),
               'groups_match': current['groups'] == original['groups'],
               'totals_match': current['totals'] == expected_totals,
               'environment': current['meta']['environment']['status']}
        rows.append(row)
    report = {'archive_sha256': digest(archive), 'historical_runner_commit': lock['historical_runner_commit'],
              'runner_sha256': digest(ROOT/'scripts/run_tests.py'), 'samples': rows,
              'all_match': all(r['groups_match'] and r['totals_match'] for r in rows)}
    (args.out/'replay.json').write_text(json.dumps(report, indent=2)+'\n')
    for row in rows:
        print(f"{row['sample']}: groups={row['groups_match']} totals={row['totals_match']} ({row['environment']})")
    return 0 if report['all_match'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        raise SystemExit(f'replay evidence: {exc}')
