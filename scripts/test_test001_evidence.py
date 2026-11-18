"""从最终报告验证：参考 adapter 不得独立授权 PASS，结构事件不得无归属。"""

import json
from pathlib import Path
import shlex
import tempfile
import unittest
from test_test001_analyze import ROOT, make_run, cli, load_report, J1, HISTORICAL, section_bytes
from test001.observations import LMBENCH_COMMANDS, IOZONE_HEADERS

ROWS = [
    json.loads(line)
    for line in (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
    .read_text()
    .splitlines()
]


def names(group):
    return [r['name'] for r in ROWS if r['group'] == group and r['libc'] == 'glibc']


LABELS = [
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
]


def lmbench_lines():
    lines = []
    for i, cmd in enumerate(LMBENCH_COMMANDS):
        lines.append('+ ./lmbench_all ' + shlex.join(cmd))
        if i < 14:
            lines.append(LABELS[i] + ': 1 microseconds')
        elif i == 14:
            lines.append('File /var/tmp/XXX write bandwidth: 1 KB/sec')
        elif i == 15:
            lines.append('Pagefaults on /var/tmp/XXX: 1 microseconds')
        elif i == 17:
            lines.append('Pipe bandwidth: 1 MB/sec')
        elif i == 18:
            lines.extend(['file system latency', '10k 100 200 300'])
        elif i == 23:
            lines.extend(['context switch overhead', '96 1'])
        else:
            lines.append('0.524288 1')
    return lines


class FinalEvidenceTests(unittest.TestCase):
    def analyze(self, group, body):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        serial = (
            f'#### OS COMP TEST GROUP START {group}-glibc ####\n'
            + body
            + f'\n#### OS COMP TEST GROUP END {group}-glibc ####\n'
        ).encode()
        run = make_run(root / 'run', [group + '-glibc'], serial)
        out = root / 'out'
        result = cli('analyze', '--run', run, '--criteria', J1, '--out', out, '--require-pass')
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, load_report(out)

    def test_adapter_only_pass_is_blocked_across_record_transformations(self):
        for group in ('iperf', 'netperf', 'cyclictest'):
            valid = [f'====== {group} {name} end: success ======' for name in names(group)]
            for transform in (
                lambda s: s.replace('====== ', '======\n', 1).replace(' ======', '\n======'),
                *(
                    lambda s, sep=sep: s.replace(' end:', sep + 'end:')
                    for sep in ('\u00a0', '\v', '\f', '\r')
                ),
            ):
                with self.subTest(group=group):
                    code, report = self.analyze(group, '\n'.join(transform(line) for line in valid))
                    self.assertEqual(code, 1)
                    self.assertEqual(report['totals']['accepted_pass'], 0)
        code, _ = self.analyze('unixbench', '\n'.join(n + ':\n1' for n in names('unixbench')))
        self.assertEqual(code, 1)
        code, _ = self.analyze(
            'lmbench',
            '\n'.join(lmbench_lines()).replace('Simple syscall: ', 'Simple syscall:\u00a0'),
        )
        self.assertEqual(code, 1)

    def test_orphan_time_cannot_disappear(self):
        body = 'time: 99, virt: 1, res: 1\n' + '\n'.join(
            n + '\ntime: 1, virt: 1, res: 1' for n in names('libcbench')
        )
        code, report = self.analyze('libcbench', body)
        self.assertEqual(code, 1)
        self.assertTrue(
            any(row['disposition'] == 'error' for row in report['groups'][0]['event_ledger'])
        )

    def test_final_report_rejects_duplicate_or_orphan_measurements(self):
        cases = [
            (
                'unixbench',
                '\n'.join(n + ': 1' for n in names('unixbench')),
                names('unixbench')[0] + ': 1',
            ),
            (
                'libcbench',
                '\n'.join(n + '\ntime: 1, virt: 1, res: 1' for n in names('libcbench')),
                'time: 2, virt: 1, res: 1',
            ),
            ('lmbench', '\n'.join(lmbench_lines()), '0.524288 1'),
            ('lmbench', '\n'.join(lmbench_lines()), 'Simple syscall: 1 microseconds'),
        ]
        for group, valid, added in cases:
            for body in (added + '\n' + valid, valid + '\n' + added):
                with self.subTest(group=group, added=added, beginning=body.startswith(added)):
                    code, report = self.analyze(group, body)
                    self.assertEqual(code, 1)
                    self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_nonfinite_or_oversized_table_dimensions_produce_blocked_reports(self):
        valid = '\n'.join(lmbench_lines())
        for body in (
            valid.replace('96 1', '1e999 1'),
            valid.replace('10k 100 200 300', '10k ' + '9' * 5000 + ' 200 300'),
        ):
            code, report = self.analyze('lmbench', body)
            self.assertEqual(code, 1)
            self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_valid_performance_passes_have_owned_evidence(self):
        bodies = {
            g: '\n'.join(f'====== {g} {n} end: success ======' for n in names(g))
            for g in ('iperf', 'netperf', 'cyclictest')
        }
        bodies.update(
            unixbench='\n'.join(n + ': 1' for n in names('unixbench')),
            libcbench='\n'.join(n + '\ntime: 1, virt: 1, res: 1' for n in names('libcbench')),
            lmbench='\n'.join(lmbench_lines()),
        )
        for group, body in bodies.items():
            with self.subTest(group=group):
                code, report = self.analyze(group, body)
                self.assertEqual(code, 0, report['acceptance'])
                self.assertEqual(len(report['protocol']['event_ledger']), len(body.split('\n')) + 2)
                gr = report['groups'][0]
                ledger = {r['line']: r for r in gr['event_ledger']}
                for item in gr['items']:
                    proof = item['proof']
                    self.assertTrue(proof['valid'])
                    self.assertEqual(proof['name'], item['name'])
                    self.assertTrue(proof['event_lines'])
                    for line in proof['event_lines']:
                        self.assertEqual(ledger[line]['disposition'], 'consumed')
                        self.assertEqual(ledger[line]['owner'], item['name'])
                self.assertFalse(
                    any(
                        r['disposition'] not in ('consumed', 'information') for r in ledger.values()
                    )
                )


class FinalGateTests(unittest.TestCase):
    def test_all_remaining_groups_have_final_proofs(self):
        helper = FinalEvidenceTests(methodName='test_orphan_time_cannot_disappear')
        self.addCleanup(helper.doCleanups)
        bodies = {
            'lua': '\n'.join('testcase ' + n + ' success' for n in names('lua')),
            'busybox': '\n'.join(
                'testcase ' + n + ' success'
                for n in names('busybox')
                for _ in range(2 if n == 'busybox echo "bbbbbbb" >> test.txt' else 1)
            ),
            'libctest': '\n'.join(
                '========== START entry-'
                + n.split(' ', 2)[1]
                + '.exe '
                + n.split(' ', 2)[2]
                + ' ==========\nPass!\n========== END entry-'
                + n.split(' ', 2)[1]
                + '.exe '
                + n.split(' ', 2)[2]
                + ' =========='
                for n in names('libctest')
            ),
            'ltp': '\n'.join(
                'RUN LTP CASE ' + n + '\nTPASS: ok\nFAIL LTP CASE ' + n + ' : 0'
                for n in names('ltp')
            ),
        }
        lines = []
        for n in names('iozone'):
            needle, label = next(
                (needle, label) for needle, label in IOZONE_HEADERS if n.startswith(label + ' ')
            )
            child = n[len(label) + 1 : -len(' (kb/sec)')]
            lines.extend(
                [
                    needle,
                    'Children see throughput for ' + child + ' = 1 kB/sec',
                    'Max throughput per process = 1 kB/sec',
                ]
            )
        bodies['iozone'] = '\n'.join(lines)
        for group, body in bodies.items():
            with self.subTest(group=group):
                code, report = helper.analyze(group, body)
                self.assertEqual(code, 0, report['acceptance'])
                self.assertTrue(report['protocol']['event_ledger'])
                for item in report['groups'][0]['items']:
                    self.assertEqual(item['judgment'], 'PASS')
                    self.assertTrue(item['proof']['valid'])
                    self.assertTrue(item['proof']['event_lines'])

    def test_basic_historical_passes_require_scoped_judge_proof(self):
        helper = FinalEvidenceTests(methodName='test_orphan_time_cannot_disappear')
        self.addCleanup(helper.doCleanups)
        basic = (
            section_bytes(HISTORICAL / 'logs/ref_basic2.log', 'basic', 'glibc').decode().split('\n')
        )
        code, report = helper.analyze('basic', '\n'.join(basic[1:-2]))
        # 此历史样例本身有失败及损坏边界；不把重放声称为全通过。
        self.assertEqual(code, 1)
        passed = [i for i in report['groups'][0]['items'] if i['judgment'] == 'PASS']
        self.assertEqual(len(passed), 11)
        for item in passed:
            self.assertTrue(item['proof']['valid'])
            scoped = item['proof']['attempts'][0]['scoped_check']
            self.assertTrue(scoped['input_sha256'])
            self.assertEqual(scoped['all'], item['adapter']['all'])
            self.assertEqual(scoped['pass'], item['adapter']['pass'])

    def test_final_gate_independently_rejects_missing_or_misbound_proof(self):
        from unittest.mock import patch
        from test001 import analysis
        from test001.config import load_profile, load_criteria
        from test001.fsutil import sha256_bytes

        profile = load_profile(ROOT, 'regional-2025-with-ltp-v1')
        criteria = load_criteria(ROOT, J1)
        shard = profile.shard('unixbench-glibc')
        body = '\n'.join(n + ': 1' for n in names('unixbench'))
        raw = (
            '#### OS COMP TEST GROUP START unixbench-glibc ####\n'
            + body
            + '\n#### OS COMP TEST GROUP END unixbench-glibc ####'
        ).encode()
        original = analysis._apply_evidence
        for mutation in ('missing', 'case', 'range', 'event', 'ledger', 'unassigned', 'lost_row'):

            def tamper(*args):
                result = original(*args)
                item = result[0][0]
                if mutation == 'missing':
                    item.pop('proof')
                elif mutation == 'case':
                    item['proof']['name'] = 'other'
                elif mutation == 'range':
                    item['proof']['shard_id'] = 'other'
                elif mutation == 'event':
                    item['proof']['event_lines'] = [-1]
                elif mutation == 'ledger':
                    args[2].ledger[item['proof']['event_lines'][0]]['owner'] = 'other'
                elif mutation == 'unassigned':
                    args[2].ledger[item['proof']['event_lines'][0]]['disposition'] = 'unassigned'
                else:
                    del args[2].ledger[item['proof']['event_lines'][0]]
                return result

            with (
                self.subTest(mutation=mutation),
                patch.object(analysis, '_apply_evidence', side_effect=tamper),
            ):
                report = analysis.analyze_log(
                    profile,
                    criteria,
                    [shard],
                    raw,
                    origin='recorded_capture',
                    input_info={
                        'sha256': sha256_bytes(raw),
                        'script_exit_codes': {shard.shard_id: 0},
                    },
                    capture={
                        'status': 'EXITED',
                        'exit_code': 0,
                        'subject': 'reference_linux',
                        'platform': 'qemu_riscv64',
                    },
                    input_integrity='VERIFIED',
                    analysis_role='standalone',
                    campaign=None,
                )
                self.assertEqual(report['acceptance']['status'], 'BLOCKED')
                self.assertTrue(
                    any(r.startswith('UNPROVEN_PASS:') for r in report['acceptance']['reasons'])
                )

    def test_forged_adapter_passes_cannot_authorize_missing_values(self):
        from unittest.mock import patch
        from test001 import analysis
        from test001.adapters import AdapterOutput
        from test001.config import load_profile, load_criteria

        profile = load_profile(ROOT, 'regional-2025-with-ltp-v1')
        criteria = load_criteria(ROOT, J1)
        raw = (
            '#### OS COMP TEST GROUP START unixbench-glibc ####\n'
            + '\n'.join(n + ':' for n in names('unixbench'))
            + '\n#### OS COMP TEST GROUP END unixbench-glibc ####'
        ).encode()
        fake = AdapterOutput(
            {n: {'name': n, 'all': 1, 'pass': 1, 'score': 1} for n in names('unixbench')}
        )
        with patch.object(analysis, 'run_adapter', return_value=fake):
            report = analysis.analyze_log(
                profile,
                criteria,
                [profile.shard('unixbench-glibc')],
                raw,
                origin='recorded_capture',
                input_info={},
                capture={
                    'status': 'EXITED',
                    'exit_code': 0,
                    'subject': 'reference_linux',
                    'platform': 'qemu_riscv64',
                },
                input_integrity='VERIFIED',
                analysis_role='standalone',
                campaign=None,
            )
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertEqual(report['totals']['pass'], 0)
        self.assertTrue(all(i['adapter']['pass'] == 1 for i in report['groups'][0]['items']))


if __name__ == '__main__':
    unittest.main()
