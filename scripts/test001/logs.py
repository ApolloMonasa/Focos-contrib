"""严格切分组段：重复、嵌套、错配和未闭合都不能覆盖旧段。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .protocol import Event, lex, UNATTRIBUTED_FLAGS

LIBC_RE = re.compile(r'-(musl|glibc)\Z')


@dataclass
class Section:
    group: str
    libc: str
    key: tuple[str, str]
    lines: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int | None = None
    truncated: bool = False
    events: list[Event] = field(default_factory=list)

    @property
    def text(self) -> str:
        # 官方 Python judge 按 LF 比较正文；只归一化 CRLF 的行终止符。
        # Event.raw 和 source.log 仍保留原始 CR，原日志行号不变。
        return '\n'.join(line.removesuffix('\r') for line in self.lines)


@dataclass
class SplitResult:
    sections: dict[tuple[str, str], Section]
    errors: list[str]
    protocol: str
    section_errors: dict[tuple[str, str], list[str]]
    unattributed: list[dict] = field(default_factory=list)
    events: tuple[Event, ...] = ()


def _key(raw: str, *, errors: list[str], section_errors: dict, line_number: int):
    match = LIBC_RE.search(raw)
    if not match:
        errors.append(f'line {line_number}: group marker has no libc suffix: {raw}')
        return None
    key = (raw[: match.start()], match.group(1))
    section_errors.setdefault(key, [])
    return key


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

    source_events = lex(text)
    for event in source_events:
        line_number, raw = event.line, event.raw
        if event.kind != 'group':
            if not event.canonical:
                errors.append(f'line {line_number}: non-canonical {event.kind} record')
            if event.kind == 'invalid_group':
                errors.append(f'line {line_number}: malformed group marker')
            structural = bool(event.groups)
            if current is not None:
                if structural and current.group not in event.groups:
                    unattributed.append(
                        {
                            'line': line_number,
                            'kind': 'cross_group',
                            'event': event.kind,
                            'section': f'{current.group}-{current.libc}',
                            'text': event.text[:200],
                        }
                    )
                    errors.append(
                        f'line {line_number}: {event.kind} result inside {current.group}-{current.libc} section'
                    )
                current.lines.append(raw)
                current.events.append(event)
            elif structural:
                if event.kind == 'ltp_result' and event.fields[0] not in UNATTRIBUTED_FLAGS:
                    continue
                kind = 'ltp' if event.groups == frozenset({'ltp'}) else 'non_ltp'
                if event.kind == 'ltp_unknown':
                    kind = 'ltp_unknown'
                name = (
                    (event.fields[0] if event.fields else event.kind)
                    if kind in ('ltp', 'ltp_unknown')
                    else {
                        'testcase': 'testcase',
                        'lmbench_metric': 'lmbench',
                        'lmbench_trace': 'lmbench',
                        'lmbench_header': 'lmbench',
                    }.get(event.kind, event.kind)
                )
                unattributed.append(
                    {'line': line_number, 'kind': kind, 'event': name, 'text': event.text[:200]}
                )
                errors.append(f'line {line_number}: {kind} result outside group: {name}')
            continue
        state, name = event.fields
        # 异常边界也是原始证据，保留后再处理状态；合法闭合边界不属正文。
        key = _key(name, errors=errors, section_errors=section_errors, line_number=line_number)
        if current is not None and (state == 'START' or key != current.key):
            current.lines.append(raw)
            current.events.append(event)
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
    return SplitResult(
        sections=sections,
        errors=errors,
        protocol=protocol,
        section_errors=section_errors,
        unattributed=unattributed,
        events=source_events,
    )
