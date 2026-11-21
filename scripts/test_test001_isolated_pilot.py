"""Independent expectations for the local design pilot; no kernel acceptance claim."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'tests/test001/fixtures/isolated-pilot'
SPEC = importlib.util.spec_from_file_location('isolated_pilot', ROOT / 'pilot.py')
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)


class IsolatedPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = {r['id']: r for r in json.loads((ROOT / 'samples.json').read_text())}

    def test_locked_real_runs_and_replay(self):
        index = json.loads((ROOT / 'index.json').read_text())
        self.assertEqual(index['kind'], 'isolated-design-pilot-not-production-acceptance')
        self.assertEqual(index['runs'], 30)
        for name, digest in index['files'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)
        for row in self.samples.values():
            plan, report = row['plan'], row['report']
            self.assertEqual(plan['pilot_sha256'], index['files']['pilot.py'])
            self.assertEqual(plan['init_sha256'], index['files']['init.sh'])
            self.assertEqual(hashlib.sha256((json.dumps(plan, indent=2) + '\n').encode()).hexdigest(), report['plan_sha256'])
            self.assertTrue(report['host']['reaped'])
            self.assertEqual(report['result']['acceptance'], 'BLOCKED')
            replay = PILOT.analyze(bytes.fromhex(row['serial_fragment_hex']), report['host'],
                                   plan['policy'], plan['result_name'])
            self.assertEqual(replay, report['result'])

    def test_real_success_and_failure_with_different_native_rules(self):
        for libc in ('musl', 'glibc'):
            for name, expected, status in (('lua-pass', 'PASS', 0), ('lua-missing', 'FAIL', 0),
                                           ('libctest-pass', 'PASS', 1), ('libctest-missing', 'FAIL', 1)):
                result = self.samples[f'{libc}-{name}']['report']['result']
                self.assertEqual(result['judgment'], expected)
                self.assertEqual(result['native_status'], status)
                self.assertEqual(result['residual'], '')

    def test_residual_timeout_and_forged_output_never_pass(self):
        for libc in ('musl', 'glibc'):
            residual = self.samples[f'{libc}-residual']['report']['result']
            self.assertEqual(residual['native_status'], 0)
            self.assertEqual(residual['reason'], 'residual-process')
            self.assertTrue(residual['residual'])
            timeout = self.samples[f'{libc}-timeout']['report']
            self.assertEqual(timeout['host']['status'], 'TIMEOUT')
            self.assertLess(timeout['host']['exit'], 0)
            self.assertEqual(timeout['result']['reason'], 'host-timeout')
            forged = self.samples[f'{libc}-lua-forged-diagnostic']['report']['result']
            self.assertEqual(forged['judgment'], 'BLOCKED')
            self.assertIn(b'PILOT STATUS 0', bytes.fromhex(forged['stdout_hex']))

    def test_missing_or_conflicting_evidence_cannot_preserve_pass(self):
        row = self.samples['glibc-libctest-pass']
        raw = bytes.fromhex(row['serial_fragment_hex'])
        host = row['report']['host']
        variants = [raw.replace(b'PILOT STATUS 1', b'PILOT STATUS 0'),
                    raw.replace(b'PILOT END', b'INCOMPLETE'),
                    raw + b'\r\nPILOT END\r\n',
                    raw.replace(b'PILOT stdout ', b'PILOT stdout ff')]
        misplaced = re.sub(rb'PILOT stdout ([0-9a-f]*)\r\nPILOT stderr \r\n',
                           rb'PILOT stdout \r\nPILOT stderr \1\r\n', raw)
        self.assertNotEqual(misplaced, raw)
        variants.append(misplaced)
        for variant in variants:
            result = PILOT.analyze(variant, host, 'libctest', 'entry-static.exe argv')
            self.assertEqual(result['judgment'], 'BLOCKED')
        result = PILOT.analyze(raw, dict(host, status='TIMEOUT', exit=0),
                               'libctest', 'entry-static.exe argv')
        self.assertEqual(result['judgment'], 'BLOCKED')

    def test_earlier_timeout_is_not_erased_by_later_completion(self):
        earlier = json.loads((ROOT / 'earlier-lmbench.json').read_text())
        self.assertEqual(len(earlier), 2)
        for old in earlier:
            current = self.samples[old['id']]
            self.assertEqual(old['report']['host']['status'], 'TIMEOUT')
            self.assertTrue(old['report']['host']['reaped'])
            self.assertEqual(current['report']['host']['status'], 'EXITED')
            self.assertEqual(old['plan']['initramfs_sha256'], current['plan']['initramfs_sha256'])
            self.assertEqual(old['report']['host']['limit_seconds'], current['report']['host']['limit_seconds'])
            self.assertEqual(current['report']['result']['judgment'], 'OBSERVATION')

    def test_environment_changes_do_not_become_benchmark_pass(self):
        missing = self.samples['musl-loader-missing']['report']['result']
        adapted = self.samples['musl-loader-adapted']['report']['result']
        self.assertEqual(missing['native_status'], 127)
        self.assertIn(b'not found', bytes.fromhex(missing['stderr_hex']))
        self.assertEqual(adapted['native_status'], 0)
        for libc in ('musl', 'glibc'):
            baseline = self.samples[f'{libc}-lmbench-default']['report']
            self.assertEqual(baseline['host']['status'], 'EXITED')
            self.assertIn(b'microseconds', bytes.fromhex(baseline['result']['stderr_hex']))
            for name in ('loader-adapted', 'lmbench-calibrated', 'unixbench-pipe', 'unixbench-whetstone'):
                result = self.samples[f'{libc}-{name}']['report']['result']
                self.assertEqual(result['judgment'], 'OBSERVATION')
            whet = self.samples[f'{libc}-unixbench-whetstone']['report']['result']
            self.assertIn(b'|0|MWIPS\n', bytes.fromhex(whet['stdout_hex']))
            net = self.samples[f'{libc}-netperf-address-config']['report']['result']
            self.assertEqual(net['native_status'], 90)
            self.assertIn(b'not supported', bytes.fromhex(net['stderr_hex']).lower())


if __name__ == '__main__':
    unittest.main()
