"""从原始段文本独立提取观测名称，防止适配器覆盖重复项或丢掉未知项。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import shlex

MARKER = re.compile(r'^=+\s*(START|END)\s+(.+?)\s*=+\s*$')
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
LIBCBENCH_TIME = re.compile(rf'time:\s*({NUMBER}),')
LMBENCH_METRIC = re.compile(rf'^(.+?):\s*({NUMBER})\s+(microseconds|KB/sec|MB/sec)$')
TESTCASE = re.compile(r'testcase (.+) (\bsuccess\b|\bfail\b)')
UNIXBENCH = re.compile(r'^Unixbench (.+?):\s*(.*?)\s*$', re.M)
LIBCBENCH = re.compile(r'^b_[^\r\n(]+\([^\r\n]*\)\s*$', re.M)
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
    ('lmdd', 'label=File /var/tmp/XXX write bandwidth:', 'of=/var/tmp/XXX',
     'move=1m', 'fsync=1', 'print=3'),
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
    successes: set[str]


def _markers(group: str, text: str) -> list[str]:
    pattern = re.compile(
        rf'^=+\s*{re.escape(group)}\s+(\S+)\s+end:\s*\S+\s*=+\s*$', re.M
    )
    return [match.group(1) for match in pattern.finditer(text)]


def _iozone(text: str) -> list[str]:
    names = []
    current = None
    sub_key = None
    for line in text.splitlines():
        for needle, label in IOZONE_HEADERS:
            if needle in line:
                current = label
        if 'Children see throughput for' in line:
            sub_key = line.replace('Children see throughput for', '', 1).strip()
            sub_key = sub_key.split('=')[0].strip()
        if 'Max throughput per process' in line and current is not None and sub_key:
            names.append(f'{current} {sub_key} (kb/sec)')
    return names


def _testcase_pairs(text: str) -> list[tuple[str, str]]:
    return [
        (match.group(1).strip(), match.group(2))
        for match in TESTCASE.finditer(text)
    ]


def _testcase_malformed(text: str) -> list[str]:
    malformed = []
    for raw in text.splitlines():
        if 'testcase ' not in raw:
            continue
        statuses = re.findall(r'\b(success|fail)\b', raw)
        if len(statuses) != 1 or not TESTCASE.search(raw):
            malformed.append(raw.strip())
    return malformed


def _libctest_normalized(name: str):
    match = re.fullmatch(r'entry-(static|dynamic)\.exe (.+)', name)
    if match is None:
        return None
    return f'libctest {match.group(1)} {match.group(2)}'


def _libctest_marker(raw: str):
    match = re.search(
        r'\b(START|END)\s+(entry-(?:static|dynamic)\.exe\s+\S+)', raw
    )
    if match is None:
        return None
    return match.group(1), match.group(2)


def _basic_marker(raw: str):
    match = re.search(r'=+\s*(START|END)\s+(.+?)\s*=+', raw)
    if match is None:
        return None
    return match.group(1), match.group(2).strip()


def _marker_guard(group: str, text: str) -> tuple[list[str], list[str], set[str]]:
    """basic/libctest 的边界、正文归属和重复次数统一状态机。"""
    names = []
    malformed = []
    successes = set()
    open_name = None
    inside = False

    def begin(raw_name: str, line_number: int):
        nonlocal open_name, inside
        if open_name is not None:
            malformed.append(
                f'line {line_number}: nested START {raw_name} before END {open_name}'
            )
        if group == 'basic':
            names.append(raw_name)
        else:
            normalized = _libctest_normalized(raw_name)
            if normalized is None:
                names.append('libctest ' + raw_name)
                malformed.append(
                    f'line {line_number}: unknown libctest START name: {raw_name}'
                )
            else:
                names.append(normalized)
        open_name = raw_name
        inside = True

    def finish(raw_name: str, line_number: int):
        nonlocal open_name, inside
        if open_name is None:
            malformed.append(f'line {line_number}: END without START: {raw_name}')
        elif raw_name != open_name:
            malformed.append(
                f'line {line_number}: END {raw_name} does not match START {open_name}'
            )
            open_name = None
            inside = False
        else:
            open_name = None
            inside = False

    for line_number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        match = MARKER.fullmatch(stripped)
        if match is not None:
            state, name = match.groups()
            if state == 'START':
                begin(name, line_number)
            else:
                finish(name, line_number)
            continue

        if group == 'libctest':
            suspicious = any(token in raw for token in (
                'START entry-static.exe', 'START entry-dynamic.exe',
                'END entry-static.exe', 'END entry-dynamic.exe',
            ))
            parsed = _libctest_marker(raw) if suspicious else None
        else:
            suspicious = '========== START ' in raw or '========== END ' in raw
            parsed = _basic_marker(raw) if suspicious else None
        if suspicious:
            malformed.append(
                f'line {line_number}: non-canonical marker: {stripped}'
            )
            if parsed is not None:
                state, name = parsed
                if state == 'START':
                    begin(name, line_number)
                else:
                    finish(name, line_number)
            continue

        if group == 'libctest' and raw == 'Pass!':
            normalized = _libctest_normalized(open_name) if inside and open_name else None
            if normalized is None:
                malformed.append(f'line {line_number}: Pass! outside case')
            else:
                successes.add(normalized)

    if open_name is not None:
        malformed.append(f'unclosed START: {open_name}')
    return names, malformed, successes

def _lmbench(text: str, expected: list[str]) -> list[str]:
    names = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith('+ '):
            continue
        try:
            args = shlex.split(line[2:])
        except ValueError:
            if line[2:].startswith('./lmbench_all'):
                names.append('lmbench malformed-trace ' + line[2:])
            continue
        if not args or args[0] != './lmbench_all':
            continue
        command = tuple(args[1:])
        index = LMBENCH_INDEX.get(command)
        if command == tuple(' '.join(LMBENCH_COMMANDS[14]).split()):
            index = 14
        if index is not None and index < len(expected):
            names.append(expected[index])
        else:
            names.append('lmbench ' + shlex.join(command))
    return names


def extract_metrics(group: str, text: str, expected: list[str]) -> list[dict]:
    """从原始段保留性能数值和单位；冻结 adapter 仍只给 PASS/FAIL。"""
    metrics = []
    if group == 'libcbench':
        names = set(expected)
        current = None
        for line_number, raw in enumerate(text.splitlines(), 1):
            line = ANSI.sub('', raw).strip()
            if line in names:
                current = line
                continue
            if current is not None:
                match = LIBCBENCH_TIME.match(line)
                if match:
                    value = float(match.group(1))
                    if math.isfinite(value):
                        metrics.append({'name': current, 'value': value,
                                        'unit': 's', 'line': line_number})
                    current = None
        return metrics
    if group == 'lmbench':
        for line_number, raw in enumerate(text.splitlines(), 1):
            match = LMBENCH_METRIC.fullmatch(ANSI.sub('', raw).strip())
            if match:
                value = float(match.group(2))
                if math.isfinite(value):
                    metrics.append({'label': match.group(1).strip(),
                                    'value': value, 'unit': match.group(3),
                                    'line': line_number})
    return metrics


def observed_names(group: str, text: str, expected: list[str] | None = None) -> list[str]:
    """按固定组格式提取原始观测名称；未知格式不猜测。"""
    if group in ('basic', 'libctest'):
        names, _, _ = _marker_guard(group, text)
        return names
    if group in ('lua', 'busybox'):
        return [name for name, _ in _testcase_pairs(text)]
    if group == 'iozone':
        return _iozone(text)
    if group == 'unixbench':
        return ['Unixbench ' + match.group(1) for match in UNIXBENCH.finditer(text)]
    if group in ('iperf', 'netperf', 'cyclictest'):
        return _markers(group, text)
    if group == 'libcbench':
        return [match.group(0).strip() for match in LIBCBENCH.finditer(text)]
    if group == 'lmbench':
        return _lmbench(text, expected or [])
    return []


def check_observations(group: str, text: str, expected: list[str]) -> ObservationGuard:
    """返回范围外名称和重复名称；重复不代表可以挑最后一次成功。"""
    counts = Counter(observed_names(group, text, expected))
    expected_set = set(expected)
    extras = sorted(name for name in counts if name not in expected_set)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    statuses: dict[str, list[str]] = {}
    if group in ('lua', 'busybox'):
        for name, status in _testcase_pairs(text):
            statuses.setdefault(name, []).append(status)
    failures = {name for name, values in statuses.items() if 'fail' in values}
    successes = set()
    if group in ('basic', 'libctest'):
        _, malformed, successes = _marker_guard(group, text)
    elif group in ('lua', 'busybox'):
        malformed = _testcase_malformed(text)
    else:
        malformed = []
    return ObservationGuard(counts=counts, extras=extras, duplicates=duplicates,
                            failures=failures, statuses=statuses, malformed=malformed,
                            group=group, successes=successes)
