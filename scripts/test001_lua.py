"""TEST-001 A1 Lua 子集；纯内存解析，不提供 CLI 或运行验收。"""
from dataclasses import dataclass
from enum import Enum
import re

LUA_NAMES = ('date.lua', 'file_io.lua', 'max_min.lua', 'random.lua', 'remove.lua',
             'round_num.lua', 'sin30.lua', 'sort.lua', 'strings.lua')


class Protocol(Enum):
    COMPLETE = 'COMPLETE'
    INCOMPLETE = 'INCOMPLETE'
    ERROR = 'ERROR'


class Judgment(Enum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    MISSING = 'MISSING'
    NOT_RUN = 'NOT_RUN'
    JUDGE_ERROR = 'JUDGE_ERROR'


class Code(Enum):
    INVALID_STRUCTURE = 'INVALID_STRUCTURE'
    UNSUPPORTED_STRUCTURE = 'UNSUPPORTED_STRUCTURE'
    GROUP_MISMATCH = 'GROUP_MISMATCH'
    DUPLICATE_GROUP = 'DUPLICATE_GROUP'
    OUTSIDE_GROUP = 'OUTSIDE_GROUP'
    UNKNOWN_INVOCATION = 'UNKNOWN_INVOCATION'
    OUT_OF_SELECTION = 'OUT_OF_SELECTION'
    DUPLICATE_INVOCATION = 'DUPLICATE_INVOCATION'
    ORDER_MISMATCH = 'ORDER_MISMATCH'
    ORDER_INCOMPLETE = 'ORDER_INCOMPLETE'
    ORDER_UNVERIFIABLE = 'ORDER_UNVERIFIABLE'
    MISSING_GROUP = 'MISSING_GROUP'
    TRUNCATED = 'TRUNCATED'


@dataclass(frozen=True)
class Record:
    record_id: int
    line: int
    start: int
    end: int
    raw: bytes
    text: str
    text_offsets: tuple
    kind: str
    group: str | None = None
    libc: str | None = None
    name: str | None = None
    status: str | None = None
    result_like: bool = False


@dataclass(frozen=True)
class Assignment:
    record_id: int
    owner: str
    rule: str
    group: str | None
    libc: str | None
    invocation: str | None = None
    attempt: int | None = None


@dataclass(frozen=True)
class Diagnostic:
    code: Code
    record_id: int | None
    expected: tuple | None = None
    observed: tuple | None = None


@dataclass(frozen=True)
class Observation:
    record_id: int
    group: str | None
    libc: str | None
    name: str | None
    status: str | None
    attempt: int
    in_selection: bool
    diagnostics: tuple

    @property
    def invocation_diagnostics(self):
        # 顺序属于组协议；不能据此抹掉已唯一归属的单次调用结果。
        return tuple(code for code in self.diagnostics if code != Code.ORDER_MISMATCH)


@dataclass(frozen=True)
class InvocationResult:
    name: str
    identity: tuple
    attempts: tuple
    judgment: Judgment


@dataclass(frozen=True)
class AnalysisResult:
    libc: str
    expected_sequence: tuple
    observed_sequence: tuple
    order_matches: bool
    records: tuple
    assignments: tuple
    observations: tuple
    invocations: tuple
    diagnostics: tuple
    protocol: Protocol
    acceptance: str = 'BLOCKED'
    trust: str = 'external_log/unknown'


# 固定 Unicode 16.0 Cf 范围（170 个字符），避免 Python 3.11/3.14 的
# Unicode 14/16 分类差异改变协议；Cc 使用固定的 C0/C1 范围，HT 例外。
FORMAT_CONTROL_RANGES = (
    (0xAD, 0xAD), (0x600, 0x605), (0x61C, 0x61C), (0x6DD, 0x6DD),
    (0x70F, 0x70F), (0x890, 0x891), (0x8E2, 0x8E2), (0x180E, 0x180E),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
    (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x110BD, 0x110BD), (0x110CD, 0x110CD),
    (0x13430, 0x1343F), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0001, 0xE0001), (0xE0020, 0xE007F),
)
UNSUPPORTED_CONTROLS = frozenset(
    chr(point) for begin, end in ((0, 8), (10, 31), (127, 159)) + FORMAT_CONTROL_RANGES
    for point in range(begin, end + 1)
)


SGR = (b'\x1b[0m', b'\x1b[31m', b'\x1b[32m', b'\x1b[33m', b'\x1b[39m')
GROUP = re.compile(r'####[ \t]+OS[ \t]+COMP[ \t]+TEST[ \t]+GROUP[ \t]+(START|END)[ \t]+([a-z0-9_]+)-(musl|glibc)[ \t]+####')
RESULT = re.compile(r'testcase[ \t]+([a-z0-9_]+)[ \t]+([A-Za-z0-9_.+-]+)[ \t]+(success|fail)')
RESERVED = ('####', 'testcase', 'RUN LTP CASE', 'FAIL LTP CASE', 'Summary',
            'passed:', 'failed:', 'broken:', 'skipped:', 'warnings:', 'Total Tests',
            'TPASS', 'TFAIL', 'TBROK', 'TCONF', 'TWARN')


# 仅用于损坏前缀的保留标志识别，不参与合法结构匹配。
CSI_PREFIX = re.compile(r'(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]')
OSC_PREFIX = re.compile(r'(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)')


def _result_prefix(text):
    """只越过开头的空白/控制/坏字节，绝不搜索普通消息内部。"""
    position = 0
    while position < len(text):
        if text.startswith('testcase', position):
            return True
        if text.startswith(('\x1b]', '\x9d'), position):
            # OSC 消息体不透明；未终止时也不能把其中 testcase 当成调用。
            message = OSC_PREFIX.match(text, position)
            if message is None:
                return False
            position = message.end()
            continue
        char = text[position]
        if char in ('\x1b', '\x9b') and not text.startswith('testcase', position + 1):
            sequence = CSI_PREFIX.match(text, position)
            if sequence:
                position = sequence.end()
                continue
        if char.isspace() or char in UNSUPPORTED_CONTROLS or 0xDC80 <= ord(char) <= 0xDCFF:
            position += 1
            continue
        return False
    return False


def _recognition(body, start):
    normalized, positions = bytearray(), []
    i, invalid = 0, False
    while i < len(body):
        color = next((s for s in SGR if body.startswith(s, i)), None)
        if color:
            i += len(color)
            continue
        byte = body[i]
        invalid |= byte < 32 and byte != 9 or byte == 127
        normalized.append(byte)
        positions.append(start + i)
        i += 1
    try:
        text = normalized.decode('utf-8')
    except UnicodeDecodeError:
        result_like = _result_prefix(normalized.decode('utf-8', errors='surrogateescape'))
        text, mapping = '', []
        for byte, position in zip(normalized, positions):
            char = chr(byte) if byte < 128 else f'\\x{byte:02x}'
            text += char
            mapping.extend([position] * len(char))
        return text, tuple(mapping), True, result_like
    invalid |= any((char.isspace() and char not in ' \t') or
                   char in UNSUPPORTED_CONTROLS
                   for char in text)
    mapping, byte_index = [], 0
    for char in text:
        mapping.append(positions[byte_index])
        byte_index += len(char.encode('utf-8'))
    return text, tuple(mapping), invalid, _result_prefix(text)


def _records(raw):
    records, offset = [], 0
    while offset < len(raw):
        lf = raw.find(b'\n', offset)
        end = lf + 1 if lf >= 0 else len(raw)
        original = raw[offset:end]
        body = original[:-1] if lf >= 0 else original
        if lf >= 0 and body.endswith(b'\r'):
            body = body[:-1]
        text, mapping, invalid, result_like = _recognition(body, offset)
        token = text.strip(' \t')
        group_match, result_match = GROUP.fullmatch(token), RESULT.fullmatch(token)
        reserved = token.startswith(RESERVED)
        kind, group, libc, name, status = 'information', None, None, None, None
        if invalid or (lf < 0 and (reserved or group_match or result_match)):
            kind = 'invalid'
        elif group_match:
            action, group, libc = group_match.groups()
            kind = action.lower()
        elif result_match:
            group, name, status = result_match.groups()
            kind = 'result'
        elif reserved:
            kind = 'invalid' if token.startswith(('####', 'testcase')) else 'unsupported'
        records.append(Record(len(records) + 1, len(records) + 1, offset, end, original,
                              text, mapping, kind, group, libc, name, status,
                              result_like))
        offset = end
    return tuple(records)


def analyze_lua(raw, libc, selection=None):
    if not isinstance(raw, bytes):
        raise TypeError('raw must be immutable bytes')
    if libc not in ('musl', 'glibc'):
        raise ValueError('unsupported libc')
    selected = LUA_NAMES if selection is None else selection
    if (not isinstance(selected, tuple) or not selected or
            any(not isinstance(n, str) or n not in LUA_NAMES for n in selected) or
            len(set(selected)) != len(selected)):
        raise ValueError('selection must be a nonempty unique tuple of known Lua names')
    names = tuple(n for n in LUA_NAMES if n in selected)
    expected = tuple(('lua', libc, n) for n in names)
    records = _records(raw)
    assignments, observations, diagnostics, observed = [], [], [], []
    attempts = {name: [] for name in names}
    counts = {}
    active, started = False, False

    def error(code, record_id=None, want=None, actual=None):
        diagnostics.append(Diagnostic(code, record_id, want, actual))

    def observe(record, identity, codes):
        position = len(observed)
        want = expected[position] if position < len(expected) else None
        if identity != want:
            error(Code.ORDER_MISMATCH, record.record_id, want, identity)
            codes.append(Code.ORDER_MISMATCH)
        observed.append(identity)
        counts[identity] = counts.get(identity, 0) + 1
        if counts[identity] > 1 and identity[2] is not None:
            error(Code.DUPLICATE_INVOCATION, record.record_id, actual=identity)
            codes.append(Code.DUPLICATE_INVOCATION)
        item = Observation(record.record_id, *identity, record.status, counts[identity],
                           identity in expected, tuple(codes))
        observations.append(item)
        if item.in_selection:
            attempts[item.name].append(item)
        return item

    for record in records:
        owner, rule = 'information', 'opaque'
        assignment_group, assignment_libc = ('lua', libc) if active else (None, None)
        invocation, attempt = None, None
        if record.kind in ('invalid', 'unsupported'):
            code = Code.INVALID_STRUCTURE if record.kind == 'invalid' else Code.UNSUPPORTED_STRUCTURE
            error(code, record.record_id)
            owner, rule = 'error', code.value
            if record.result_like:
                error(Code.ORDER_UNVERIFIABLE, record.record_id)
                item = observe(record, (None, None, None), [code, Code.ORDER_UNVERIFIABLE])
                attempt = item.attempt
        elif record.kind in ('start', 'end'):
            matches = record.group == 'lua' and record.libc == libc
            code = None
            if not matches:
                code = Code.GROUP_MISMATCH
            elif record.kind == 'start':
                if started:
                    code = Code.DUPLICATE_GROUP
                else:
                    active = started = True
            elif not active:
                code = Code.OUTSIDE_GROUP
            else:
                active = False
            assignment_group, assignment_libc = record.group, record.libc
            if code:
                error(code, record.record_id)
                owner, rule = 'error', code.value
            else:
                owner, rule = 'group', record.kind
        elif record.kind == 'result':
            codes = []
            identity = (record.group, libc if active else None, record.name)
            if not active:
                codes.append(Code.OUTSIDE_GROUP)
            if record.group != 'lua':
                codes.append(Code.UNSUPPORTED_STRUCTURE)
            elif record.name not in LUA_NAMES:
                codes.append(Code.UNKNOWN_INVOCATION)
            elif record.name not in names:
                codes.append(Code.OUT_OF_SELECTION)
            for code in codes:
                error(code, record.record_id, actual=identity)
            item = observe(record, identity, codes)
            assignment_group, assignment_libc, invocation = identity
            attempt = item.attempt
            invocation_errors = item.invocation_diagnostics
            owner = 'error' if invocation_errors else 'invocation'
            rule = invocation_errors[0].value if invocation_errors else 'lua_result'
        assignments.append(Assignment(record.record_id, owner, rule, assignment_group,
                                      assignment_libc, invocation, attempt))
    if not started:
        error(Code.MISSING_GROUP)
    if active:
        error(Code.TRUNCATED)
    if len(observed) < len(expected) and tuple(observed) == expected[:len(observed)]:
        error(Code.ORDER_INCOMPLETE, want=expected[len(observed)])
    incomplete = {Code.MISSING_GROUP, Code.TRUNCATED, Code.ORDER_INCOMPLETE}
    protocol = (Protocol.ERROR if any(d.code not in incomplete for d in diagnostics) else
                Protocol.INCOMPLETE if diagnostics else Protocol.COMPLETE)
    invocations = []
    for name, identity in zip(names, expected):
        items = tuple(attempts[name])
        if not items:
            judgment = Judgment.MISSING if started else Judgment.NOT_RUN
        elif len(items) > 1 or any(item.invocation_diagnostics for item in items):
            judgment = Judgment.JUDGE_ERROR
        else:
            judgment = Judgment.PASS if items[0].status == 'success' else Judgment.FAIL
        invocations.append(InvocationResult(name, identity, items, judgment))
    result = AnalysisResult(libc, expected, tuple(observed), tuple(observed) == expected,
                          records, tuple(assignments), tuple(observations), tuple(invocations),
                          tuple(diagnostics), protocol)
    _validate_links(result)
    return result


def _validate_links(result):
    """仅验证规范对象的引用守恒；不重扫原始文本。"""
    ids = tuple(record.record_id for record in result.records)
    if ids != tuple(a.record_id for a in result.assignments) or len(set(ids)) != len(ids):
        raise RuntimeError('record ownership invariant failed')
    records = {r.record_id: r for r in result.records}
    assignments = {a.record_id: a for a in result.assignments}
    seen = set()
    for observation in result.observations:
        record = records.get(observation.record_id)
        if record is None or record.kind not in ('result', 'invalid') or record.record_id in seen:
            raise RuntimeError('observation reference invariant failed')
        seen.add(record.record_id)
        if record.kind == 'result' and (record.group, record.name, record.status) != (
                observation.group, observation.name, observation.status):
            raise RuntimeError('observation fields invariant failed')
        owner = 'error' if observation.invocation_diagnostics else 'invocation'
        if assignments[record.record_id].owner != owner:
            raise RuntimeError('observation ownership invariant failed')
    by_id = {o.record_id: o for o in result.observations}
    for invocation in result.invocations:
        for attempt in invocation.attempts:
            if (by_id.get(attempt.record_id) != attempt or not attempt.in_selection or
                    (attempt.group, attempt.libc, attempt.name) != invocation.identity):
                raise RuntimeError('invocation evidence invariant failed')
