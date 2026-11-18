"""从原始段文本独立提取观测名称，防止适配器覆盖重复项或丢掉未知项。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import shlex

from .protocol import lex, NUMBER

LMBENCH_COMMANDS = (
    ('lat_syscall', '-P', '1', 'null'),
    ('lat_syscall', '-P', '1', 'read'),
    ('lat_syscall', '-P', '1', 'write'),
    ('lat_syscall', '-P', '1', 'stat', '/var/tmp/lmbench'),
    ('lat_syscall', '-P', '1', 'fstat', '/var/tmp/lmbench'),
    ('lat_syscall', '-P', '1', 'open', '/var/tmp/lmbench'),
    ('lat_select', '-n', '100', '-P', '1', 'file'),
    ('lat_sig', '-P', '1', 'install'),
    ('lat_sig', '-P', '1', 'catch'),
    ('lat_sig', '-P', '1', 'prot', 'lat_sig'),
    ('lat_pipe', '-P', '1'),
    ('lat_proc', '-P', '1', 'fork'),
    ('lat_proc', '-P', '1', 'exec'),
    ('lat_proc', '-P', '1', 'shell'),
    (
        'lmdd',
        'label=File /var/tmp/XXX write bandwidth:',
        'of=/var/tmp/XXX',
        'move=1m',
        'fsync=1',
        'print=3',
    ),
    ('lat_pagefault', '-P', '1', '/var/tmp/XXX'),
    ('lat_mmap', '-P', '1', '512k', '/var/tmp/XXX'),
    ('bw_pipe', '-P', '1'),
    ('lat_fs', '/var/tmp'),
    ('bw_file_rd', '-P', '1', '512k', 'io_only', '/var/tmp/XXX'),
    ('bw_file_rd', '-P', '1', '512k', 'open2close', '/var/tmp/XXX'),
    ('bw_mmap_rd', '-P', '1', '512k', 'mmap_only', '/var/tmp/XXX'),
    ('bw_mmap_rd', '-P', '1', '512k', 'open2close', '/var/tmp/XXX'),
    ('lat_ctx', '-P', '1', '-s', '32', '2', '4', '8', '16', '24', '32', '64', '96'),
)
LMBENCH_INDEX = {command: index for index, command in enumerate(LMBENCH_COMMANDS)}
LMBENCH_LABELS = {
    'Simple syscall',
    'Simple read',
    'Simple write',
    'Simple stat',
    'Simple fstat',
    'Simple open/close',
    "Select on 100 fd's",
    'Signal handler installation',
    'Signal handler overhead',
    'Protection fault',
    'Pipe latency',
    'Process fork+exit',
    'Process fork+execve',
    'Process fork+/bin/sh -c',
    'Static Process fork+exit',
    'Static Process fork+execve',
    'Static Process fork+/bin/sh -c',
    'File /var/tmp/XXX write bandwidth',
    'Pagefaults on /var/tmp/XXX',
    'Pipe bandwidth',
}
IOZONE_HEADERS = (
    ('iozone throughput write/read measurements', 'iozone write/read'),
    ('iozone throughput random-read measurements', 'iozone random-read'),
    ('iozone throughput read-backwards measurements', 'iozone read-backwards'),
    ('iozone throughput stride-read measurements', 'iozone stride-read'),
    ('iozone throughput fwrite/fread measurements', 'iozone fwrite/fread'),
    ('iozone throughput pwrite/pread measurements', 'iozone pwrite/pread'),
    ('iozone throughtput pwritev/preadv measurements', 'iozone pwritev/preadv'),
)


@dataclass
class ObservationGuard:
    counts: Counter
    extras: list[str]
    duplicates: list[str]
    failures: set[str]
    statuses: dict[str, list[str]]
    malformed: list[str]
    group: str
    successes: Counter


def _markers(group: str, records) -> list[str]:
    return [
        event.fields[1]
        for event in records
        if event.kind == 'team_result' and event.fields[0] == group
    ]


def _iozone(records) -> list[str]:
    names = []
    current = None
    sub_key = None
    for event in records:
        line = event.text
        for needle, label in IOZONE_HEADERS:
            if needle in line:
                current = label
        if 'Children see throughput for' in line:
            sub_key = line.replace('Children see throughput for', '', 1).strip()
            sub_key = sub_key.split('=')[0].strip()
        if 'Max throughput per process' in line and current is not None and sub_key:
            names.append(f'{current} {sub_key} (kb/sec)')
    return names


def _testcase_pairs(records) -> list[tuple[str, str]]:
    return [event.fields for event in records if event.kind == 'testcase']


def _testcase_malformed(records) -> list[str]:
    return [
        event.raw.strip()
        for event in records
        if event.kind == 'invalid_testcase' or event.kind == 'testcase' and not event.canonical
    ]


def _libctest_normalized(name: str):
    match = re.fullmatch(r'entry-(static|dynamic)\.exe (.+)', name)
    if match is None:
        return None
    return f'libctest {match.group(1)} {match.group(2)}'


def _marker_guard(group: str, records) -> tuple[list[str], list[str], Counter]:
    """basic/libctest 的边界、正文归属和重复次数统一状态机。"""
    names = []
    malformed = []
    successes = Counter()
    open_name = None
    inside = False

    def begin(raw_name: str, line_number: int):
        nonlocal open_name, inside
        if open_name is not None:
            malformed.append(f'line {line_number}: nested START {raw_name} before END {open_name}')
        if group == 'basic':
            names.append(raw_name)
        else:
            normalized = _libctest_normalized(raw_name)
            if normalized is None:
                names.append('libctest ' + raw_name)
                malformed.append(f'line {line_number}: unknown libctest START name: {raw_name}')
            else:
                names.append(normalized)
        open_name = raw_name
        inside = True

    def finish(raw_name: str, line_number: int):
        nonlocal open_name, inside
        if open_name is None:
            malformed.append(f'line {line_number}: END without START: {raw_name}')
        elif raw_name != open_name:
            malformed.append(f'line {line_number}: END {raw_name} does not match START {open_name}')
            open_name = None
            inside = False
        else:
            open_name = None
            inside = False

    for event in records:
        line_number = event.line
        if event.kind == 'invalid_case':
            malformed.append(f'line {line_number}: non-canonical marker: {event.text}')
            continue
        if event.kind == 'case_marker':
            state, name = event.fields
            if not event.canonical:
                malformed.append(f'line {line_number}: non-canonical marker: {event.text}')
            if state == 'START':
                begin(name, line_number)
            else:
                finish(name, line_number)
            continue
        if group == 'libctest' and event.kind == 'case_pass':
            normalized = _libctest_normalized(open_name) if inside and open_name else None
            if normalized is None:
                malformed.append(f'line {line_number}: Pass! outside case')
            else:
                successes[normalized] += 1

    if open_name is not None:
        malformed.append(f'unclosed START: {open_name}')
    return names, malformed, successes


def _lmbench(records, expected: list[str]) -> list[str]:
    names = []
    for event in records:
        if event.kind == 'lmbench_trace':
            if not event.canonical:
                names.append('lmbench malformed-trace ' + event.text)
                continue
            command = event.fields
            index = LMBENCH_INDEX.get(command)
            if command == tuple(' '.join(LMBENCH_COMMANDS[14]).split()):
                index = 14
            if index is not None and index < len(expected):
                names.append(expected[index])
            else:
                names.append('lmbench ' + shlex.join(command))
        elif event.kind == 'lmbench_metric':
            label = event.fields[0]
            if label not in LMBENCH_LABELS:
                names.append('lmbench ' + label)
    return names


def extract_metrics(
    group: str, text: str, expected: list[str], *, line_offset: int = 0, events=None
) -> list[dict]:
    """从原始段保留性能数值和单位；冻结 adapter 仍只给 PASS/FAIL。"""
    metrics = []
    records = lex(text, line_offset=line_offset) if events is None else events
    if group == 'libcbench':
        names = set(expected)
        current = None
        for event in records:
            line_number, raw, line = event.line - line_offset, event.raw, event.text
            if event.kind == 'libcbench_label' and line in names:
                current = line
                continue
            if current is not None:
                if event.kind == 'libcbench_time':
                    value = float(event.fields[0])
                    if math.isfinite(value):
                        metrics.append(
                            {
                                'name': current,
                                'value': value,
                                'unit': 's',
                                'line': line_number + line_offset,
                                'section_line': line_number,
                            }
                        )
                    current = None
        return metrics
    if group == 'lmbench':
        context = None
        for event in records:
            line_number, raw, line = event.line - line_offset, event.raw, event.text
            if event.kind == 'lmbench_trace':
                context = LMBENCH_INDEX.get(event.fields) if event.canonical else None
                if event.fields == tuple(' '.join(LMBENCH_COMMANDS[14]).split()):
                    context = 14
                continue
            if event.kind == 'lmbench_header':
                context = 18 if line == 'file system latency' else 23
                continue
            if event.kind == 'lmbench_metric':
                label, number, unit = event.fields
                value = float(number)
                if math.isfinite(value):
                    metrics.append(
                        {
                            'label': label,
                            'value': value,
                            'unit': unit,
                            'line': event.line,
                            'section_line': line_number,
                            'raw': raw,
                        }
                    )
                context = None
                continue
            name = expected[context] if context is not None and context < len(expected) else None
            if context in (16, 19, 20, 21, 22):
                pair = re.fullmatch(rf'({NUMBER})\s+({NUMBER})', line)
                if pair and all(math.isfinite(float(v)) for v in pair.groups()):
                    metrics.append(
                        {
                            'name': name,
                            'value': float(pair[2]),
                            'unit': 'microseconds' if context == 16 else 'MB/sec',
                            'input_size': float(pair[1]),
                            'input_size_unit': 'MB',
                            'line': line_number + line_offset,
                            'section_line': line_number,
                            'raw': raw,
                        }
                    )
                elif line:
                    context = None
            elif context == 18:
                row = re.fullmatch(r'(\d+)k\s+(\d+)\s+(\d+)\s+(\d+)', line)
                if row:
                    for column, operation in ((3, 'create'), (4, 'delete')):
                        metrics.append(
                            {
                                'name': name,
                                'operation': operation,
                                'value': int(row[column]),
                                'unit': 'operations/sec',
                                'file_size': int(row[1]),
                                'file_size_unit': 'kB',
                                'files': int(row[2]),
                                'line': line_number + line_offset,
                                'section_line': line_number,
                                'raw': raw,
                            }
                        )
                elif line:
                    context = None
            elif context == 23:
                row = re.fullmatch(rf'(\d+)\s+({NUMBER})', line)
                if row and math.isfinite(float(row[2])):
                    metrics.append(
                        {
                            'name': name,
                            'value': float(row[2]),
                            'unit': 'microseconds',
                            'processes': int(row[1]),
                            'line': line_number + line_offset,
                            'section_line': line_number,
                            'raw': raw,
                        }
                    )
                elif line and not line.startswith('"size='):
                    context = None
    return metrics


def observed_names(
    group: str, text: str, expected: list[str] | None = None, *, events=None
) -> list[str]:
    """消费共享结构事件，冻结判据的名称投影不改变原始事件。"""
    records = lex(text) if events is None else events
    if group in ('basic', 'libctest'):
        names, _, _ = _marker_guard(group, records)
        return names
    if group in ('lua', 'busybox'):
        return [name for name, _ in _testcase_pairs(records)]
    if group == 'iozone':
        return _iozone(records)
    if group == 'unixbench':
        return ['Unixbench ' + e.fields[0] for e in records if e.kind == 'unixbench']
    if group in ('iperf', 'netperf', 'cyclictest'):
        return _markers(group, records)
    if group == 'libcbench':
        return [e.fields[0] for e in records if e.kind == 'libcbench_label']
    if group == 'lmbench':
        return _lmbench(records, expected or [])
    return []


def check_observations(
    group: str, text: str, expected: list[str], *, events=None
) -> ObservationGuard:
    records = lex(text) if events is None else events
    counts = Counter(observed_names(group, text, expected, events=records))
    extras = sorted(name for name in counts if name not in set(expected))
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    statuses = {}
    if group in ('lua', 'busybox'):
        for name, status in _testcase_pairs(records):
            statuses.setdefault(name, []).append(status)
    failures = {name for name, values in statuses.items() if 'fail' in values}
    successes = Counter()
    if group in ('basic', 'libctest'):
        _, malformed, successes = _marker_guard(group, records)
    elif group in ('lua', 'busybox'):
        malformed = _testcase_malformed(records)
    else:
        malformed = []
    return ObservationGuard(
        counts, extras, duplicates, failures, statuses, malformed, group, successes
    )
