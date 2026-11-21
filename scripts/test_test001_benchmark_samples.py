"""保护实测反例与证据边界；不判定 benchmark 成绩，不调用生产解析器。"""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'tests/test001/fixtures/benchmark-output'
GROUPS = {'basic', 'busybox', 'lua', 'libctest', 'libcbench', 'cyclictest',
          'iperf', 'netperf', 'iozone', 'lmbench', 'unixbench', 'ltp_subset'}


class BenchmarkOutputSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((ROOT / 'index.json').read_text())
        cls.samples = json.loads((ROOT / 'samples.json').read_text())

    def raw(self, experiment, libc):
        return bytes.fromhex(self.samples[f'{experiment}-{libc}']['serial_hex'])

    def block(self, experiment, libc, name):
        raw = self.raw(experiment, libc)
        start = raw.index(f'OUTPUT_AUDIT_BEGIN {name}\r\n'.encode())
        end = re.search(rb'OUTPUT_AUDIT_END ' + name.encode() + rb' status=\d+\r\n', raw[start:])
        self.assertIsNotNone(end)
        return raw[start:start + end.end()]

    def test_evidence_identity_and_coverage(self):
        self.assertEqual(self.index['kind'], 'reference-linux-output-audit-not-acceptance')
        expected = {f'{e}-{c}' for e in ('original', 'supplement', 'format-probes', 'timing-probes')
                    for c in ('musl', 'glibc')}
        self.assertEqual(set(self.samples), expected)
        self.assertEqual({r['id'] for r in self.index['runs']}, expected)
        self.assertEqual(len(self.index['runs']), 8)
        for name, digest in self.index['files'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)
        for run in self.index['runs']:
            raw = bytes.fromhex(self.samples[run['id']]['serial_hex'])
            self.assertEqual(len(raw), run['fragment_length'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), run['fragment_sha256'])
            self.assertEqual(run['host_exit'], 0)  # QEMU exit only, not test verdict.
            self.assertTrue(raw.endswith(b'OUTPUT_AUDIT_DONE\r\n'))
            if run['experiment'] == 'original':
                self.assertEqual({g['name'] for g in run['groups']}, GROUPS)
                self.assertEqual(len(run['groups']), 12)

    def test_group_end_zero_can_hide_start_failure(self):
        for libc in ('musl', 'glibc'):
            raw = self.block('original', libc, 'basic')
            self.assertIn(b'Permission denied', raw)
            self.assertIn(f'GROUP END basic-{libc}'.encode(), raw)
            self.assertIn(b'END basic status=0', raw)
        raw = self.block('original', 'musl', 'unixbench')
        self.assertIn(b'not found', raw)
        self.assertIn(b'END unixbench status=0', raw)

    def test_libctest_success_and_failure_both_return_one(self):
        for libc in ('musl', 'glibc'):
            good = self.block('supplement', libc, 'libctest_pass_exit')
            bad = self.block('supplement', libc, 'libctest_missing_exit')
            self.assertIn(b'Pass!\r\n', good)
            self.assertNotIn(b'FAIL ', good)
            self.assertIn(b'FAIL ', bad)
            self.assertNotIn(b'Pass!', bad)
            self.assertIn(b'status=1\r\n', good)
            self.assertIn(b'status=1\r\n', bad)

    def test_directory_name_does_not_establish_libc(self):
        members = json.loads((ROOT / 'image-members.json').read_text())
        basic = [n for n in members if n.startswith('/musl/basic/')]
        self.assertEqual(len(basic), 36)
        for name in basic:
            self.assertEqual(members[name], members[name.replace('/musl/', '/glibc/', 1)])
        elf = json.loads((ROOT / 'elf-observations.json').read_text())
        self.assertIn('/lib/ld-linux-riscv64-lp64d.so.1', elf['musl/basic/getpid']['stdout'])
        self.assertRegex(elf['musl/iozone']['stdout'], r'Flags:\s+0x0\n')
        self.assertIn('/lib/ld-musl-riscv64-sf.so.1', elf['musl/iozone']['stdout'])
        self.assertIn('double-float ABI', elf['musl/ltp/testcases/bin/getpid01']['stdout'])

    def test_units_and_anonymous_rows_require_context(self):
        for libc in ('musl', 'glibc'):
            whet = self.block('format-probes', libc, 'unix_whetstone-double')
            self.assertRegex(whet, rb'COUNT\|[0-9.]+\|0\|MWIPS\r\n')
            net = self.block('supplement', libc, 'netperf_loader')
            if libc == 'musl':
                self.assertIn(b'10^6bits/sec', net)
                self.assertIn(b'Trans.', net)
            else:
                self.assertIn(b'could not resolve host', net)
                self.assertEqual(net.count(b'end: fail'), 5)
            bw = self.block('timing-probes', libc, 'lm_bw_file')
            mmap = self.block('timing-probes', libc, 'lm_mmap')
            for block in (bw, mmap):
                self.assertRegex(block, rb'\r\n0\.524288 [0-9.]+\r\n')
            self.assertIn(b'"size=32k ovr=', self.block('timing-probes', libc, 'lm_ctx'))
        self.assertIn(b'WHETSTONE test(MFLOPS):', self.block('original', 'glibc', 'unixbench'))

    def test_timeouts_and_native_ltp_errors_are_preserved(self):
        for libc in ('musl', 'glibc'):
            raw = self.block('original', libc, 'lmbench')
            self.assertIn(b'END lmbench status=137', raw)
        ltp = self.block('supplement', 'glibc', 'ltp_subset')
        self.assertIn(b'TBROK:', ltp)
        self.assertIn(b'FAIL LTP CASE access01 : 2', ltp)
        self.assertIn(b'FAIL LTP CASE getpid01 : 0', ltp)
        self.assertGreater(ltp.count(b'TPASS:'), 4)
        self.assertIn(b'not found', self.block('supplement', 'musl', 'ltp_subset'))


if __name__ == '__main__':
    unittest.main()
