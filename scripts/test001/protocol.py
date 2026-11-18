"""冻结日志词法：每行一个结构角色，所有消费者共用原始位置和字段。"""

from __future__ import annotations
from dataclasses import dataclass
import re
import shlex

RESULT_FLAGS = ('TPASS', 'TFAIL', 'TBROK', 'TCONF', 'TWARN', 'TINFO', 'TDEBUG')
UNATTRIBUTED_FLAGS = {'TPASS', 'TFAIL', 'TBROK', 'TCONF', 'TWARN'}
IGNORED_FLAGS = {'TERRNO', 'TTERRNO', 'TRERRNO'}
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
GROUP_RE = re.compile(r'^####\s*OS COMP TEST GROUP\s+(START|END)\s+(\S+)\s*####\s*$')
MARKER = re.compile(r'^=+\s*(START|END)\s+(.+?)\s*=+\s*$')
TESTCASE = re.compile(r'testcase (.+) (\bsuccess\b|\bfail\b)')
LMBENCH_METRIC = re.compile(rf'^(.+?):\s*({NUMBER})\s+(microseconds|KB/sec|MB/sec)$')
LMBENCH_HEADERS = {'file system latency', 'context switch overhead'}
SUMMARY_LINE_RE = re.compile(r'^(passed|failed|broken|skipped|warnings)\s*:?\s*(\d+)\s*$')
LEGACY_RE = re.compile(r'^(Total Tests|Passed|Failed|Not Supported)\s*:\s*(\d+)\s*$')
# 前缀是协议字段，冒号后的消息不再扫描；支持冻结的现代及旧式格式。
LTP_RECORD = re.compile(
    r'^(?:(?:[^\s:]+:\d+:\s*)|(?:[^\s:]+\s+\d+\s+))?'
    r'(T[A-Z][A-Z0-9_]{2,})\s*:\s*(.*)$'
)


@dataclass(frozen=True)
class Event:
    line: int
    raw: str
    text: str
    kind: str
    fields: tuple[str, ...] = ()
    groups: frozenset[str] = frozenset()
    canonical: bool = True


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub('', text)


def classify(raw: str, number: int) -> Event:
    text = strip_ansi(raw).strip()

    def event(kind, fields=(), groups=(), canonical=True):
        return Event(number, raw, text, kind, tuple(fields), frozenset(groups), canonical)

    m = GROUP_RE.fullmatch(text)
    if m:
        return event('group', m.groups())
    if 'OS COMP TEST GROUP' in text and not text.startswith('+ '):
        return event('invalid_group')
    m = re.fullmatch(r'RUN LTP CASE (.+)', text)
    if m:
        return event('ltp_start', m.groups(), ('ltp',))
    m = re.fullmatch(r'FAIL LTP CASE (.+) : (\S+)', text)
    if m:
        return event('ltp_end', m.groups(), ('ltp',))
    m = LTP_RECORD.fullmatch(text)
    if m:
        kind = 'ltp_result' if m[1] in RESULT_FLAGS else 'ltp_unknown'
        if m[1] in IGNORED_FLAGS:
            kind = 'text'
        return event(kind, m.groups(), ('ltp',) if kind != 'text' else ())
    if text == 'Summary:':
        return event('summary', groups=('ltp',))
    m = SUMMARY_LINE_RE.fullmatch(text)
    if m:
        return event('summary_field', m.groups(), ('ltp',))
    m = LEGACY_RE.fullmatch(text)
    if m:
        return event('legacy_summary', m.groups(), ('ltp',))
    m = MARKER.fullmatch(text)
    if m:
        return event(
            'case_marker', m.groups(), ('libctest',) if m[2].startswith('entry-') else ('basic',)
        )
    # 疑似官方标记也必须可见；不能因非规范语法而隐藏一次尝试。
    m = re.search(r'\b(START|END)\s+(entry-(?:static|dynamic)\.exe\s+\S+)', text)
    if m:
        return event('case_marker', m.groups(), ('libctest',), False)
    m = re.search(r'=+\s*(START|END)\s+(.+?)\s*=+', text)
    if m:
        return event('case_marker', m.groups(), ('basic',), False)
    if any(
        token in text
        for token in ('START entry-', 'END entry-', '========== START ', '========== END ')
    ):
        return event('invalid_case', groups=('basic', 'libctest'), canonical=False)
    if text == 'Pass!':
        return event('case_pass', groups=('libctest',))
    m = TESTCASE.search(raw)
    if m:
        name, status = m.groups()
        owner = name.split()[0]
        ambiguous = len(re.findall(r'\b(success|fail)\b', raw)) != 1
        return event('testcase', (name.strip(), status), (owner,), not ambiguous)
    if 'testcase ' in raw:
        return event('invalid_testcase', groups=('lua', 'busybox'), canonical=False)
    m = re.fullmatch(r'=+\s*(\S+)\s+(\S+)\s+end:\s*(\S+)\s*=+\s*', text)
    if m:
        return event('team_result', m.groups(), (m[1],))
    m = re.fullmatch(r'b_[^\r\n(]+\([^\r\n]*\)', text)
    if m:
        return event('libcbench_label', (text,), ('libcbench',))
    m = re.match(rf'time:\s*({NUMBER}),', text)
    if m:
        return event('libcbench_time', m.groups(), ('libcbench',))
    m = re.fullmatch(r'Unixbench (.+?):\s*(.*?)\s*', text)
    if m:
        return event('unixbench', m.groups(), ('unixbench',))
    if re.search(
        r'iozone .*measurements|Max throughput per process|Children see throughput for', text
    ):
        return event('iozone', (text,), ('iozone',))
    if text.startswith('+ '):
        try:
            args = shlex.split(text[2:])
        except ValueError:
            if text[2:].startswith('./lmbench_all'):
                return event('lmbench_trace', (text[2:],), ('lmbench',), False)
        else:
            if args and args[0] == './lmbench_all':
                return event('lmbench_trace', args[1:], ('lmbench',))
    m = LMBENCH_METRIC.fullmatch(text)
    if m:
        return event('lmbench_metric', m.groups(), ('lmbench',))
    if text in LMBENCH_HEADERS:
        return event('lmbench_header', (text,), ('lmbench',))
    # 数字行只有在已冻结 trace/表头上下文内才能归属，不单独作为组边界结果。
    return event('text')


def lex(text: str, *, line_offset: int = 0) -> tuple[Event, ...]:
    # 串口记录由 LF 分隔；VT/FF/Unicode 分隔符属于正文，不能伪造新结果行。
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return tuple(classify(raw, number) for number, raw in enumerate(lines, 1 + line_offset))
