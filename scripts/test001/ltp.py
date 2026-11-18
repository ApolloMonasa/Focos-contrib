"""LTP 原生结果适配器：先保留每次尝试和原生计数，再给调用判分。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RESULT_FLAGS = ('TPASS', 'TFAIL', 'TBROK', 'TCONF', 'TWARN', 'TINFO', 'TDEBUG')
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RESULT_RE = re.compile(r'(?:^|\s)(TPASS|TFAIL|TBROK|TCONF|TWARN|TINFO|TDEBUG)\s*:')
UNKNOWN_RE = re.compile(r'(?:^|\s)(T[A-Z][A-Z0-9_]{2,})\s*:')
IGNORED_FLAGS = {'TERRNO', 'TTERRNO', 'TRERRNO'}
SUMMARY_LINE_RE = re.compile(r'^(passed|failed|broken|skipped|warnings)\s*:?\s*(\d+)\s*$')
LEGACY_RE = re.compile(r'^(Total Tests|Passed|Failed|Not Supported)\s*:\s*(\d+)\s*$')


@dataclass
class Attempt:
    name: str
    run_line: int
    end_line: int | None = None
    exit_code: int | None = None
    complete: bool = False
    lines: list[str] = field(default_factory=list)
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
            for flag, label in (('TFAIL', 'TFAIL'), ('TBROK', 'TBROK'),
                                ('TCONF', 'TCONF'), ('TWARN', 'TWARN')):
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
            'events': dict(sorted(self.events.items())),
            'summaries': list(self.summaries),
            'summary_incomplete': self.summary_incomplete,
            'summary_mismatch': self.summary_mismatch,
            'unknown_flags': sorted(self.unknown_flags),
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


def _summaries_from_lines(lines: list[str], *, attempt: Attempt) -> list[dict[str, int]]:
    summaries = []
    for index, raw in enumerate(lines):
        if strip_ansi(raw).strip() != 'Summary:':
            continue
        values = {}
        cursor = index + 1
        while cursor < len(lines):
            line = strip_ansi(lines[cursor]).strip()
            if not line:
                cursor += 1
                continue
            match = SUMMARY_LINE_RE.fullmatch(line)
            if not match:
                break
            key, value = match.groups()
            if key in values:
                attempt.parse_errors.append(f'DUPLICATE_SUMMARY_FIELD:{key}')
            values[key] = int(value)
            cursor += 1
        if not values:
            attempt.parse_errors.append('EMPTY_SUMMARY')
        elif set(values) != {'passed', 'failed', 'broken', 'skipped', 'warnings'}:
            attempt.summary_incomplete = True
        summaries.append(values)
    if summaries:
        return summaries
    legacy = {}
    for raw in lines:
        match = LEGACY_RE.fullmatch(strip_ansi(raw).strip())
        if not match:
            continue
        key, value = match.groups()
        if key == 'Total Tests':
            continue
        legacy[{'Passed': 'passed', 'Failed': 'failed', 'Not Supported': 'skipped'}[key]] = int(value)
    if legacy:
        attempt.summary_incomplete = True
        return [legacy]
    return []


def strip_ansi(text: str) -> str:
    """去掉串口颜色控制码；原始字节仍由报告输入哈希保存。"""
    return ANSI_RE.sub('', text)


def _parse_attempt(attempt: Attempt):
    counts = {flag: 0 for flag in RESULT_FLAGS}
    unknown = set()
    for raw in attempt.lines:
        line = strip_ansi(raw)
        for match in RESULT_RE.finditer(line):
            counts[match.group(1)] += 1
        for match in UNKNOWN_RE.finditer(line):
            token = match.group(1)
            if token not in RESULT_FLAGS and token not in IGNORED_FLAGS:
                unknown.add(token)
    attempt.events = {flag: count for flag, count in counts.items() if count}
    attempt.unknown_flags = sorted(unknown)
    attempt.summaries = _summaries_from_lines(attempt.lines, attempt=attempt)
    if unknown:
        attempt.parse_errors.append('UNKNOWN_RESULT_FLAG:' + ','.join(sorted(unknown)))
    for summary in attempt.summaries:
        pairs = (('passed', 'TPASS'), ('failed', 'TFAIL'), ('broken', 'TBROK'),
                 ('skipped', 'TCONF'), ('warnings', 'TWARN'))
        for summary_key, event_key in pairs:
            if summary_key in summary and summary[summary_key] != counts[event_key]:
                attempt.summary_mismatch = True


def parse_ltp_section(text: str, expected: list[dict]) -> LtpParse:
    """按固定版本的原生标志解析一个 ltp-<libc> 段。"""
    expected_names = [item['name'] for item in expected]
    expected_set = set(expected_names)
    attempts: dict[str, list[Attempt]] = {}
    errors: list[str] = []
    observed_order: list[str] = []
    current: Attempt | None = None
    lines = text.splitlines()

    def finish(attempt: Attempt | None):
        if attempt is None:
            return
        attempts.setdefault(attempt.name, []).append(attempt)

    for line_number, raw in enumerate(lines, 1):
        stripped = strip_ansi(raw).strip()
        run = re.fullmatch(r'RUN LTP CASE (.+)', stripped)
        if run:
            name = run.group(1)
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
            current = Attempt(name=name, run_line=line_number)
            observed_order.append(name)
            continue
        end = re.fullmatch(r'FAIL LTP CASE (.+) : (\S+)', stripped)
        if end:
            name, raw_exit = end.groups()
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
            current.lines.append(raw)

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
            items.append({
                'name': name, 'judgment': 'MISSING', 'reasons': ['NO_RESULT'],
                'native': {'events': {}},
            })
            continue
        if len(cases) > 1:
            if any(case.summary_incomplete for case in cases):
                summary_incomplete += 1
            items.append({
                'name': name, 'judgment': 'JUDGE_ERROR',
                'reasons': ['DUPLICATE_ATTEMPT'],
                'attempts': [case.result() for case in cases],
            })
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
        extras.append({
            'name': name,
            'attempts': [case.result() for case in cases],
        })

    native = {flag: 0 for flag in RESULT_FLAGS}
    summaries = {'passed': 0, 'failed': 0, 'broken': 0, 'skipped': 0, 'warnings': 0}
    for cases in attempts.values():
        for case in cases:
            for flag, count in case.events.items():
                native[flag] = native.get(flag, 0) + count
            for summary in case.summaries:
                for key, value in summary.items():
                    summaries[key] = summaries.get(key, 0) + value
    return LtpParse(
        items=items, extras=extras, errors=errors,
        native={'events': {flag: count for flag, count in native.items() if count},
                'summaries': {key: value for key, value in summaries.items() if value},
                'summary_missing': summary_missing, 'summary_incomplete': summary_incomplete,
                'summary_mismatch': summary_mismatch},
        order_matches=observed_order == expected_names,
        summary_missing=summary_missing, summary_incomplete=summary_incomplete,
        summary_mismatch=summary_mismatch,
    )
