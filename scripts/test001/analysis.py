"""把日志段、冻结适配器和验收策略合成为一份报告。"""

from __future__ import annotations

import tempfile
from bisect import bisect_left, bisect_right
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adapters import prepare_adapter_snapshot, run_adapter
from .config import Criteria, Profile, Shard
from .errors import InputError
from .logs import Section, SplitResult, split_sections
from .ltp import parse_ltp_section
from .proofs import build_plan
from .fsutil import sha256_bytes


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
    diagnostics: list[dict] | None = None
    evidence: list[dict] | None = None
    event_ledger: list[dict] | None = None


def _empty_counts() -> dict:
    return {
        'selected_total': 0,
        'pass': 0,
        'partial': 0,
        'fail': 0,
        'missing': 0,
        'not_run': 0,
        'judge_error': 0,
    }


def _count_items(items: list[dict]) -> dict:
    counts = _empty_counts()
    counts['selected_total'] = len(items)
    for item in items:
        status = item['judgment']
        key = {
            'PASS': 'pass',
            'PARTIAL': 'partial',
            'FAIL': 'fail',
            'MISSING': 'missing',
            'NOT_RUN': 'not_run',
            'JUDGE_ERROR': 'judge_error',
        }.get(status)
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
    return [
        {
            'name': item['name'],
            'invocation_id': item['invocation_id'],
            'judgment': 'NOT_RUN',
            'reasons': ['SECTION_ABSENT'],
        }
        for item in profile.items_for(shard)
    ]


def _map_judge_items(expected: list[dict], items: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    mapped = []
    for invocation in expected:
        name = invocation['name']
        judged = items.get(name)
        if judged is None:
            mapped.append(
                {
                    'name': name,
                    'invocation_id': invocation['invocation_id'],
                    'judgment': 'MISSING',
                    'reasons': ['NO_RESULT'],
                }
            )
            continue
        if judged['pass'] == judged['all'] and judged['all'] > 0:
            judgment = 'PASS'
        elif judged['pass'] > 0:
            judgment = 'PARTIAL'
        else:
            judgment = 'FAIL'
        mapped.append(
            {
                'name': name,
                'invocation_id': invocation['invocation_id'],
                'judgment': judgment,
                'adapter': judged,
                'reasons': [],
            }
        )
    expected_names = {item['name'] for item in expected}
    extras = [
        {'name': name, 'adapter': item}
        for name, item in sorted(items.items())
        if name not in expected_names
    ]
    return mapped, extras


def _merge_extras(
    extras: list[dict], observed: list[str], statuses: dict[str, list[str]]
) -> list[dict]:
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


def _certify_basic(plan, criteria, adapter_root):
    for name, claims in plan.claims.items():
        if name not in plan.expected or len(claims) != 1:
            continue
        claim = claims[0]
        if not claim.complete or not claim.positive or claim.reasons:
            continue
        lines = set(claim.lines)
        text = '\n'.join(e.raw.removesuffix('\r') for e in plan.records if e.line in lines)
        result = run_adapter(criteria, 'basic', text, adapter_root)
        item = result.items.get(name)
        if result.error or item is None or not item['all'] or item['pass'] != item['all']:
            claim.reasons.append('SCOPED_JUDGE_FAILED')
        else:
            claim.scoped_check = {
                'input_sha256': sha256_bytes(text.encode()),
                'adapter_sha256': criteria.adapter('basic')['sha256'],
                'all': item['all'],
                'pass': item['pass'],
            }


def _apply_evidence(items, extras, plan, planned, shard):
    errors = [f'MALFORMED_OBSERVATION:line {d["line"]}:{d["message"]}' for d in plan.malformed]
    for item in items:
        name = item['name']
        if plan.group == 'basic' and item.get('adapter'):
            for claim in plan.claims.get(name, []):
                scoped = claim.scoped_check
                if scoped and any(scoped[key] != item['adapter'][key] for key in ('all', 'pass')):
                    claim.reasons.append('SCOPED_OBSERVATION_MISMATCH')
        proof = plan.proof_for(name, planned.get(name, 1))
        proof.update(shard_id=shard.shard_id, invocation_id=item['invocation_id'])
        item['proof'] = proof
        if name in plan.statuses:
            item['observations'] = plan.statuses[name]
        count = plan.counts.get(name, 0)
        if count > planned.get(name, 1):
            reason = f'DUPLICATE_OBSERVATION:{count}/{planned.get(name, 1)}'
            item['judgment'] = 'JUDGE_ERROR'
            item['reasons'].append(reason)
            errors.append(name + ':' + reason)
        if plan.successes.get(name, 0) > 1:
            reason = f'DUPLICATE_PASS:{plan.successes[name]}'
            item['judgment'] = 'JUDGE_ERROR'
            item['reasons'].append(reason)
            errors.append(name + ':' + reason)
        if item['judgment'] == 'PASS' and not proof['valid']:
            item['judgment'] = 'JUDGE_ERROR'
            reason = 'UNPROVEN_ADAPTER_PASS'
            claims = plan.claims.get(name, [])
            if (
                len(claims) == planned.get(name, 1)
                and all(c.complete and not c.reasons for c in claims)
                and any(not c.positive for c in claims)
            ):
                item['judgment'] = 'FAIL'
                reason = 'OBSERVED_FAILURE'
            if plan.counts.get(name, 0) > planned.get(name, 1):
                reason = f'DUPLICATE_OBSERVATION:{plan.counts[name]}/{planned.get(name, 1)}'
            if plan.group == 'libctest' and not plan.successes.get(name, 0):
                reason = 'NO_IN_ATTEMPT_PASS'
            if plan.successes.get(name, 0) > 1:
                reason = f'DUPLICATE_PASS:{plan.successes[name]}'
            item['reasons'].append(reason)
            if reason != 'OBSERVED_FAILURE':
                errors.append(name + ':' + reason)
    return items, _merge_extras(extras, plan.extras, plan.statuses), errors


def _native_proofs(parsed, section, shard):
    records = {e.line: e for e in section.events}
    positions = list(records)
    starts = sorted(
        a['run_line']
        for i in [*parsed.items, *parsed.extras]
        for a in i.get('attempts', [i])
        if a.get('run_line') is not None
    )
    ledger = {}
    for e in section.events:
        ledger[e.line] = {
            'line': e.line,
            'disposition': 'information',
            'owner': None,
            'reason': 'opaque_body',
        }
    for item in [*parsed.items, *parsed.extras]:
        attempts = item.get('attempts', [item])
        lines = []
        valid = len(attempts) == 1 and item.get('judgment') == 'PASS'
        for attempt in attempts:
            start = attempt.get('run_line')
            end = attempt.get('end_line')
            begin = records.get(start)
            finish = records.get(end)
            scope = (
                begin is not None
                and finish is not None
                and begin.kind == 'ltp_start'
                and finish.kind == 'ltp_end'
                and begin.fields[0] == item['name'] == finish.fields[0]
                and finish.fields[1] == '0'
            )
            valid = valid and scope and not attempt.get('summary_mismatch')
            if start is not None:
                following = bisect_right(starts, start)
                cutoff = (
                    end
                    if end is not None
                    else (starts[following] - 1 if following < len(starts) else positions[-1])
                )
                for e in section.events[
                    bisect_left(positions, start) : bisect_right(positions, cutoff)
                ]:
                    lines.append(e.line)
                    row = ledger[e.line]
                    if row['owner'] not in (None, item['name']):
                        row.update(disposition='error', reason='conflicting_owner')
                        valid = False
                        continue
                    row.update(disposition='consumed', owner=item['name'], reason='invocation')
                    if not e.canonical or e.groups and 'ltp' not in e.groups:
                        row.update(disposition='error', reason='invalid_invocation_record')
                        valid = False
        item['proof'] = {
            'valid': valid and bool(lines),
            'group': 'ltp',
            'name': item['name'],
            'event_lines': sorted(set(lines)),
            'shard_id': shard.shard_id,
            'invocation_id': item.get('invocation_id'),
        }
        if item.get('judgment') == 'PASS' and not item['proof']['valid']:
            item['native_judgment'] = 'PASS'
            item['judgment'] = 'JUDGE_ERROR'
            item['reasons'].append('UNPROVEN_NATIVE_PASS')
    for e in section.events:
        row = ledger[e.line]
        if row['disposition'] == 'information' and (
            e.groups or not e.canonical or e.kind in ('group', 'invalid_group')
        ):
            if e.kind == 'ltp_result' and e.fields[0] in ('TINFO', 'TDEBUG'):
                continue
            row.update(disposition='error', reason='unowned_invocation_record')
    return list(ledger.values())


def _analyze_group(
    profile: Profile,
    criteria: Criteria,
    shard: Shard,
    split: SplitResult,
    *,
    adapter_root: Path | None,
) -> GroupAnalysis:
    group_meta = profile.data['groups'][shard.group]
    section = split.sections.get((shard.group, shard.libc))
    status, section_errors = _section_status(split, shard)
    expected = profile.items_for(shard)

    if group_meta['kind'] == 'ltp':
        if section is None:
            items = _not_run_items(profile, shard)
            return GroupAnalysis(
                shard,
                section,
                status,
                'ltp_native',
                items,
                [],
                [],
                {'events': {}, 'summaries': {}},
                _count_items(items),
            )
        parsed = parse_ltp_section(
            section.text, expected, line_offset=section.start_line, events=section.events
        )
        errors = list(parsed.errors)
        if parsed.unattributed:
            errors.append(f'LTP_UNATTRIBUTED_RESULT:{len(parsed.unattributed)}')
        invocation_ids = {inv['name']: inv['invocation_id'] for inv in expected}
        for item in parsed.items:
            item['invocation_id'] = invocation_ids.get(item['name'])
        ledger = _native_proofs(parsed, section, shard)
        counts = _count_items(parsed.items)
        return GroupAnalysis(
            shard,
            section,
            status,
            'ltp_native',
            parsed.items,
            parsed.extras,
            errors,
            parsed.native,
            counts,
            parsed.order_matches,
            event_ledger=ledger,
        )

    if section is None:
        items = _not_run_items(profile, shard)
        return GroupAnalysis(
            shard, section, status, group_meta['adapter'], items, [], [], None, _count_items(items)
        )

    plan = build_plan(
        shard.group,
        section.text,
        [item['name'] for item in expected],
        events=section.events,
        line_offset=section.start_line,
    )
    if shard.group == 'basic':
        _certify_basic(plan, criteria, adapter_root)
    planned = group_meta.get('planned_occurrences', {})
    if adapter_root is None:
        raise InputError('adapter snapshot is missing for a non-LTP group')
    output = run_adapter(criteria, shard.group, section.text, adapter_root)
    if output.error:
        items = [
            {
                'name': item['name'],
                'invocation_id': item['invocation_id'],
                'judgment': 'JUDGE_ERROR',
                'reasons': [output.error],
            }
            for item in expected
        ]
        items, extras, guard_errors = _apply_evidence(items, [], plan, planned, shard)
        return GroupAnalysis(
            shard,
            section,
            status,
            group_meta['adapter'],
            items,
            extras,
            [output.error, *guard_errors],
            None,
            _count_items(items),
            diagnostics=plan.malformed,
            evidence=plan.evidence,
            metrics=plan.metrics,
            event_ledger=list(plan.ledger.values()),
        )
    items, extras = _map_judge_items(expected, output.items)
    items, extras, guard_errors = _apply_evidence(items, extras, plan, planned, shard)
    return GroupAnalysis(
        shard,
        section,
        status,
        group_meta['adapter'],
        items,
        extras,
        guard_errors,
        None,
        _count_items(items),
        diagnostics=plan.malformed,
        evidence=plan.evidence,
        metrics=plan.metrics,
        event_ledger=list(plan.ledger.values()),
    )


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


def _build_report(
    profile: Profile,
    criteria: Criteria,
    shards: list[Shard],
    analyses: list[GroupAnalysis],
    *,
    origin: str,
    input_info: dict,
    capture: dict,
    input_integrity: str,
    protocol: dict,
    analysis_role: str,
    campaign: dict | None,
) -> dict:
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
        ledger = {row['line']: row for row in (analysis.event_ledger or [])}
        if (
            len(ledger) != len(analysis.event_ledger or [])
            or set(ledger)
            != {e.line for e in (analysis.section.events if analysis.section else ())}
            or any(row['disposition'] not in ('consumed', 'information') for row in ledger.values())
        ):
            reasons.append('EVENT_OWNERSHIP_ERROR:' + analysis.shard.shard_id)
        for item in analysis.items:
            if item['judgment'] != 'PASS':
                continue
            proof = item.get('proof', {})
            lines = proof.get('event_lines', [])
            if (
                not proof.get('valid')
                or not lines
                or proof.get('name') != item['name']
                or proof.get('group') != analysis.shard.group
                or proof.get('shard_id') != analysis.shard.shard_id
                or proof.get('invocation_id') != item['invocation_id']
                or any(
                    line not in ledger
                    or ledger[line]['disposition'] != 'consumed'
                    or ledger[line]['owner'] != 'context:' + analysis.shard.group
                    for line in proof.get('context_lines', [])
                )
                or any(
                    line not in ledger
                    or ledger[line]['disposition'] != 'consumed'
                    or ledger[line]['owner'] != item['name']
                    for line in lines
                )
            ):
                reasons.append('UNPROVEN_PASS:' + item['invocation_id'])
        if analysis.section_status != policy['require_sections']:
            reasons.append(f'SECTION_{analysis.section_status}:{analysis.shard.shard_id}')
        if any(item['judgment'] != policy['require_all_judgments'] for item in analysis.items):
            failed = [
                item
                for item in analysis.items
                if item['judgment'] != policy['require_all_judgments']
            ]
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
                    f'LTP_SUMMARY_MISMATCH:{analysis.shard.shard_id}:{native["summary_mismatch"]}'
                )
            if policy['ltp_summary_required']:
                if native.get('summary_missing'):
                    reasons.append(
                        f'LTP_SUMMARY_MISSING:{analysis.shard.shard_id}:{native["summary_missing"]}'
                    )
                if native.get('summary_incomplete'):
                    reasons.append(
                        f'LTP_SUMMARY_INCOMPLETE:{analysis.shard.shard_id}:'
                        f'{native["summary_incomplete"]}'
                    )
            native_unattributed = native.get('unattributed', [])
            if native_unattributed:
                reasons.append(
                    f'LTP_UNATTRIBUTED_RESULT:{analysis.shard.shard_id}:{len(native_unattributed)}'
                )
            unknown_native = [
                item for item in native_unattributed if item.get('kind') == 'ltp_unknown'
            ]
            if unknown_native:
                reasons.append(
                    f'LTP_UNKNOWN_RESULT:{analysis.shard.shard_id}:{len(unknown_native)}'
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
        group['event_ledger'] = analysis.event_ledger or []
        if analysis.native is not None:
            group['native'] = analysis.native
            group['order_matches'] = analysis.order_matches
        if analysis.diagnostics is not None:
            group['diagnostics'] = analysis.diagnostics
        if analysis.evidence is not None:
            group['evidence'] = analysis.evidence
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
            {
                'shard_id': shard.shard_id,
                'group': shard.group,
                'libc': shard.libc,
                'selected_total': shard.selected_total,
            }
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
    return {
        'status': status,
        'errors': errors,
        'unselected_sections': outside,
        'unattributed': split.unattributed,
    }


def _source_ledger(split, analyses):
    owned = {row['line']: dict(row) for a in analyses for row in (a.event_ledger or [])}
    sections = sorted(split.sections.values(), key=lambda section: section.start_line)
    starts = [section.start_line for section in sections]
    boundaries = {
        line: section
        for section in sections
        for line in (section.start_line, section.end_line)
        if line is not None
    }
    rows = []
    selected = {(a.shard.group, a.shard.libc) for a in analyses}
    for e in split.events:
        if e.line in owned:
            rows.append(owned[e.line])
            continue
        row = {'line': e.line, 'disposition': 'information', 'owner': None, 'reason': 'opaque_body'}
        if e.kind == 'group' and e.line in boundaries:
            section = boundaries[e.line]
            row.update(
                disposition='consumed',
                owner='group:' + section.group + '-' + section.libc,
                reason='group_boundary',
            )
        else:
            index = bisect_right(starts, e.line) - 1
            section = sections[index] if index >= 0 else None
            inside = (
                section is not None
                and section.start_line < e.line
                and (section.end_line is None or e.line < section.end_line)
            )
            if inside and (section.group, section.libc) not in selected:
                row.update(
                    owner='group:' + section.group + '-' + section.libc, reason='outside_selection'
                )
            elif (
                e.groups
                and not (e.kind == 'ltp_result' and e.fields[0] in ('TINFO', 'TDEBUG'))
                or not e.canonical
                or e.kind in ('group', 'invalid_group')
            ):
                row.update(disposition='error', reason='unowned_source_event')
        rows.append(row)
    return rows


def analyze_log(
    profile: Profile,
    criteria: Criteria,
    shards: list[Shard],
    log_bytes: bytes,
    *,
    origin: str,
    input_info: dict,
    capture: dict,
    input_integrity: str,
    analysis_role: str,
    campaign: dict | None,
) -> dict:
    criteria.check_profile(profile)
    validate_selection(profile, shards)
    try:
        text = log_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = log_bytes.decode('utf-8', 'replace')
    split_obj = split_sections(text)
    need_adapters = any(profile.data['groups'][shard.group]['kind'] != 'ltp' for shard in shards)
    analyses = []
    with tempfile.TemporaryDirectory(prefix='test001-adapters-') as temporary:
        adapter_root = None
        if need_adapters:
            adapter_root = prepare_adapter_snapshot(criteria, profile, Path(temporary))
        for shard in shards:
            analysis = _analyze_group(
                profile,
                criteria,
                shard,
                split_obj,
                adapter_root=adapter_root,
            )
            analyses.append(analysis)
    protocol = _protocol(split_obj, shards, origin=origin)
    protocol['event_ledger'] = _source_ledger(split_obj, analyses)
    if any(row['disposition'] == 'error' for row in protocol['event_ledger']):
        protocol['status'] = 'ERROR'
    if input_info.get('script_exit_codes'):
        protocol['script_exit_codes'] = dict(input_info['script_exit_codes'])
    return _build_report(
        profile,
        criteria,
        shards,
        analyses,
        origin=origin,
        input_info=input_info,
        capture=capture,
        input_integrity=input_integrity,
        protocol=protocol,
        analysis_role=analysis_role,
        campaign=campaign,
    )
