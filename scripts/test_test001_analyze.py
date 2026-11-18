"""analyze 的端到端行为：历史回放、外部边界、运行包与 LTP 负例。"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / 'tests/test001/fixtures/historical'
PROFILE = 'regional-2025-with-ltp-v1'
J1 = 'regional-2025-log-v1'
J2 = 'regional-2025-log-v2'
sys.path.insert(0, str(ROOT / 'scripts'))


def cli(*args, cwd=ROOT):
    command = [sys.executable, '-B', str(ROOT / 'scripts/test001.py'), *map(str, args)]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def load_report(path: Path) -> dict:
    return json.loads((path / 'report.json').read_text())


def section_bytes(log: Path, group: str, libc: str) -> bytes:
    text = log.read_text(errors='replace')
    match = re.search(
        rf'####\s*OS COMP TEST GROUP START {group}-{libc}\s*####\n'
        rf'.*?####\s*OS COMP TEST GROUP END {group}-{libc}\s*####\n',
        text, re.S,
    )
    if not match:
        raise AssertionError(f'section not found: {group}-{libc}')
    return match.group(0).encode('utf-8')


def profile_hash() -> str:
    return json.loads((ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.json').read_text())['profile_hash']


def make_run(base: Path, selection: list[str], serial: bytes, *,
             status: str = 'EXITED', exit_code: int | None = 0,
             integrity: str = 'VERIFIED', campaign: dict | None = None) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / 'serial.log').write_bytes(serial)
    firmware = b'fixture-firmware\n'
    (base / 'firmware.bin').write_bytes(firmware)
    capture = {
        'schema_version': 2, 'kind': 'test001_capture', 'run_id': str(uuid.uuid4()),
        'profile': {'profile_id': PROFILE, 'revision': 1, 'profile_hash': profile_hash()},
        'selection': selection, 'subject': 'reference_linux', 'platform': 'qemu_riscv64',
        'capture': {'status': status, 'exit_code': exit_code},
        'input_integrity': integrity,
        'serial': {'file': 'serial.log', 'sha256': hashlib.sha256(serial).hexdigest(),
                   'size': len(serial)},
        'build': {'manifest_sha256': 'a' * 64},
        'firmware': {'file': 'firmware.bin', 'sha256': hashlib.sha256(firmware).hexdigest(),
                     'size': len(firmware)},
        'campaign': campaign,
    }
    (base / 'capture.json').write_text(json.dumps(capture, indent=2, sort_keys=True))
    return base


LTP_CASE = 'test_1_to_1_addrs'


def ltp_log(cases: list[tuple[str, list[str], int]]) -> bytes:
    parts = ['#### OS COMP TEST GROUP START ltp-musl ####']
    for name, lines, code in cases:
        parts.append(f'RUN LTP CASE {name}')
        parts.extend(lines)
        parts.append(f'FAIL LTP CASE {name} : {code}')
    parts.append('#### OS COMP TEST GROUP END ltp-musl ####')
    return ('\n'.join(parts) + '\n').encode()


class TemporaryOutput(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def out(self, name='report'):
        return self.base / name


class HistoricalReplayTests(TemporaryOutput):
    """15 份固定历史结果逐项对照；来源始终是 external_log。"""

    def test_all_historical_samples_match_old_summaries(self):
        index = json.loads((HISTORICAL / 'index.json').read_text())
        self.assertEqual(len(index['cases']), 15)
        for case in index['cases']:
            with self.subTest(sample=case['sample']):
                output = self.out(case['sample'])
                result = cli(
                    'analyze', '--log', HISTORICAL / case['log'], '--profile', PROFILE,
                    '--criteria', J1, '--group', case['group'], '--libc', case['libc'],
                    '--out', output,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                report = load_report(output)
                self.assertEqual(report['origin'], 'external_log')
                self.assertEqual((report['subject'], report['platform']), ('unknown', 'unknown'))
                self.assertEqual(report['acceptance']['status'], 'BLOCKED')
                self.assertEqual(report['totals']['accepted_pass'], 0)
                group = report['groups'][0]
                expected = json.loads((HISTORICAL / case['expected']).read_text())
                expected_group = next(
                    item for item in expected['groups']
                    if item['group'] == case['group'] and item['libc'] == case['libc']
                )
                expected_status = {item['name']: item['status']
                                   for item in expected_group['subcases']}
                actual_status = {item['name']: item['judgment']
                                 for item in group['items']}
                self.assertEqual(actual_status, expected_status)
                if case['group'] == 'lmbench':
                    self.assertEqual(group['section'], 'TRUNCATED')
                    self.assertEqual(report['protocol']['status'], 'INCOMPLETE')
                else:
                    self.assertEqual(group['section'], 'COMPLETE')
                if case['group'] == 'busybox':
                    self.assertEqual(
                        [extra['name'] for extra in group['extras']],
                        ['busybox printf "abcn"', "busybox sh -c 'sleep 5' & ./busybox kill $!"],
                    )
                else:
                    self.assertEqual(group['extras'], [])
                source = (output / 'source.log').read_bytes()
                self.assertEqual(hashlib.sha256(source).hexdigest(), report['input']['sha256'])
                markdown = (output / 'report.md').read_bytes()
                self.assertEqual(hashlib.sha256(markdown).hexdigest(),
                                 report['report_md']['sha256'])
                self.assertEqual(sorted(path.name for path in output.iterdir()),
                                 ['report.json', 'report.md', 'source.log'])

    def test_external_all_pass_is_still_blocked(self):
        output = self.out()
        result = cli(
            'analyze', '--log', HISTORICAL / 'logs/ref_lua3.log', '--profile', PROFILE,
            '--criteria', J1, '--group', 'lua', '--libc', 'glibc', '--out', output,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['totals']['pass'], 9)
        self.assertEqual(report['totals']['selected_total'], 9)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('ORIGIN_external_log', report['acceptance']['reasons'])

    def test_require_pass_returns_one_for_external_log(self):
        output = self.out()
        result = cli(
            'analyze', '--log', HISTORICAL / 'logs/ref_lua3.log', '--profile', PROFILE,
            '--criteria', J1, '--group', 'lua', '--libc', 'glibc', '--out', output,
            '--require-pass',
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((output / 'report.json').is_file())


class InputOwnershipTests(TemporaryOutput):
    """输出独占、输入不跟随链接、未知选择在分析前拒绝。"""

    def test_existing_output_is_rejected_without_overwrite(self):
        output = self.out()
        output.mkdir()
        (output / 'sentinel').write_text('keep')
        result = cli(
            'analyze', '--log', HISTORICAL / 'logs/ref_lua3.log', '--profile', PROFILE,
            '--criteria', J1, '--group', 'lua', '--libc', 'glibc', '--out', output,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual((output / 'sentinel').read_text(), 'keep')

    def test_symlink_output_is_rejected(self):
        real = self.base / 'real'
        real.mkdir()
        link = self.base / 'link'
        link.symlink_to(real)
        result = cli(
            'analyze', '--log', HISTORICAL / 'logs/ref_lua3.log', '--profile', PROFILE,
            '--criteria', J1, '--group', 'lua', '--libc', 'glibc', '--out', link,
        )
        self.assertEqual(result.returncode, 2)

    def test_symlink_log_is_rejected(self):
        link = self.base / 'log'
        link.symlink_to(HISTORICAL / 'logs/ref_lua3.log')
        result = cli(
            'analyze', '--log', link, '--profile', PROFILE, '--criteria', J1,
            '--group', 'lua', '--libc', 'glibc', '--out', self.out(),
        )
        self.assertEqual(result.returncode, 2)

    def test_shard_alone_selects_group_and_libc(self):
        output = self.out()
        result = cli(
            'analyze', '--log', HISTORICAL / 'logs/ref_lua3.log', '--profile', PROFILE,
            '--criteria', J1, '--shard', 'lua-glibc', '--out', output,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['selection'],
                         [{'shard_id': 'lua-glibc', 'group': 'lua', 'libc': 'glibc',
                           'selected_total': 9}])

    def test_unknown_group_or_shard_is_rejected(self):
        for extra in (['--group', 'nope', '--libc', 'glibc'],
                      ['--shard', 'nope']):
            with self.subTest(extra=extra):
                result = cli(
                    'analyze', '--log', HISTORICAL / 'logs/ref_lua3.log', '--profile', PROFILE,
                    '--criteria', J1, *extra, '--out', self.out(),
                )
                self.assertEqual(result.returncode, 2)

    def test_missing_section_produces_blocked_report(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        only_lua = self.base / 'only-lua.log'
        only_lua.write_bytes(serial)
        output = self.out('missing')
        result = cli('analyze', '--log', only_lua, '--profile', PROFILE,
                     '--criteria', J1, '--group', 'basic', '--libc', 'glibc',
                     '--out', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        group = report['groups'][0]
        self.assertEqual(group['section'], 'ABSENT')
        self.assertTrue(all(item['judgment'] == 'NOT_RUN' for item in group['items']))
        self.assertIn('SECTION_ABSENT:basic-glibc', report['acceptance']['reasons'])

    def test_duplicate_section_is_protocol_error_and_keeps_first(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        duplicated = serial + serial
        log = self.base / 'duplicate.log'
        log.write_bytes(duplicated)
        output = self.out()
        result = cli('analyze', '--log', log, '--profile', PROFILE, '--criteria', J1,
                     '--group', 'lua', '--libc', 'glibc', '--out', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['protocol']['status'], 'ERROR')
        self.assertIn('duplicate section', ' '.join(report['protocol']['errors']))
        self.assertEqual(report['totals']['selected_total'], 9)

    def test_truncated_section_keeps_observed_values_but_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        serial = serial.replace(b'#### OS COMP TEST GROUP END lua-glibc ####\n', b'')
        log = self.base / 'truncated.log'
        log.write_bytes(serial)
        output = self.out()
        result = cli('analyze', '--log', log, '--profile', PROFILE, '--criteria', J1,
                     '--group', 'lua', '--libc', 'glibc', '--out', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        group = report['groups'][0]
        self.assertEqual(group['section'], 'TRUNCATED')
        self.assertEqual(group['counts']['pass'], 9)
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertIn('SECTION_TRUNCATED:lua-glibc', report['acceptance']['reasons'])


class LtpIntegrationTests(TemporaryOutput):
    """LTP 原生语义在完整 CLI 路径上的正反例。"""

    def analyze(self, serial: bytes, *, criteria=J1, require_pass=False):
        log = self.base / 'ltp.log'
        log.write_bytes(serial)
        output = self.out(str(uuid.uuid4()))
        args = ['analyze', '--log', log, '--profile', PROFILE, '--criteria', criteria,
                '--group', 'ltp', '--libc', 'musl', '--out', output]
        if require_pass:
            args.append('--require-pass')
        result = cli(*args)
        self.assertEqual(result.returncode, 0 if not require_pass else 1, result.stderr)
        return load_report(output)

    def find(self, report, name):
        return next(item for item in report['groups'][0]['items'] if item['name'] == name)

    def test_native_results_are_per_case(self):
        report = self.analyze(ltp_log([(LTP_CASE, ['TPASS: ok'], 0)]))
        self.assertEqual(self.find(report, LTP_CASE)['judgment'], 'PASS')
        self.assertEqual(report['groups'][0]['counts']['pass'], 1)
        self.assertEqual(report['groups'][0]['counts']['missing'], 2819)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')

    def test_tconf_blocks(self):
        report = self.analyze(ltp_log([(LTP_CASE, ['TPASS: ok', 'TCONF: skip'], 0)]))
        entry = self.find(report, LTP_CASE)
        self.assertEqual(entry['judgment'], 'FAIL')
        self.assertTrue(any('TCONF_1' in reason for reason in entry['reasons']))

    def test_duplicate_attempt_is_judge_error(self):
        report = self.analyze(ltp_log([
            (LTP_CASE, ['TPASS: ok'], 0),
            (LTP_CASE, ['TPASS: ok'], 0),
        ]))
        entry = self.find(report, LTP_CASE)
        self.assertEqual(entry['judgment'], 'JUDGE_ERROR')
        self.assertTrue(report['groups'][0]['judge_errors'])

    def test_missing_end_is_missing_and_blocks(self):
        serial = ltp_log([(LTP_CASE, ['TPASS: ok'], 0)])
        serial = serial.replace(f'FAIL LTP CASE {LTP_CASE} : 0\n'.encode(), b'')
        report = self.analyze(serial)
        entry = self.find(report, LTP_CASE)
        self.assertEqual(entry['judgment'], 'MISSING')
        self.assertIn('INCOMPLETE_ATTEMPT', entry['reasons'])

    def test_unknown_flag_is_judge_error(self):
        report = self.analyze(ltp_log([(LTP_CASE, ['TPASS: ok', 'TFOO: x'], 0)]))
        entry = self.find(report, LTP_CASE)
        self.assertEqual(entry['judgment'], 'JUDGE_ERROR')


class RealLtpSampleTests(TemporaryOutput):
    """冻结镜像的真实短时 LTP 样本：ANSI 颜色、两种 TPASS 格式和截断都要能读。"""

    def test_real_reference_sample_parses_native_results(self):
        fixture = ROOT / 'tests/test001/fixtures/ltp/reference-ltp-sample-2026-11-18.log'
        index = json.loads((fixture.parent / 'index.json').read_text())
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                         index['log']['fixture_sha256'])
        output = self.out()
        result = cli('analyze', '--log', fixture, '--profile', PROFILE, '--criteria', J1,
                     '--group', 'ltp', '--libc', 'glibc', '--out', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        group = report['groups'][0]
        self.assertEqual(report['origin'], 'external_log')
        self.assertEqual(report['protocol']['status'], 'INCOMPLETE')
        self.assertEqual(group['section'], 'TRUNCATED')
        self.assertGreaterEqual(group['counts']['pass'], 25)
        self.assertGreaterEqual(group['counts']['fail'], 60)
        self.assertGreater(group['counts']['missing'], 2000)
        self.assertEqual(group['extras'], [])
        self.assertTrue(group['judge_errors'])
        items = {item['name']: item for item in group['items']}
        self.assertEqual(items['abort01']['judgment'], 'PASS')
        self.assertEqual(items['accept02']['judgment'], 'FAIL')
        self.assertEqual(items['accept03']['judgment'], 'FAIL')
        self.assertGreaterEqual(group['native']['events'].get('TPASS', 0), 100)
        self.assertGreaterEqual(group['native']['events'].get('TCONF', 0), 20)


class RunPackageTests(TemporaryOutput):
    """运行包分析、重判和篡改拒绝。"""

    def run_analyze(self, run: Path, criteria: str, *, require_pass=False):
        output = self.out(str(uuid.uuid4()))
        args = ['analyze', '--run', run, '--criteria', criteria, '--out', output]
        if require_pass:
            args.append('--require-pass')
        result = cli(*args)
        return result, output

    def test_lua_run_passes_and_reanalysis_keeps_identity(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        first, first_out = self.run_analyze(run, J1)
        second, second_out = self.run_analyze(run, J2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        a = load_report(first_out)
        b = load_report(second_out)
        self.assertEqual(a['acceptance']['status'], 'PASS')
        self.assertEqual(b['acceptance']['status'], 'PASS')
        self.assertEqual(a['totals']['accepted_pass'], 9)
        self.assertEqual(b['totals']['accepted_pass'], 9)
        self.assertEqual(a['input']['run_id'], b['input']['run_id'])
        self.assertEqual(a['input']['capture_sha256'], b['input']['capture_sha256'])
        self.assertEqual(a['profile']['profile_hash'], b['profile']['profile_hash'])
        self.assertEqual(a['selection'], b['selection'])
        self.assertEqual(a['totals']['selected_total'], b['totals']['selected_total'])
        self.assertNotEqual(a['criteria']['criteria_hash'], b['criteria']['criteria_hash'])
        self.assertEqual(a['analysis_role'], 'standalone')
        self.assertEqual(sorted(path.name for path in first_out.iterdir()),
                         ['report.json', 'report.md'])
        self.assertEqual(sorted(path.name for path in second_out.iterdir()),
                         ['report.json', 'report.md'])

    def test_ltp_full_run_v1_passes_v2_requires_summary(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log([(name, ['TPASS: ok'], 0) for name in names])
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        first, first_out = self.run_analyze(run, J1)
        second, second_out = self.run_analyze(run, J2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        a = load_report(first_out)
        b = load_report(second_out)
        self.assertEqual(a['acceptance']['status'], 'PASS')
        self.assertEqual(a['totals']['accepted_pass'], 2820)
        self.assertEqual(b['acceptance']['status'], 'BLOCKED')
        self.assertEqual(b['totals']['selected_total'], 2820)
        self.assertEqual(b['groups'][0]['counts']['pass'], 2820)
        self.assertIn('LTP_SUMMARY_MISSING:ltp-musl:2820', b['acceptance']['reasons'])
        self.assertEqual(a['input']['capture_sha256'], b['input']['capture_sha256'])

    def test_tampered_serial_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        (run / 'serial.log').write_bytes(serial + b'x')
        result, _ = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 2)

    def test_extra_member_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        (run / 'extra').write_text('x')
        result, _ = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 2)

    def test_timeout_capture_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial,
                       status='TIMEOUT', exit_code=None)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('CAPTURE_TIMEOUT', report['acceptance']['reasons'])
        require, _ = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(require.returncode, 1)

    def test_campaign_binding_marks_reanalysis(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        campaign = {'campaign_id': str(uuid.uuid4()), 'slot_id': 'slot-1',
                    'campaign_sha256': 'b' * 64}
        run = make_run(self.base / 'run', ['lua-glibc'], serial, campaign=campaign)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['analysis_role'], 'reanalysis')
        self.assertEqual(report['campaign'], campaign)

    def test_run_rejects_selection_override(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result = cli('analyze', '--run', run, '--criteria', J1, '--profile', PROFILE,
                     '--out', self.out())
        self.assertEqual(result.returncode, 2)

    def test_extra_section_in_run_is_protocol_error(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        serial += section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'busybox', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['protocol']['status'], 'ERROR')
        self.assertIn('unselected sections', ' '.join(report['protocol']['errors']))

    def test_symlink_run_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        link = self.base / 'run-link'
        link.symlink_to(run)
        result = cli('analyze', '--run', link, '--criteria', J1, '--out', self.out())
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
