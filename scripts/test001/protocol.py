"""冻结日志词法：每行一个结构角色，所有消费者共用原始位置和字段。"""

from __future__ import annotations
from dataclasses import dataclass
import re
import shlex
from .formats import LMBENCH_LABELS

RESULT_FLAGS = ('TPASS', 'TFAIL', 'TBROK', 'TCONF', 'TWARN', 'TINFO', 'TDEBUG')
UNATTRIBUTED_FLAGS = {'TPASS', 'TFAIL', 'TBROK', 'TCONF', 'TWARN'}
IGNORED_FLAGS = {'TERRNO', 'TTERRNO', 'TRERRNO'}
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
GROUP_RE = re.compile(r'^####[ \t]*OS COMP TEST GROUP[ \t]+(START|END)[ \t]+(\S+)[ \t]*####[ \t]*$')
MARKER = re.compile(r'^=+[ \t]*(START|END)[ \t]+(.+?)[ \t]*=+[ \t]*$')
TESTCASE = re.compile(r'testcase (.+) (\bsuccess\b|\bfail\b)')
LMBENCH_METRIC = re.compile(rf'^(.+?):[ \t]*({NUMBER})[ \t]+(microseconds|KB/sec|MB/sec)$')
LMBENCH_HEADERS = {'file system latency', 'context switch overhead'}
SUMMARY_LINE_RE = re.compile(r'^(passed|failed|broken|skipped|warnings)[ \t]*:?[ \t]*(\d+)[ \t]*$')
LEGACY_RE = re.compile(r'^(Total Tests|Passed|Failed|Not Supported)[ \t]*:[ \t]*(\d+)[ \t]*$')
# 前缀是协议字段，冒号后的消息不再扫描；支持冻结的现代及旧式格式。
LTP_RECORD = re.compile(
    r'^(?:(?:[^\s:]+:\d+:[ \t]*)|(?:[^\s:]+[ \t]+\d+[ \t]+))?'
    r'(T[A-Z][A-Z0-9_]{2,})[ \t]*:[ \t]*(.*)$'
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


def classify(raw: str, number: int, *, crlf=False) -> Event:
    text = strip_ansi(raw)
    if crlf:
        text = text.removesuffix('\r')
    text = text.strip(' \t')

    def event(kind, fields=(), groups=(), canonical=True):
        return Event(number, raw, text, kind, tuple(fields), frozenset(groups), canonical)

    if text and text[0].isspace():
        return event('invalid_record', canonical=False)
    m = GROUP_RE.fullmatch(text)
    if m:
        return event('group', m.groups())
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
    if 'OS COMP TEST GROUP' in text and not text.startswith('+ '):
        return event('invalid_group')
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
    m = re.search(r'\b(START|END)[ \t]+(entry-(?:static|dynamic)\.exe[ \t]+\S+)', text)
    if m:
        return event('case_marker', m.groups(), ('libctest',), False)
    m = re.search(r'=+[ \t]*(START|END)[ \t]+(.+?)[ \t]*=+', text)
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
        owner = name.split(' ', 1)[0]
        ambiguous = len(re.findall(r'\b(success|fail)\b', raw)) != 1
        return event('testcase', (name.strip(' \t'), status), (owner,), not ambiguous)
    if 'testcase ' in raw:
        return event('invalid_testcase', groups=('lua', 'busybox'), canonical=False)
    m = re.fullmatch(r'=+[ \t]*(\S+)[ \t]+(\S+)[ \t]+end:[ \t]*(\S+)[ \t]*=+[ \t]*', text)
    if m:
        return event('team_result', m.groups(), (m[1],))
    m = re.fullmatch(r'b_[^\r\n(]+\([^\r\n]*\)', text)
    if m:
        return event('libcbench_label', (text,), ('libcbench',))
    m = re.fullmatch(
        rf'time:[ \t]*({NUMBER}),[ \t]*virt:[ \t]*[0-9]+,[ \t]*res:[ \t]*[0-9]+(?:,[ \t]*dirty:[ \t]*[0-9]+)?',
        text,
    )
    if m:
        return event('libcbench_time', m.groups(), ('libcbench',))
    if text.startswith('time:'):
        return event('invalid_libcbench_time', groups=('libcbench',), canonical=False)
    m = re.fullmatch(r'Unixbench (.+?):[ \t]*(.*?)[ \t]*', text)
    if m:
        return event('unixbench', m.groups(), ('unixbench',))
    if 'iozone ' in text and 'measurements' in text:
        return event('iozone_header', (text,), ('iozone',))
    m = re.fullmatch(
        r'Children see throughput for[ \t]+(.+?)[ \t]*=[ \t]*(' + NUMBER + r')[ \t]+kB/sec', text
    )
    if m:
        return event('iozone_child', m.groups(), ('iozone',))
    m = re.fullmatch(r'Max throughput per process[ \t]*=[ \t]*(' + NUMBER + r')[ \t]+kB/sec', text)
    if m:
        return event('iozone_max', m.groups(), ('iozone',))
    m = re.match(r'Children see throughput for[ \t]+(.+?)(?:[ \t]*=|$)', text)
    if m:
        return event('iozone_child', (m[1].strip(' \t'), ''), ('iozone',), False)
    if 'Max throughput per process' in text:
        return event('iozone_max', ('',), ('iozone',), False)
    if 'Children see throughput for' in text or 'Max throughput per process' in text:
        return event('invalid_iozone', groups=('iozone',), canonical=False)
    if re.search(r'\b(?:iperf|netperf|cyclictest)[ \t]+.*end:', text):
        return event(
            'invalid_team_result', groups=('iperf', 'netperf', 'cyclictest'), canonical=False
        )
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
    if any(text.startswith(label + ':') for label in LMBENCH_LABELS):
        return event('invalid_lmbench_metric', groups=('lmbench',), canonical=False)
    m = re.fullmatch(r'(' + NUMBER + r')[ \t]+(' + NUMBER + r')', text)
    if m:
        return event('numbers', m.groups())
    m = re.fullmatch(r'([0-9]+)k[ \t]+([0-9]+)[ \t]+([0-9]+)[ \t]+([0-9]+)', text)
    if m:
        return event('fs_row', m.groups(), ('lmbench',))
    if re.fullmatch(r'"size=32k ovr=' + NUMBER, text):
        return event('ctx_header', (text,), ('lmbench',))
    if re.fullmatch(NUMBER, text):
        return event('scalar', (text,))
    # 数字行只有在已冻结 trace/表头上下文内才能归属，不单独作为组边界结果。
    return event('text')


def evidence(event: Event, line_offset: int = 0) -> dict:
    """诊断统一引用原始事件，聚合计数不替代可回查证据。"""
    return {
        'line': event.line,
        'section_line': event.line - line_offset,
        'raw': event.raw,
        'kind': event.kind,
        'fields': list(event.fields),
    }


def lex(text: str, *, line_offset: int = 0) -> tuple[Event, ...]:
    # 串口记录由 LF 分隔；VT/FF/Unicode 分隔符属于正文，不能伪造新结果行。
    lines = text.split('\n')
    final_terminated = text.endswith('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return tuple(
        classify(raw, number, crlf=index < len(lines) - 1 or final_terminated)
        for index, (number, raw) in enumerate(enumerate(lines, 1 + line_offset))
    )
