"""严格切分组段：重复、嵌套、错配和未闭合都不能覆盖旧段。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ltp import (RESULT_FLAGS, RESULT_RE, UNATTRIBUTED_FLAGS, UNKNOWN_RE,
                  IGNORED_FLAGS, strip_ansi)

GROUP_RE = re.compile(r'^####\s*OS COMP TEST GROUP\s+(START|END)\s+(\S+)\s*####\s*$')
LIBC_RE = re.compile(r'-(musl|glibc)\Z')
TESTCASE_RESULT = re.compile(r'\btestcase .+ \b(?:success|fail)\b')
CASE_MARKER = re.compile(r'^=+\s*(?:START|END)\s+')
TEAM_RESULT = re.compile(r'^=+\s*\S+\s+\S+\s+end:\s*\S+\s*=+\s*$')
LIBCBENCH_RESULT = re.compile(r'^b_[^\r\n(]+\([^\r\n]*\)')
UNIXBENCH_RESULT = re.compile(r'^Unixbench .+:')
IOZONE_RESULT = re.compile(r'(?:iozone .*measurements|Max throughput per process)')
LMBENCH_RESULT = re.compile(r'^\+ \./lmbench_all\b')
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
LMBENCH_METRIC = re.compile(rf'^(.+?):\s*({NUMBER})\s+(microseconds|KB/sec|MB/sec)$')
LMBENCH_HEADERS = {'file system latency', 'context switch overhead'}

RESULT_GROUPS = {
    'ltp': {'ltp'},
    'ltp_unknown': {'ltp'},
    'testcase': {'lua', 'busybox'},
    'pass': {'libctest'},
    'case marker': {'basic', 'libctest'},
    'team marker': {'iperf', 'netperf', 'cyclictest'},
    'libcbench': {'libcbench'},
    'unixbench': {'unixbench'},
    'iozone': {'iozone'},
    'lmbench': {'lmbench'},
}


def _result_kind(cleaned: str):
    if RESULT_RE.search(cleaned):
        return 'ltp'
    if any(match.group(1) not in RESULT_FLAGS and match.group(1) not in IGNORED_FLAGS
           for match in UNKNOWN_RE.finditer(cleaned)):
        return 'ltp_unknown'
    if cleaned == 'Pass!':
        return 'pass'
    if TESTCASE_RESULT.search(cleaned):
        return 'testcase'
    if CASE_MARKER.match(cleaned):
        return 'case marker'
    if TEAM_RESULT.fullmatch(cleaned):
        return 'team marker'
    if LIBCBENCH_RESULT.match(cleaned):
        return 'libcbench'
    if UNIXBENCH_RESULT.match(cleaned):
        return 'unixbench'
    if IOZONE_RESULT.search(cleaned):
        return 'iozone'
    if (LMBENCH_RESULT.match(cleaned) or LMBENCH_METRIC.fullmatch(cleaned)
            or cleaned in LMBENCH_HEADERS):
        return 'lmbench'
    return None


@dataclass
class Section:
    group: str
    libc: str
    key: tuple[str, str]
    lines: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int | None = None
    truncated: bool = False

    @property
    def text(self) -> str:
        return '\n'.join(self.lines)


@dataclass
class SplitResult:
    sections: dict[tuple[str, str], Section]
    errors: list[str]
    protocol: str
    section_errors: dict[tuple[str, str], list[str]]
    unattributed: list[dict] = field(default_factory=list)


def _key(raw: str, *, errors: list[str], section_errors: dict, line_number: int):
    match = LIBC_RE.search(raw)
    if not match:
        errors.append(f'line {line_number}: group marker has no libc suffix: {raw}')
        return None
    key = (raw[:match.start()], match.group(1))
    section_errors.setdefault(key, [])
    return key


def _compatible_groups(kind: str, cleaned: str) -> set[str]:
    # 团队标记携带实际组名，不能把三种组视为可互换。
    if kind == 'team marker':
        return {cleaned.lstrip('= ').split()[0]}
    return RESULT_GROUPS[kind]


def split_sections(text: str) -> SplitResult:
    """只按完整组标记切分；正文保持原始行顺序。"""
    sections: dict[tuple[str, str], Section] = {}
    errors: list[str] = []
    section_errors: dict[tuple[str, str], list[str]] = {}
    unattributed: list[dict] = []
    current: Section | None = None

    def close_current(*, truncated: bool, line_number: int):
        nonlocal current
        if current is None:
            return
        current.truncated = truncated
        current.end_line = None if truncated else line_number
        if current.key in sections:
            errors.append(f'line {line_number}: duplicate section: {current.group}-{current.libc}')
            section_errors.setdefault(current.key, []).append('DUPLICATE_SECTION')
        else:
            sections[current.key] = current
        current = None

    for line_number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        match = GROUP_RE.fullmatch(stripped)
        if not match:
            if 'OS COMP TEST GROUP' in stripped and not stripped.startswith('+ '):
                errors.append(f'line {line_number}: malformed group marker')
            cleaned = strip_ansi(raw).strip()
            if current is not None:
                kind = _result_kind(cleaned)
                if kind is not None and current.group not in _compatible_groups(kind, cleaned):
                    unattributed.append({
                        'line': line_number, 'kind': 'cross_group',
                        'event': kind, 'section': f'{current.group}-{current.libc}',
                        'text': cleaned[:200],
                    })
                    errors.append(
                        f'line {line_number}: {kind} result inside '
                        f'{current.group}-{current.libc} section'
                    )
                # 协议错误单独保留，正文不删行，避免后续证据行号偏移。
                current.lines.append(raw)
                continue
            kind = _result_kind(cleaned)
            if kind == 'ltp':
                for result in RESULT_RE.finditer(cleaned):
                    flag = result.group(1)
                    if flag not in UNATTRIBUTED_FLAGS:
                        continue
                    unattributed.append({'line': line_number, 'kind': 'ltp',
                                         'event': flag})
                    errors.append(f'line {line_number}: LTP result outside group: {flag}')
            else:
                for result in UNKNOWN_RE.finditer(cleaned):
                    token = result.group(1)
                    if token in RESULT_FLAGS or token in IGNORED_FLAGS:
                        continue
                    unattributed.append({'line': line_number, 'kind': 'ltp_unknown',
                                         'event': token})
                    errors.append(
                        f'line {line_number}: unknown LTP result outside group: {token}'
                    )
                if kind is not None and kind != 'ltp_unknown':
                    unattributed.append({'line': line_number, 'kind': 'non_ltp',
                                         'event': kind, 'text': cleaned[:200]})
                    errors.append(
                        f'line {line_number}: non-LTP result outside group: {kind}'
                    )
            continue
        state, name = match.groups()
        key = _key(name, errors=errors, section_errors=section_errors, line_number=line_number)
        if key is None:
            continue
        if state == 'START':
            if current is not None:
                errors.append(
                    f'line {line_number}: nested section start: {name}; '
                    f'current={current.group}-{current.libc}'
                )
                section_errors.setdefault(current.key, []).append('NESTED_SECTION')
                section_errors.setdefault(key, []).append('NESTED_SECTION')
                continue
            if key in sections:
                errors.append(f'line {line_number}: duplicate section: {name}')
                section_errors.setdefault(key, []).append('DUPLICATE_SECTION')
                continue
            current = Section(group=key[0], libc=key[1], key=key, start_line=line_number)
            continue
        if current is None:
            errors.append(f'line {line_number}: unmatched section end: {name}')
            section_errors.setdefault(key, []).append('UNMATCHED_END')
            continue
        if current.key != key:
            errors.append(
                f'line {line_number}: section end does not match start: '
                f'{current.group}-{current.libc} != {name}'
            )
            section_errors.setdefault(current.key, []).append('MISMATCHED_END')
            section_errors.setdefault(key, []).append('MISMATCHED_END')
            close_current(truncated=True, line_number=line_number)
            continue
        close_current(truncated=False, line_number=line_number)

    if current is not None:
        errors.append(
            f'line {current.start_line}: section not closed before EOF: '
            f'{current.group}-{current.libc}'
        )
        section_errors.setdefault(current.key, []).append('UNCLOSED_SECTION')
        close_current(truncated=True, line_number=len(text.splitlines()))

    if any(error for error in errors if 'not closed before EOF' not in error):
        protocol = 'ERROR'
    elif errors:
        protocol = 'INCOMPLETE'
    else:
        protocol = 'VALID'
    return SplitResult(sections=sections, errors=errors, protocol=protocol,
                       section_errors=section_errors, unattributed=unattributed)
