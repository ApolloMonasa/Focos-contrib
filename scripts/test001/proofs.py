"""语义证据计划：PASS 的正向证明、指标和逐事件归属来自同一状态机。"""

from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
import math
import re
import shlex

from .protocol import Event, lex, evidence, NUMBER
from .formats import LMBENCH_INDEX, LMBENCH_COMMANDS, LMBENCH_LABEL_INDEX, IOZONE_HEADERS


@dataclass
class Claim:
    name: str
    lines: list[int] = field(default_factory=list)
    context_lines: list[int] = field(default_factory=list)
    complete: bool = False
    positive: bool = False
    reasons: list[str] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    scoped_check: dict | None = None
    requires_scoped_check: bool = False

    @property
    def valid(self):
        return (
            self.complete
            and self.positive
            and bool(self.lines)
            and not self.reasons
            and (not self.requires_scoped_check or self.scoped_check is not None)
        )


@dataclass
class Plan:
    group: str
    expected: list[str]
    records: tuple[Event, ...]
    line_offset: int = 0
    claims: dict[str, list[Claim]] = field(default_factory=dict)
    malformed: list[dict] = field(default_factory=list)
    ledger: dict[int, dict] = field(default_factory=dict)
    successes: Counter = field(default_factory=Counter)
    statuses: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self):
        for e in self.records:
            structural = (
                bool(e.groups)
                or e.kind == 'group'
                or (self.group == 'lmbench' and e.kind in ('numbers', 'fs_row', 'ctx_header'))
            )
            self.ledger[e.line] = {
                'line': e.line,
                'disposition': 'unassigned' if structural else 'information',
                'owner': None,
                'reason': 'awaiting_owner' if structural else 'opaque_body',
            }

    @property
    def counts(self):
        return Counter({name: len(claims) for name, claims in self.claims.items()})

    @property
    def extras(self):
        return sorted(set(self.claims) - set(self.expected))

    @property
    def duplicates(self):
        return sorted(n for n, c in self.claims.items() if len(c) > 1)

    @property
    def failures(self):
        return {n for n, c in self.claims.items() if any(not x.positive for x in c)}

    @property
    def evidence(self):
        return [evidence(e, self.line_offset) for e in self.records if e.kind != 'text']

    @property
    def metrics(self):
        return [
            m
            for name, claims in self.claims.items()
            if name in self.expected or not self.expected
            if len(claims) == 1 and claims[0].valid
            for m in claims[0].measurements
        ]

    def error(self, event, message, claim=None):
        d = {**evidence(event, self.line_offset), 'message': message}
        self.malformed.append(d)
        self.ledger[event.line].update(disposition='error', reason=message)
        if claim is not None:
            claim.reasons.append(message)

    def begin(self, name, event):
        claim = Claim(name)
        self.claims.setdefault(name, []).append(claim)
        self.consume(event, claim)
        if self.expected and name not in self.expected:
            self.error(event, 'UNKNOWN_ITEM:' + name, claim)
        return claim

    def consume(self, event, claim):
        row = self.ledger[event.line]
        if row['owner'] not in (None, claim.name):
            self.error(event, 'CONFLICTING_OWNER', claim)
            return
        row['owner'] = claim.name
        if row['disposition'] != 'error':
            row.update(disposition='consumed', reason='case_evidence')
        if event.line not in claim.lines:
            claim.lines.append(event.line)
        if not event.canonical:
            self.error(event, 'NON_CANONICAL_RECORD', claim)
        if event.groups and self.group not in event.groups:
            self.error(event, 'WRONG_GROUP', claim)

    def measurement(self, claim, event, value, unit, **dimensions):
        self.consume(event, claim)
        if not math.isfinite(value):
            claim.reasons.append('NON_FINITE_MEASUREMENT')
            return
        claim.measurements.append(
            {
                'name': claim.name,
                'value': value,
                'unit': unit,
                'line': event.line,
                'section_line': event.line - self.line_offset,
                'raw': event.raw,
                **dimensions,
            }
        )

    def finish(self):
        for event in self.records:
            if self.ledger[event.line]['disposition'] == 'unassigned':
                self.error(event, 'UNOWNED_STRUCTURAL_EVENT')
        return self

    def proof_for(self, name, planned=1):
        claims = self.claims.get(name, [])
        lines = sorted({line for c in claims for line in c.lines})
        valid = (
            len(claims) == planned
            and all(c.valid for c in claims)
            and bool(lines)
            and all(
                self.ledger[line]['disposition'] == 'consumed'
                and self.ledger[line]['owner'] == name
                for line in lines
            )
        )
        return {
            'valid': valid,
            'group': self.group,
            'name': name,
            'planned_occurrences': planned,
            'event_lines': lines,
            'context_lines': sorted({line for c in claims for line in c.context_lines}),
            'attempts': [
                {
                    'complete': c.complete,
                    'positive': c.positive,
                    'reasons': c.reasons,
                    'event_lines': c.lines,
                    'scoped_check': c.scoped_check,
                }
                for c in claims
            ],
        }


def number(text):
    if re.fullmatch(NUMBER, text) is None:
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _cases(plan):
    current = None
    opened = None
    passes = 0
    for event in plan.records:
        if event.kind == 'case_marker':
            state, raw_name = event.fields
            name = raw_name
            if plan.group == 'libctest':
                match = re.fullmatch(r'entry-(static|dynamic)\.exe (.+)', raw_name)
                name = 'libctest ' + match[1] + ' ' + match[2] if match else 'libctest ' + raw_name
            if state == 'START':
                if current is not None:
                    plan.error(event, 'nested START before END', current)
                current = plan.begin(name, event)
                opened = event
                passes = 0
                current.requires_scoped_check = plan.group == 'basic'
            elif current is None:
                plan.error(event, 'END without START')
            else:
                plan.consume(event, current)
                if name != current.name:
                    plan.error(event, 'END does not match START', current)
                current.complete = True
                current.positive = (
                    passes == 1
                    if plan.group == 'libctest'
                    else any(
                        e.kind == 'text' and e.text
                        for e in plan.records
                        if opened.line < e.line < event.line
                    )
                )
                if plan.group == 'libctest' and passes == 0:
                    current.reasons.append('NO_IN_ATTEMPT_PASS')
                if passes > 1:
                    current.reasons.append('DUPLICATE_PASS:' + str(passes))
                current = None
        elif event.kind == 'case_pass':
            if current is None:
                plan.error(event, 'Pass! outside case')
            else:
                plan.consume(event, current)
                passes += 1
                plan.successes[current.name] += 1
        elif current is not None:
            plan.consume(event, current)
        elif event.groups or not event.canonical:
            plan.error(event, 'case result outside attempt')
    if current is not None:
        plan.error(opened, 'unclosed START', current)


def _single_records(plan):
    for e in plan.records:
        if plan.group in ('lua', 'busybox') and e.kind == 'testcase':
            name, status = e.fields
            c = plan.begin(name, e)
            c.complete = True
            c.positive = status == 'success'
            plan.statuses.setdefault(name, []).append(status)
        elif plan.group in ('iperf', 'netperf', 'cyclictest') and e.kind == 'team_result':
            owner, name, status = e.fields
            c = plan.begin(name, e)
            c.complete = True
            c.positive = owner == plan.group and status == 'success'
            if status not in ('success', 'fail'):
                c.reasons.append('INVALID_STATUS')
            if owner != plan.group:
                plan.error(e, 'WRONG_GROUP', c)
        elif plan.group == 'unixbench' and e.kind == 'unixbench':
            name, text = e.fields
            c = plan.begin('Unixbench ' + name, e)
            c.complete = True
            value = number(text)
            c.positive = value is not None and value > 0
            if value is None:
                c.reasons.append('INVALID_NUMERIC_RESULT')
            if value is not None:
                unit = re.search(r'\(([^()]*)\)$', name)
                plan.measurement(c, e, value, unit[1] if unit else 'source-defined')
        elif e.groups or not e.canonical:
            plan.error(e, 'UNEXPECTED_RECORD')


def _libcbench(plan):
    current = None
    for e in plan.records:
        if e.kind == 'libcbench_label':
            if current is not None:
                current.reasons.append('MISSING_TIME')
            current = plan.begin(e.fields[0], e)
        elif e.kind == 'libcbench_time':
            if current is None:
                plan.error(e, 'ORPHAN_TIME')
                continue
            plan.consume(e, current)
            value = number(e.fields[0])
            current.complete = True
            current.positive = value is not None and value > 0
            if value is not None:
                plan.measurement(current, e, value, 's')
            current = None
        elif current is not None and e.text:
            plan.consume(e, current)
            plan.error(e, 'INTERRUPTED_TIME', current)
            current = None
        elif e.groups or not e.canonical:
            plan.error(e, 'UNEXPECTED_RECORD')
    if current is not None:
        current.reasons.append('MISSING_TIME')


def _lmbench(plan):
    current = None
    index = None
    header = False
    rows = set()

    def name(i, label=None):
        return plan.expected[i] if i < len(plan.expected) else (label or f'lmbench index {i}')

    def begin(i, e, label=None):
        nonlocal current, index, header, rows
        current = plan.begin(name(i, label), e)
        index = i
        header = False
        rows = set()

    for e in plan.records:
        if e.kind == 'lmbench_trace':
            cmd = e.fields
            i = LMBENCH_INDEX.get(cmd)
            if cmd == tuple(' '.join(LMBENCH_COMMANDS[14]).split()):
                i = 14
            if current is not None and not current.complete:
                current.reasons.append('MISSING_MEASUREMENT')
            if i is None:
                c = plan.begin('lmbench ' + shlex.join(cmd), e)
                plan.error(e, 'UNKNOWN_TRACE', c)
                current = None
                index = None
                continue
            begin(i, e)
        elif e.kind == 'lmbench_metric':
            label, text, unit = e.fields
            i = LMBENCH_LABEL_INDEX.get(label)
            if i is None:
                c = plan.begin('lmbench ' + label, e)
                plan.error(e, 'UNKNOWN_LABEL', c)
                continue
            if current is not None and not current.complete and index != i:
                plan.error(e, 'TRACE_RESULT_MISMATCH', current)
            if current is None or current.complete or index != i:
                begin(i, e, label)
            plan.consume(e, current)
            wanted = 'KB/sec' if i == 14 else 'MB/sec' if i == 17 else 'microseconds'
            value = number(text)
            current.complete = True
            current.positive = value is not None and value > 0 and unit == wanted
            if unit != wanted:
                current.reasons.append('WRONG_UNIT')
            if value is not None:
                plan.measurement(current, e, value, unit, label=label)
            current = None
            index = None
        elif e.kind == 'lmbench_header':
            i = 18 if e.fields[0] == 'file system latency' else 23
            if current is not None and index == i and not header and not current.complete:
                plan.consume(e, current)
            else:
                begin(i, e)
            header = True
        elif e.kind == 'ctx_header':
            if current is None or index != 23:
                plan.error(e, 'ORPHAN_CONTEXT_HEADER')
            else:
                plan.consume(e, current)
        elif e.kind == 'numbers' and current is not None and index in (16, 19, 20, 21, 22):
            plan.consume(e, current)
            if current.complete:
                current.reasons.append('DUPLICATE_MEASUREMENT')
            size, value = map(float, e.fields)
            current.complete = True
            current.positive = (
                math.isfinite(size)
                and math.isclose(size, 0.524288, rel_tol=0, abs_tol=0.0000005)
                and math.isfinite(value)
                and value > 0
            )
            plan.measurement(
                current,
                e,
                value,
                'microseconds' if index == 16 else 'MB/sec',
                input_size=size,
                input_size_unit='MB',
            )
        elif e.kind == 'fs_row':
            if current is None or index != 18:
                plan.error(e, 'ORPHAN_FS_ROW')
                continue
            plan.consume(e, current)
            size, files, creates, deletes = map(int, e.fields)
            if size in rows:
                current.reasons.append('DUPLICATE_TABLE_ROW')
            rows.add(size)
            if size == 10:
                current.complete = True
                current.positive = all(n > 0 for n in (files, creates, deletes))
            for operation, value in (('create', creates), ('delete', deletes)):
                plan.measurement(
                    current,
                    e,
                    value,
                    'operations/sec',
                    operation=operation,
                    file_size=size,
                    file_size_unit='kB',
                    files=files,
                )
        elif e.kind == 'numbers' and current is not None and index == 23:
            plan.consume(e, current)
            processes, value = map(float, e.fields)
            if processes not in (2, 4, 8, 16, 24, 32, 64, 96):
                plan.error(e, 'UNKNOWN_PROCESS_COUNT', current)
            if processes in rows:
                current.reasons.append('DUPLICATE_TABLE_ROW')
            rows.add(processes)
            if processes == 96:
                current.complete = True
                current.positive = math.isfinite(value) and value > 0
            plan.measurement(current, e, value, 'microseconds', processes=int(processes))
        elif e.kind in ('numbers', 'scalar'):
            plan.error(e, 'ORPHAN_NUMERIC_RESULT')
        elif current is not None and e.text:
            plan.consume(e, current)
            plan.error(e, 'INTERRUPTED_MEASUREMENT', current)
            current = None
            index = None
        elif e.groups or not e.canonical:
            plan.error(e, 'UNEXPECTED_RECORD')


def _iozone(plan):
    current_header = None
    pending = None
    headers = dict(IOZONE_HEADERS)
    for e in plan.records:
        if e.kind == 'iozone_header':
            current_header = headers.get(e.fields[0])
            header_line = e.line
            if current_header is None:
                plan.error(e, 'UNKNOWN_IOZONE_HEADER')
            else:
                plan.ledger[e.line].update(
                    disposition='consumed', owner='context:iozone', reason='iozone_table_header'
                )
            if pending is not None and not pending.complete:
                pending.reasons.append('MISSING_MAXIMUM')
            pending = None
        elif e.kind == 'iozone_child':
            if current_header is None:
                plan.error(e, 'ORPHAN_IOZONE_CHILD')
                continue
            if pending is not None and not pending.complete:
                pending.reasons.append('MISSING_MAXIMUM')
            pending = plan.begin(f'{current_header} {e.fields[0]} (kb/sec)', e)
            pending.context_lines.append(header_line)
        elif e.kind == 'iozone_max':
            if pending is None:
                plan.error(e, 'ORPHAN_IOZONE_MAXIMUM')
                continue
            plan.consume(e, pending)
            value = number(e.fields[0])
            pending.complete = True
            pending.positive = value is not None and value > 0
            if value is not None:
                plan.measurement(pending, e, value, 'kB/sec')
            pending = None
        elif e.groups or not e.canonical:
            plan.error(e, 'UNEXPECTED_RECORD')


def build_plan(group, text, expected, *, events=None, line_offset=0):
    records = tuple(lex(text, line_offset=line_offset) if events is None else events)
    plan = Plan(group, list(expected), records, line_offset)
    if group in ('basic', 'libctest'):
        _cases(plan)
    elif group in ('lua', 'busybox', 'iperf', 'netperf', 'cyclictest', 'unixbench'):
        _single_records(plan)
    elif group == 'libcbench':
        _libcbench(plan)
    elif group == 'lmbench':
        _lmbench(plan)
    elif group == 'iozone':
        _iozone(plan)
    else:
        for e in records:
            if e.groups:
                plan.error(e, 'UNSUPPORTED_GROUP')
    return plan.finish()
