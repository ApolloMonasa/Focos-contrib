"""把日志段、冻结适配器和验收策略合成为一份报告。"""
from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adapters import prepare_adapter_snapshot, run_adapter
from .config import Criteria, Profile, Shard
from .errors import InputError
from .logs import Section, SplitResult, split_sections
from .ltp import parse_ltp_section
from .observations import check_observations, extract_metrics


@dataclass
class GroupAnalysis:
    shard: Shard
    section: Section | None
    section_status: str
    adapter: str
    items: list[dict]
    extras: list[dict]
    judge_errors: list[str]
    native: dict | None
    counts: dict
    order_matches: bool | None = None
    metrics: list[dict] | None = None


def _empty_counts() -> dict:
    return {'selected_total': 0, 'pass': 0, 'partial': 0, 'fail': 0,
            'missing': 0, 'not_run': 0, 'judge_error': 0}


def _count_items(items: list[dict]) -> dict:
    counts = _empty_counts()
    counts['selected_total'] = len(items)
    for item in items:
        status = item['judgment']
        key = {'PASS': 'pass', 'PARTIAL': 'partial', 'FAIL': 'fail',
               'MISSING': 'missing', 'NOT_RUN': 'not_run',
               'JUDGE_ERROR': 'judge_error'}.get(status)
        if key is None:
            raise InputError(f'unknown judgment: {status}')
        counts[key] += 1
    return counts


def _section_status(split: SplitResult, shard: Shard) -> tuple[str, list[str]]:
    section = split.sections.get((shard.group, shard.libc))
    errors = list(split.section_errors.get((shard.group, shard.libc), []))
    if section is None:
        return 'ABSENT', errors
    if section.truncated:
        return 'TRUNCATED', errors
    if errors:
        return 'INVALID', errors
    return 'COMPLETE', errors


def _not_run_items(profile: Profile, shard: Shard) -> list[dict]:
    return [{'name': item['name'], 'invocation_id': item['invocation_id'],
             'judgment': 'NOT_RUN', 'reasons': ['SECTION_ABSENT']}
            for item in profile.items_for(shard)]


def _map_judge_items(expected: list[dict], items: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    mapped = []
    for invocation in expected:
        name = invocation['name']
        judged = items.get(name)
        if judged is None:
            mapped.append({
                'name': name, 'invocation_id': invocation['invocation_id'],
                'judgment': 'MISSING', 'reasons': ['NO_RESULT'],
            })
            continue
        if judged['pass'] == judged['all'] and judged['all'] > 0:
            judgment = 'PASS'
        elif judged['pass'] > 0:
            judgment = 'PARTIAL'
        else:
            judgment = 'FAIL'
        mapped.append({
            'name': name, 'invocation_id': invocation['invocation_id'],
            'judgment': judgment, 'adapter': judged,
            'reasons': [],
        })
    expected_names = {item['name'] for item in expected}
    extras = [{'name': name, 'adapter': item}
              for name, item in sorted(items.items()) if name not in expected_names]
    return mapped, extras


def _merge_extras(extras: list[dict], observed: list[str],
                  statuses: dict[str, list[str]]) -> list[dict]:
    merged = list(extras)
    known = {item['name'] for item in merged}
    for name in observed:
        if name not in known:
            item = {'name': name, 'source': 'observation_guard'}
            if name in statuses:
                item['observations'] = list(statuses[name])
            merged.append(item)
            known.add(name)
    return sorted(merged, key=lambda item: item['name'])


def _apply_observation_guard(items: list[dict], extras: list[dict],
                             guard, planned: dict) -> tuple[list[dict], list[dict], list[str]]:
    """独立校验原始观测：重复尝试不能被最后一次成功覆盖，未知名称单列。"""
    errors = []
    for line in guard.malformed:
        errors.append(f'MALFORMED_OBSERVATION:{line[:160]}')
    for item in items:
        name = item['name']
        count = guard.counts.get(name, 0)
        planned_count = planned.get(name, 1)
        reasons = item.setdefault('reasons', [])
        if name in guard.statuses:
            item['observations'] = list(guard.statuses[name])
        if count > planned_count:
            item['judgment'] = 'JUDGE_ERROR'
            reason = f'DUPLICATE_OBSERVATION:{count}/{planned_count}'
            reasons.append(reason)
            errors.append(f'{name}:{reason}')
        elif count and count < planned_count:
            item['judgment'] = 'JUDGE_ERROR'
            reason = f'MISSING_PLANNED_OCCURRENCE:{count}/{planned_count}'
            reasons.append(reason)
            errors.append(f'{name}:{reason}')
        elif name in guard.failures and item['judgment'] != 'JUDGE_ERROR':
            item['judgment'] = 'FAIL'
            reasons.append('OBSERVED_FAILURE')
        elif guard.group == 'libctest' and guard.successes.get(name, 0) > 1:
            item['judgment'] = 'JUDGE_ERROR'
            reason = f'DUPLICATE_PASS:{guard.successes[name]}'
            reasons.append(reason)
            errors.append(f'{name}:{reason}')
        elif (guard.group == 'libctest' and item['judgment'] == 'PASS'
              and guard.successes.get(name, 0) == 0):
            item['judgment'] = 'JUDGE_ERROR'
            reason = 'NO_IN_ATTEMPT_PASS'
            reasons.append(reason)
            errors.append(f'{name}:{reason}')
    return items, _merge_extras(extras, guard.extras, guard.statuses), errors


def _analyze_group(profile: Profile, criteria: Criteria, shard: Shard,
                   split: SplitResult, *, adapter_root: Path | None) -> GroupAnalysis:
    group_meta = profile.data['groups'][shard.group]
    section = split.sections.get((shard.group, shard.libc))
    status, section_errors = _section_status(split, shard)
    expected = profile.items_for(shard)

    if group_meta['kind'] == 'ltp':
        if section is None:
            items = _not_run_items(profile, shard)
            return GroupAnalysis(shard, section, status, 'ltp_native', items, [], [],
                                 {'events': {}, 'summaries': {}}, _count_items(items))
        parsed = parse_ltp_section(section.text, expected, line_offset=section.start_line, events=section.events)
        errors = list(parsed.errors)
        if parsed.unattributed:
            errors.append(f'LTP_UNATTRIBUTED_RESULT:{len(parsed.unattributed)}')
        invocation_ids = {inv['name']: inv['invocation_id'] for inv in expected}
        for item in parsed.items:
            item['invocation_id'] = invocation_ids.get(item['name'])
        counts = _count_items(parsed.items)
        return GroupAnalysis(
            shard, section, status, 'ltp_native', parsed.items, parsed.extras, errors,
            parsed.native, counts, parsed.order_matches,
        )

    if section is None:
        items = _not_run_items(profile, shard)
        return GroupAnalysis(shard, section, status, group_meta['adapter'], items, [], [],
                             None, _count_items(items))

    guard = check_observations(
        shard.group, section.text, [item['name'] for item in expected], events=section.events
    )
    planned = group_meta.get('planned_occurrences', {})
    if adapter_root is None:
        raise InputError('adapter snapshot is missing for a non-LTP group')
    output = run_adapter(criteria, shard.group, section.text, adapter_root)
    if output.error:
        items = [{
            'name': item['name'], 'invocation_id': item['invocation_id'],
            'judgment': 'JUDGE_ERROR', 'reasons': [output.error],
        } for item in expected]
        items, extras, guard_errors = _apply_observation_guard(items, [], guard, planned)
        return GroupAnalysis(shard, section, status, group_meta['adapter'], items, extras,
                             [output.error, *guard_errors], None, _count_items(items))
    items, extras = _map_judge_items(expected, output.items)
    items, extras, guard_errors = _apply_observation_guard(items, extras, guard, planned)
    return GroupAnalysis(shard, section, status, group_meta['adapter'], items, extras,
                         guard_errors, None, _count_items(items))


def validate_selection(profile: Profile, shards: list[Shard]):
    if not shards:
        raise InputError('selection is empty')
    seen = set()
    for shard in shards:
        if shard.shard_id in seen:
            raise InputError(f'duplicate shard in selection: {shard.shard_id}')
        seen.add(shard.shard_id)
        if profile.shard(shard.shard_id) != shard:
            raise InputError(f'shard does not belong to profile: {shard.shard_id}')


def _build_report(profile: Profile, criteria: Criteria, shards: list[Shard],
                  analyses: list[GroupAnalysis], *,
                  origin: str, input_info: dict, capture: dict,
                  input_integrity: str, protocol: dict,
                  analysis_role: str, campaign: dict | None) -> dict:
    policy = criteria.data['acceptance']
    reasons = []
    if origin != policy['require_origin']:
        reasons.append(f'ORIGIN_{origin}')
    if capture.get('status') != policy['require_capture_status']:
        reasons.append(f'CAPTURE_{capture.get("status")}')
    if capture.get('exit_code') != policy['require_capture_exit_code']:
        reasons.append(f'CAPTURE_EXIT_{capture.get("exit_code")}')
    if input_integrity != policy['require_input_integrity']:
        reasons.append(f'INPUT_INTEGRITY_{input_integrity}')
    if protocol['status'] != policy['require_protocol']:
        reasons.append(f'PROTOCOL_{protocol["status"]}')
    unattributed = protocol.get('unattributed', [])
    ltp_outside = [item for item in unattributed if item.get('kind') == 'ltp']
    if ltp_outside:
        reasons.append(f'LTP_UNATTRIBUTED_RESULT:outside:{len(ltp_outside)}')
    ltp_unknown = [item for item in unattributed if item.get('kind') == 'ltp_unknown']
    if ltp_unknown:
        reasons.append(f'LTP_UNKNOWN_RESULT:outside:{len(ltp_unknown)}')
    non_ltp_outside = [item for item in unattributed if item.get('kind') == 'non_ltp']
    if non_ltp_outside:
        reasons.append(f'NON_LTP_UNATTRIBUTED_RESULT:outside:{len(non_ltp_outside)}')
    cross_group = [item for item in unattributed if item.get('kind') == 'cross_group']
    if cross_group:
        reasons.append(f'CROSS_GROUP_RESULT:inside:{len(cross_group)}')
    for shard_id, exit_code in sorted(protocol.get('script_exit_codes', {}).items()):
        if exit_code != 0:
            reasons.append(f'SCRIPT_EXIT_{shard_id}:{exit_code}')
    for analysis in analyses:
        if analysis.section_status != policy['require_sections']:
            reasons.append(f'SECTION_{analysis.section_status}:{analysis.shard.shard_id}')
        if any(item['judgment'] != policy['require_all_judgments'] for item in analysis.items):
            failed = [item for item in analysis.items if item['judgment'] != policy['require_all_judgments']]
            reasons.append(
                f'JUDGMENT_NOT_{policy["require_all_judgments"]}:'
                f'{analysis.shard.shard_id}:{len(failed)}'
            )
        if analysis.extras and policy['forbid_extras']:
            reasons.append(f'EXTRA_ITEMS:{analysis.shard.shard_id}:{len(analysis.extras)}')
        if analysis.judge_errors and policy['forbid_judge_errors']:
            reasons.append(f'JUDGE_ERROR:{analysis.shard.shard_id}:{len(analysis.judge_errors)}')
        if analysis.shard.group == 'ltp':
            native = analysis.native or {}
            # 已有汇总的矛盾证据属于采集/判分事实，J1 也不能忽略；
            # 只有“缺汇总/汇总不完整”才是 J2 的额外要求。
            if native.get('summary_mismatch'):
                reasons.append(
                    f'LTP_SUMMARY_MISMATCH:{analysis.shard.shard_id}:'
                    f'{native["summary_mismatch"]}'
                )
            if policy['ltp_summary_required']:
                if native.get('summary_missing'):
                    reasons.append(
                        f'LTP_SUMMARY_MISSING:{analysis.shard.shard_id}:'
                        f'{native["summary_missing"]}'
                    )
                if native.get('summary_incomplete'):
                    reasons.append(
                        f'LTP_SUMMARY_INCOMPLETE:{analysis.shard.shard_id}:'
                        f'{native["summary_incomplete"]}'
                    )
            native_unattributed = native.get('unattributed', [])
            if native_unattributed:
                reasons.append(
                    f'LTP_UNATTRIBUTED_RESULT:{analysis.shard.shard_id}:'
                    f'{len(native_unattributed)}'
                )
            unknown_native = [
                item for item in native_unattributed
                if item.get('kind') == 'ltp_unknown'
            ]
            if unknown_native:
                reasons.append(
                    f'LTP_UNKNOWN_RESULT:{analysis.shard.shard_id}:'
                    f'{len(unknown_native)}'
                )
            if analysis.order_matches is False:
                reasons.append(f'LTP_ORDER_MISMATCH:{analysis.shard.shard_id}')
    acceptance = 'PASS' if not reasons else 'BLOCKED'
    totals = _empty_counts()
    for analysis in analyses:
        totals['selected_total'] += analysis.counts['selected_total']
        for key in ('pass', 'partial', 'fail', 'missing', 'not_run', 'judge_error'):
            totals[key] += analysis.counts[key]
    totals['observed_pass'] = totals['pass']
    totals['accepted_pass'] = totals['selected_total'] if acceptance == 'PASS' else 0

    groups = []
    for analysis in analyses:
        group = {
            'group': analysis.shard.group,
            'libc': analysis.shard.libc,
            'shard_id': analysis.shard.shard_id,
            'section': analysis.section_status,
            'adapter': analysis.adapter,
            'items': analysis.items,
            'extras': analysis.extras,
            'judge_errors': analysis.judge_errors,
            'counts': analysis.counts,
        }
        if analysis.native is not None:
            group['native'] = analysis.native
            group['order_matches'] = analysis.order_matches
        if analysis.metrics is not None:
            group['metrics'] = analysis.metrics
        groups.append(group)

    report = {
        'schema_version': 2,
        'kind': 'test001_report',
        'report_id': str(uuid.uuid4()),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'origin': origin,
        'subject': capture['subject'],
        'platform': capture['platform'],
        'input': input_info,
        'profile': {
            'profile_id': profile.profile_id,
            'revision': profile.revision,
            'profile_hash': profile.profile_hash,
            'source_file_sha256': profile.file_sha256,
            'invocations_sha256': profile.data['invocations']['sha256'],
            'invocations_total': profile.data['invocations']['total_selected'],
        },
        'criteria': {
            'criteria_id': criteria.criteria_id,
            'revision': criteria.revision,
            'criteria_hash': criteria.criteria_hash,
            'source_file_sha256': criteria.file_sha256,
            'implementation_manifest_sha256': criteria.data['implementation']['sha256'],
        },
        'capture': capture,
        'input_integrity': input_integrity,
        'protocol': protocol,
        'selection': [
            {'shard_id': shard.shard_id, 'group': shard.group, 'libc': shard.libc,
             'selected_total': shard.selected_total}
            for shard in shards
        ],
        'groups': groups,
        'totals': totals,
        'acceptance': {'status': acceptance, 'reasons': reasons},
        'analysis_role': analysis_role,
        'campaign': campaign,
        'implementation': {
            'manifest_sha256': criteria.data['implementation']['sha256'],
            'files': criteria.implementation_files,
        },
    }
    return report


def _protocol(split: SplitResult, shards: list[Shard], *, origin: str) -> dict:
    errors = list(split.errors)
    selected = {(shard.group, shard.libc) for shard in shards}
    outside = sorted(
        f'{group}-{libc}' for group, libc in split.sections if (group, libc) not in selected
    )
    if origin == 'recorded_capture' and outside:
        errors.append('unselected sections in run package: ' + ', '.join(outside))
    status = split.protocol
    if errors and status == 'VALID':
        status = 'ERROR'
    return {'status': status, 'errors': errors, 'unselected_sections': outside,
            'unattributed': split.unattributed}


def analyze_log(profile: Profile, criteria: Criteria, shards: list[Shard],
                log_bytes: bytes, *, origin: str, input_info: dict,
                capture: dict, input_integrity: str,
                analysis_role: str, campaign: dict | None) -> dict:
    criteria.check_profile(profile)
    validate_selection(profile, shards)
    try:
        text = log_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = log_bytes.decode('utf-8', 'replace')
    split_obj = split_sections(text)
    need_adapters = any(
        profile.data['groups'][shard.group]['kind'] != 'ltp' for shard in shards
    )
    analyses = []
    with tempfile.TemporaryDirectory(prefix='test001-adapters-') as temporary:
        adapter_root = None
        if need_adapters:
            adapter_root = prepare_adapter_snapshot(criteria, profile, Path(temporary))
        for shard in shards:
            analysis = _analyze_group(
                profile, criteria, shard, split_obj,
                adapter_root=adapter_root,
            )
            if analysis.section is not None and shard.group in ('libcbench', 'lmbench'):
                analysis.metrics = extract_metrics(
                    shard.group, analysis.section.text,
                    [item['name'] for item in profile.items_for(shard)],
                    line_offset=analysis.section.start_line, events=analysis.section.events,
                )
            analyses.append(analysis)
    protocol = _protocol(split_obj, shards, origin=origin)
    if input_info.get('script_exit_codes'):
        protocol['script_exit_codes'] = dict(input_info['script_exit_codes'])
    return _build_report(
        profile, criteria, shards, analyses, origin=origin,
        input_info=input_info, capture=capture, input_integrity=input_integrity,
        protocol=protocol, analysis_role=analysis_role, campaign=campaign,
    )
