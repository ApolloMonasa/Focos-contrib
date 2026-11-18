"""LTP 原生结果适配器：先保留每次尝试和原生计数，再给调用判分。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .protocol import Event, lex, RESULT_FLAGS, UNATTRIBUTED_FLAGS, evidence


@dataclass
class Attempt:
    name: str
    run_line: int
    line_offset: int = 0
    end_line: int | None = None
    exit_code: int | None = None
    complete: bool = False
    records: list[Event] = field(default_factory=list)
    events: dict[str, int] = field(default_factory=dict)
    summaries: list[dict[str, int]] = field(default_factory=list)
    summary_incomplete: bool = False
    summary_mismatch: bool = False
    unknown_flags: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def tpass(self) -> int:
        return self.events.get('TPASS', 0)

    def result(self) -> dict:
        reasons = []
        if self.parse_errors:
            judgment = 'JUDGE_ERROR'
            reasons.extend(self.parse_errors)
        elif not self.complete:
            judgment = 'MISSING'
            reasons.append('INCOMPLETE_ATTEMPT')
        elif self.exit_code is None:
            judgment = 'JUDGE_ERROR'
            reasons.append('INVALID_EXIT')
        elif self.exit_code != 0:
            judgment = 'FAIL'
            reasons.append(f'EXIT_{self.exit_code}')
        else:
            native_failures = []
            for flag, label in (
                ('TFAIL', 'TFAIL'),
                ('TBROK', 'TBROK'),
                ('TCONF', 'TCONF'),
                ('TWARN', 'TWARN'),
            ):
                if self.events.get(flag, 0):
                    native_failures.append(f'{label}_{self.events[flag]}')
            if native_failures:
                judgment = 'FAIL'
                reasons.extend(native_failures)
            elif self.tpass == 0:
                judgment = 'FAIL'
                reasons.append('NO_TPASS')
            else:
                judgment = 'PASS'
        return {
            'name': self.name,
            'judgment': judgment,
            'reasons': reasons,
            'exit_code': self.exit_code,
            'complete': self.complete,
            'run_line': self.run_line,
            'end_line': self.end_line,
            'run_section_line': self.run_line - self.line_offset,
            'end_section_line': None if self.end_line is None else self.end_line - self.line_offset,
            'events': dict(sorted(self.events.items())),
            'summaries': list(self.summaries),
            'summary_incomplete': self.summary_incomplete,
            'summary_mismatch': self.summary_mismatch,
            'unknown_flags': sorted(self.unknown_flags),
            'evidence': [
                evidence(e, self.line_offset)
                for e in self.records
                if e.kind
                in ('ltp_result', 'ltp_unknown', 'summary', 'summary_field', 'legacy_summary')
            ],
        }


@dataclass
class LtpParse:
    items: list[dict]
    extras: list[dict]
    errors: list[str]
    native: dict
    order_matches: bool
    summary_missing: int
    summary_incomplete: int
    summary_mismatch: int
    unattributed: list[dict]


def _parse_attempt(attempt: Attempt):
    """只消费共享词法的结构字段；汇总块只允许一次，不叠加重复计数。"""
    counts = {flag: 0 for flag in RESULT_FLAGS}
    unknown = set()
    modern = []
    legacy = {}
    active = None
    for event in attempt.records:
        if event.kind == 'ltp_result':
            counts[event.fields[0]] += 1
        elif event.kind == 'ltp_unknown':
            unknown.add(event.fields[0])
        if event.kind == 'summary':
            active = {}
            modern.append(active)
        elif event.kind == 'summary_field' and active is not None:
            key, value = event.fields
            if key in active:
                attempt.parse_errors.append(f'DUPLICATE_SUMMARY_FIELD:{key}')
            active[key] = int(value)
        elif event.kind == 'legacy_summary':
            key, value = event.fields
            if key in legacy:
                attempt.parse_errors.append(f'DUPLICATE_LEGACY_FIELD:{key}')
            legacy[key] = int(value)
            active = None
        elif event.text:
            active = None
    if len(modern) > 1:
        attempt.parse_errors.append('DUPLICATE_SUMMARY')
    if modern and legacy:
        attempt.parse_errors.append('MIXED_SUMMARY_FORMATS')
    for values in modern:
        if not values:
            attempt.parse_errors.append('EMPTY_SUMMARY')
        if set(values) != {'passed', 'failed', 'broken', 'skipped', 'warnings'}:
            attempt.summary_incomplete = True
    if modern:
        attempt.summaries = modern
    elif legacy:
        attempt.summary_incomplete = True
        attempt.summaries = [
            {
                dst: legacy[src]
                for src, dst in (
                    ('Total Tests', 'total'),
                    ('Passed', 'passed'),
                    ('Failed', 'failed'),
                    ('Not Supported', 'skipped'),
                )
                if src in legacy
            }
        ]
    if legacy:
        if set(legacy) != {'Total Tests', 'Passed', 'Failed', 'Not Supported'}:
            attempt.parse_errors.append('INCOMPLETE_LEGACY_SUMMARY')
        elif legacy['Total Tests'] != legacy['Passed'] + legacy['Failed'] + legacy[
            'Not Supported'
        ] or legacy['Total Tests'] != sum(counts[f] for f in UNATTRIBUTED_FLAGS):
            attempt.summary_mismatch = True
            attempt.parse_errors.append('LEGACY_TOTAL_MISMATCH')
    attempt.events = {flag: count for flag, count in counts.items() if count}
    attempt.unknown_flags = sorted(unknown)
    if unknown:
        attempt.parse_errors.append('UNKNOWN_RESULT_FLAG:' + ','.join(sorted(unknown)))
    for summary in attempt.summaries:
        for key, flag in (
            ('passed', 'TPASS'),
            ('failed', 'TFAIL'),
            ('broken', 'TBROK'),
            ('skipped', 'TCONF'),
            ('warnings', 'TWARN'),
        ):
            if key in summary and summary[key] != counts[flag]:
                attempt.summary_mismatch = True


def parse_ltp_section(
    text: str, expected: list[dict], *, line_offset: int = 0, events=None
) -> LtpParse:
    """按固定版本的原生标志解析一个 ltp-<libc> 段。"""
    expected_names = [item['name'] for item in expected]
    expected_set = set(expected_names)
    attempts: dict[str, list[Attempt]] = {}
    errors: list[str] = []
    observed_order: list[str] = []
    unattributed: list[dict] = []
    current: Attempt | None = None
    records = lex(text, line_offset=line_offset) if events is None else events

    def finish(attempt: Attempt | None):
        if attempt is None:
            return
        attempts.setdefault(attempt.name, []).append(attempt)

    for event in records:
        line_number = event.line
        if event.kind == 'ltp_start':
            name = event.fields[0]
            if current is not None:
                errors.append(
                    f'line {line_number}: RUN LTP CASE before previous case ended: {name}'
                )
                current.complete = False
                finish(current)
                current = None
            if name not in expected_set:
                errors.append(f'line {line_number}: unknown LTP case: {name}')
            if attempts.get(name):
                errors.append(f'line {line_number}: duplicate LTP case: {name}')
            current = Attempt(name=name, run_line=line_number, line_offset=line_offset)
            observed_order.append(name)
            continue
        if event.kind == 'ltp_end':
            name, raw_exit = event.fields
            if current is None:
                errors.append(f'line {line_number}: LTP case end without start: {name}')
                continue
            if name != current.name:
                errors.append(
                    f'line {line_number}: LTP case end does not match start: '
                    f'{current.name} != {name}'
                )
                current.complete = False
                finish(current)
                current = None
                continue
            if not re.fullmatch(r'\d+', raw_exit) or not 0 <= int(raw_exit) <= 255:
                errors.append(f'line {line_number}: invalid LTP exit code: {raw_exit}')
                current.exit_code = None
            else:
                current.exit_code = int(raw_exit)
            current.end_line = line_number
            current.complete = True
            finish(current)
            current = None
            continue
        if current is not None:
            current.records.append(event)
            continue
        if (
            event.kind == 'ltp_result'
            and event.fields[0] in UNATTRIBUTED_FLAGS
            or event.kind == 'ltp_unknown'
        ):
            record = {
                'event': event.fields[0],
                'section_line': line_number - line_offset,
                'line': event.line,
            }
            if event.kind == 'ltp_unknown':
                record['kind'] = 'ltp_unknown'
                errors.append(
                    f'line {line_number}: unknown LTP flag outside invocation: {event.fields[0]}'
                )
            unattributed.append(record)
        elif event.kind in ('summary', 'summary_field', 'legacy_summary'):
            errors.append(f'line {line_number}: summary outside invocation')

    if current is not None:
        errors.append(f'line {current.run_line}: LTP case has no end: {current.name}')
        current.complete = False
        finish(current)

    for name in attempts:
        for attempt in attempts[name]:
            _parse_attempt(attempt)

    items = []
    summary_missing = 0
    summary_incomplete = 0
    summary_mismatch = 0
    for name in expected_names:
        cases = attempts.get(name, [])
        if not cases:
            items.append(
                {
                    'name': name,
                    'judgment': 'MISSING',
                    'reasons': ['NO_RESULT'],
                    'native': {'events': {}},
                }
            )
            continue
        if len(cases) > 1:
            if any(case.summary_incomplete for case in cases):
                summary_incomplete += 1
            items.append(
                {
                    'name': name,
                    'judgment': 'JUDGE_ERROR',
                    'reasons': ['DUPLICATE_ATTEMPT'],
                    'attempts': [case.result() for case in cases],
                }
            )
            continue
        case = cases[0]
        result = case.result()
        if not case.summaries:
            summary_missing += 1
        if case.summary_incomplete:
            summary_incomplete += 1
        if case.summary_mismatch:
            summary_mismatch += 1
        items.append(result)

    extras = []
    for name, cases in sorted(attempts.items()):
        if name in expected_set:
            continue
        extras.append(
            {
                'name': name,
                'attempts': [case.result() for case in cases],
            }
        )

    native = {flag: 0 for flag in RESULT_FLAGS}
    summaries = {'passed': 0, 'failed': 0, 'broken': 0, 'skipped': 0, 'warnings': 0}
    for cases in attempts.values():
        for case in cases:
            for flag, count in case.events.items():
                native[flag] = native.get(flag, 0) + count
            # 重复块仍在逐调用诊断中保留，但不能重复累加原生汇总。
            if len(case.summaries) == 1 and not any(
                'SUMMARY' in error or 'LEGACY_FIELD' in error for error in case.parse_errors
            ):
                for key, value in case.summaries[0].items():
                    summaries[key] = summaries.get(key, 0) + value
    return LtpParse(
        items=items,
        extras=extras,
        errors=errors,
        native={
            'events': {flag: count for flag, count in native.items() if count},
            'summaries': {key: value for key, value in summaries.items() if value},
            'summary_missing': summary_missing,
            'summary_incomplete': summary_incomplete,
            'summary_mismatch': summary_mismatch,
            'unattributed': unattributed,
        },
        order_matches=observed_order == expected_names,
        summary_missing=summary_missing,
        summary_incomplete=summary_incomplete,
        summary_mismatch=summary_mismatch,
        unattributed=unattributed,
    )
