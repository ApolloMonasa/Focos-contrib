"""固定输出函数实验的独立期望；不把实验当作比赛用例成绩。"""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'tests/test001/fixtures/output-audit'


class OutputProbeTests(unittest.TestCase):
    def setUp(self):
        self.index = json.loads((ROOT / 'index.json').read_text())
        self.samples = json.loads((ROOT / 'samples.json').read_text())

    def test_provenance_and_complete_case_set(self):
        for name in ('samples', 'probe'):
            path = ROOT / (name + ('.json' if name == 'samples' else '.py'))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.index[name + '_sha256'])
        expected = {f'{name}-{color}' for name in ('modern-c', 'old-c') for color in ('0', '1')}
        expected |= {f'{lib}-{mode}-{color}' for lib in ('tst_test.sh', 'test.sh')
                     for mode in ('pass', 'skip', 'mixed') for color in ('0', '1')}
        expected |= {'wrapper-musl', 'wrapper-glibc'}
        self.assertEqual(set(self.samples), expected)
        self.assertEqual(self.index['kind'], 'source-function-probe-not-contest-execution')

    def test_modern_and_old_formats_differ(self):
        modern = bytes.fromhex(self.samples['modern-c-0']['stderr_hex'])
        old = bytes.fromhex(self.samples['old-c-0']['stdout_hex'])
        self.assertTrue(modern.startswith(b'probe.c:7: TPASS: '))
        self.assertIn(b'Summary:\npassed   1\nfailed   1\nbroken   1\nskipped  1\nwarnings 1\n', modern)
        self.assertTrue(old.startswith(b'probe       1  TPASS  :  '))
        self.assertNotIn(b'Summary', old)
        self.assertNotIn(b'Total Tests', old)
        self.assertEqual(self.samples['modern-c-0']['exit'], 7)
        self.assertEqual(self.samples['old-c-0']['exit'], 39)
        for name, stream in (('modern-c', 'stderr_hex'), ('old-c', 'stdout_hex')):
            colored = bytes.fromhex(self.samples[name + '-1'][stream])
            self.assertIn(b'\x1b[1;32m', colored)
            self.assertIn(b'\x1b[1;35m', colored)
            self.assertIn(b'\x1b[1;34m', colored)

    def test_shell_skip_can_coexist_with_exit_zero(self):
        for lib, stream in (('tst_test.sh', 'stderr_hex'), ('test.sh', 'stdout_hex')):
            for color in ('0', '1'):
                self.assertEqual(self.samples[f'{lib}-pass-{color}']['exit'], 0)
                self.assertEqual(self.samples[f'{lib}-skip-{color}']['exit'], 32)
                self.assertEqual(self.samples[f'{lib}-mixed-{color}']['exit'], 0)
            mixed = bytes.fromhex(self.samples[f'{lib}-mixed-0'][stream])
            self.assertIn(b'TCONF: probe skip', mixed)
        modern = bytes.fromhex(self.samples['tst_test.sh-mixed-0']['stderr_hex'])
        self.assertIn(b'passed   1\nfailed   0\nbroken   0\nskipped  1\nwarnings 0\n', modern)
        old = bytes.fromhex(self.samples['test.sh-mixed-0']['stdout_hex'])
        self.assertNotIn(b'Summary', old)

    def test_wrapper_fail_word_is_not_failure_verdict(self):
        for libc in ('musl', 'glibc'):
            sample = self.samples['wrapper-' + libc]
            self.assertEqual(sample['exit'], 0)
            self.assertEqual(bytes.fromhex(sample['stderr_hex']), b'')
            self.assertEqual(bytes.fromhex(sample['stdout_hex']), (
                f'#### OS COMP TEST GROUP START ltp-{libc} ####\n'
                'RUN LTP CASE exit0\nFAIL LTP CASE exit0 : 0\n'
                'RUN LTP CASE exit1\nFAIL LTP CASE exit1 : 1\n'
                f'#### OS COMP TEST GROUP END ltp-{libc} ####\n').encode())


if __name__ == '__main__':
    unittest.main()
