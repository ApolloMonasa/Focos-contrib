"""TEST-001 分析入口；A 阶段只实现 analyze。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyze_log, validate_selection
from .config import load_catalog, load_criteria, load_profile
from .errors import InputError, PublishError
from .fsutil import create_output_dir, read_regular_bytes, sha256_bytes
from .report import write_report
from .runpack import load_run_package
from .strictjson import StrictJSONError, loads


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='test001',
        description='TEST-001 阶段分析器：A 阶段只提供 analyze。',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    analyze = subparsers.add_parser(
        'analyze', help='分析外部日志或已校验运行包，生成独立报告',
    )
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument('--log', type=Path, help='外部串口日志；来源固定为 external_log')
    source.add_argument('--run', type=Path, help='已校验运行包目录')
    analyze.add_argument('--profile', help='仓库内冻结 PROFILE ID；--run 时禁止')
    analyze.add_argument('--criteria', required=True, help='仓库内冻结 CRITERIA ID')
    analyze.add_argument('--group', action='append', default=[], help='显式组名；可重复')
    analyze.add_argument('--libc', choices=('musl', 'glibc'), help='显式 libc')
    analyze.add_argument('--shard', help='显式 shard ID；不能与组/libc 冲突')
    analyze.add_argument('--out', required=True, type=Path, help='独占新建的报告目录')
    analyze.add_argument('--require-pass', action='store_true',
                         help='报告为 BLOCKED 时退出 1')
    return parser


def _external_selection(profile, groups: list[str], libc: str | None, shard_id: str | None):
    if shard_id is not None:
        shard = profile.shard(shard_id)
        if groups and groups != [shard.group]:
            raise InputError('--shard conflicts with --group')
        if libc is not None and libc != shard.libc:
            raise InputError('--shard conflicts with --libc')
        return [shard]
    if not groups:
        raise InputError('--log requires --group or --shard')
    if libc is None:
        raise InputError('--log requires --libc')
    selected = []
    for group in groups:
        shards = profile.shards_for(group, libc)
        if len(shards) != 1:
            raise InputError(f'group {group}-{libc} requires an explicit --shard')
        selected.append(shards[0])
    return selected


def _read_capture_profile(path: Path) -> str:
    raw = read_regular_bytes(path / 'capture.json', label='capture manifest',
                             max_bytes=1024 * 1024)
    try:
        data = loads(raw, label='capture manifest')
    except StrictJSONError as exc:
        raise InputError(str(exc)) from exc
    if not isinstance(data, dict):
        raise InputError('capture manifest must be an object')
    profile = data.get('profile')
    if not isinstance(profile, dict) or not isinstance(profile.get('profile_id'), str):
        raise InputError('capture manifest has no profile id')
    return profile['profile_id']


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = _root()
    try:
        catalog = load_catalog(root)
        criteria = load_criteria(root, args.criteria, catalog)
        if args.command != 'analyze':
            raise InputError(f'only analyze is implemented; got {args.command}')

        if args.log is not None:
            if args.profile is None:
                raise InputError('--log requires --profile')
            profile = load_profile(root, args.profile, catalog)
            shards = _external_selection(profile, args.group, args.libc, args.shard)
            limit = profile.data['environment']['serial_limit_bytes']
            raw = read_regular_bytes(args.log, label='external log', max_bytes=limit)
            input_info = {
                'kind': 'external_log', 'sha256': sha256_bytes(raw), 'size': len(raw),
                'source_hint': str(args.log), 'source_copy': 'source.log',
            }
            capture = {'status': 'NOT_RECORDED', 'exit_code': None,
                       'subject': 'unknown', 'platform': 'unknown'}
            input_integrity = 'UNVERIFIED'
            analysis_role = 'standalone'
            campaign = None
            origin = 'external_log'
            source_raw = raw
        else:
            if args.profile is not None or args.group or args.libc or args.shard:
                raise InputError('--run does not accept --profile/--group/--libc/--shard')
            profile_id = _read_capture_profile(args.run)
            profile = load_profile(root, profile_id, catalog)
            run = load_run_package(args.run, profile)
            shards = [profile.shard(shard_id) for shard_id in run.selection]
            capture = {
                'status': run.capture['capture']['status'],
                'exit_code': run.capture['capture']['exit_code'],
                'subject': run.capture['subject'],
                'platform': run.capture['platform'],
            }
            input_info = {
                'kind': 'run_package', 'sha256': run.serial_sha256,
                'size': len(run.serial_bytes), 'source_hint': str(args.run),
                'capture_sha256': run.capture_sha256, 'run_id': run.capture['run_id'],
                'build_manifest_sha256': run.capture['build']['manifest_sha256'],
                'firmware_sha256': run.firmware_sha256,
            }
            input_integrity = run.capture['input_integrity']
            analysis_role = 'standalone' if run.capture['campaign'] is None else 'reanalysis'
            campaign = run.capture['campaign']
            origin = 'recorded_capture'
            source_raw = None

        criteria.check_profile(profile)
        validate_selection(profile, shards)
        serial_bytes = raw if args.log is not None else run.serial_bytes
        output = create_output_dir(args.out)
        report = analyze_log(
            profile, criteria, shards, serial_bytes,
            origin=origin, input_info=input_info, capture=capture,
            input_integrity=input_integrity, analysis_role=analysis_role,
            campaign=campaign,
        )
        write_report(output, report, source_raw=source_raw)
        if args.require_pass and report['acceptance']['status'] != 'PASS':
            return 1
        return 0
    except InputError as exc:
        print(f'test001: {exc}', file=sys.stderr)
        return 2
    except PublishError as exc:
        print(f'test001: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:  # 工具自身失败不能伪装成输入错误或验收通过
        print(f'test001: internal error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
