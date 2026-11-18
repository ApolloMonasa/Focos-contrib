"""严格切分组段：重复、嵌套、错配和未闭合都不能覆盖旧段。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GROUP_RE = re.compile(r'^####\s*OS COMP TEST GROUP\s+(START|END)\s+(\S+)\s*####\s*$')
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

    @property
    def text(self) -> str:
        return '\n'.join(self.lines)


@dataclass
class SplitResult:
    sections: dict[tuple[str, str], Section]
    errors: list[str]
    protocol: str
    section_errors: dict[tuple[str, str], list[str]]


def _key(raw: str, *, errors: list[str], section_errors: dict, line_number: int):
    match = LIBC_RE.search(raw)
    if not match:
        errors.append(f'line {line_number}: group marker has no libc suffix: {raw}')
        return None
    key = (raw[:match.start()], match.group(1))
    section_errors.setdefault(key, [])
    return key


def split_sections(text: str) -> SplitResult:
    """只按完整组标记切分；正文保持原始行顺序。"""
    sections: dict[tuple[str, str], Section] = {}
    errors: list[str] = []
    section_errors: dict[tuple[str, str], list[str]] = {}
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
            if current is not None:
                current.lines.append(raw)
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
                       section_errors=section_errors)
