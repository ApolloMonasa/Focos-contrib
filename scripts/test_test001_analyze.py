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


def profile_image_hash() -> str:
    profile = json.loads((ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.json').read_text())
    return profile['inputs']['image']['uncompressed_sha256']


def make_run(base: Path, selection: list[str], serial: bytes, *,
             status: str = 'EXITED', exit_code: int | None = 0,
             integrity: str = 'VERIFIED', campaign: dict | None = None) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / 'serial.log').write_bytes(serial)
    firmware = b'fixture-firmware\n'
    (base / 'firmware.bin').write_bytes(firmware)
    build = {
        'manifest_sha256': 'a' * 64,
        'kernel_sha256': 'b' * 64,
        'initramfs_sha256': 'c' * 64,
        'base_img_sha256': profile_image_hash(),
    }
    qemu_sha = 'e' * 64
    firmware_sha = hashlib.sha256(firmware).hexdigest()
    capture = {
        'schema_version': 2, 'kind': 'test001_capture', 'run_id': str(uuid.uuid4()),
        'profile': {'profile_id': PROFILE, 'revision': 1, 'profile_hash': profile_hash()},
        'selection': selection, 'subject': 'reference_linux', 'platform': 'qemu_riscv64',
        'capture': {'status': status, 'exit_code': exit_code},
        'input_integrity': integrity,
        'serial': {'file': 'serial.log', 'sha256': hashlib.sha256(serial).hexdigest(),
                   'size': len(serial)},
        'build': build,
        'firmware': {
            'file': 'firmware.bin', 'sha256': firmware_sha, 'size': len(firmware),
            'source': 'fixture release', 'version': '1.7',
            'original_path': '/fixture/firmware.bin',
        },
        'qemu': {'path': '/usr/bin/qemu-system-riscv64', 'sha256': qemu_sha,
                 'version': '10.2.2'},
        'argv': [
            '/usr/bin/qemu-system-riscv64', '-machine', 'virt',
            '-m', '1G', '-smp', '1', '-nographic', '-no-reboot',
            '-bios', '/fixture/firmware.bin',
            '-kernel', '/build/kernel',
            '-initrd', '/build/initramfs',
            '-append', 'console=ttyS0 rdinit=/init',
            '-drive', 'file=/run/cow.qcow2,if=none,format=qcow2,id=x0',
            '-device', 'virtio-blk-device,drive=x0',
        ],
        'started_at': '2026-11-18T00:00:00+00:00',
        'finished_at': '2026-11-18T00:01:00+00:00',
        'limits': {'timeout_seconds': 180, 'serial_bytes': 268435456,
                   'output_bytes': 1048576},
        'cow_layer': {
            'path': '/run/cow.qcow2', 'format': 'qcow2',
            'sha256': '1' * 64, 'size': 65536, 'created': True,
            'base_img_sha256': build['base_img_sha256'],
        },
        'read_only_inputs': [
            {'role': 'kernel', 'path': '/build/kernel',
             'sha256_before': build['kernel_sha256'],
             'sha256_after': build['kernel_sha256']},
            {'role': 'initramfs', 'path': '/build/initramfs',
             'sha256_before': build['initramfs_sha256'],
             'sha256_after': build['initramfs_sha256']},
            {'role': 'base_img', 'path': '/build/base.img',
             'sha256_before': build['base_img_sha256'],
             'sha256_after': build['base_img_sha256']},
            {'role': 'firmware', 'path': '/fixture/firmware.bin',
             'sha256_before': firmware_sha, 'sha256_after': firmware_sha},
            {'role': 'qemu', 'path': '/usr/bin/qemu-system-riscv64',
             'sha256_before': qemu_sha, 'sha256_after': qemu_sha},
        ],
        'capture_implementation': {
            'files': [{'path': 'capture/main.py', 'sha256': 'f' * 64}],
        },
        'scripts': [{'shard_id': shard_id, 'exit_code': 0}
                    for shard_id in selection],
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


def ltp_log_with_summaries(names: list[str], *, incomplete_name: str | None = None,
                           mismatch_name: str | None = None) -> bytes:
    parts = ['#### OS COMP TEST GROUP START ltp-musl ####']
    for name in names:
        parts.append(f'RUN LTP CASE {name}')
        parts.append('TPASS: ok')
        parts.append('Summary:')
        parts.append('passed   2' if name == mismatch_name else 'passed   1')
        if name != incomplete_name:
            parts.extend(['failed   0', 'broken   0', 'skipped  0', 'warnings 0'])
        parts.append(f'FAIL LTP CASE {name} : 0')
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

    def test_performance_metrics_are_preserved(self):
        for group in ('libcbench', 'lmbench'):
            with self.subTest(group=group):
                output = self.out(group)
                result = cli(
                    'analyze', '--log', HISTORICAL / 'logs/ref_perf.log',
                    '--profile', PROFILE, '--criteria', J1,
                    '--group', group, '--libc', 'glibc', '--out', output,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                report = load_report(output)
                metrics = report['groups'][0].get('metrics', [])
                self.assertTrue(metrics)
                self.assertTrue(all(metric['unit'] for metric in metrics))
                self.assertTrue(all(metric['line'] > 0 for metric in metrics))
                self.assertGreater(metrics[0]['line'], 600)

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


class RunContractTests(TemporaryOutput):
    """运行包必须带完整采集身份和前后完整性记录，不能只信 VERIFIED。"""

    def mutate(self, run: Path, callback):
        path = run / 'capture.json'
        capture = json.loads(path.read_text())
        callback(capture)
        path.write_text(json.dumps(capture, indent=2, sort_keys=True))

    def assert_rejected(self, run: Path):
        output = self.out(str(uuid.uuid4()))
        result = cli('analyze', '--run', run, '--criteria', J1,
                     '--out', output, '--require-pass')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(output.exists())

    def analyze_run(self, run: Path, criteria: str, *, require_pass=False):
        output = self.out(str(uuid.uuid4()))
        args = ['analyze', '--run', run, '--criteria', criteria, '--out', output]
        if require_pass:
            args.append('--require-pass')
        result = cli(*args)
        return result, output

    def test_missing_qemu_identity_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture.pop('qemu'))
        self.assert_rejected(run)

    def test_missing_integrity_records_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture.pop('read_only_inputs'))
        self.assert_rejected(run)

    def test_missing_capture_implementation_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture.pop('capture_implementation'))
        self.assert_rejected(run)

    def test_missing_argv_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture.pop('argv'))
        self.assert_rejected(run)

    def test_verified_but_changed_read_only_input_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)

        def changed(capture):
            capture['read_only_inputs'][0]['sha256_after'] = '0' * 64
        self.mutate(run, changed)
        self.assert_rejected(run)

    def test_bios_not_private_firmware_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)

        def bad_bios(capture):
            index = capture['argv'].index('-bios') + 1
            capture['argv'][index] = 'default'
        self.mutate(run, bad_bios)
        self.assert_rejected(run)

    def test_unverified_kernel_argv_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)

        def other_kernel(capture):
            index = capture['argv'].index('-kernel') + 1
            capture['argv'][index] = '/unverified/other-kernel'
        self.mutate(run, other_kernel)
        self.assert_rejected(run)

    def test_unverified_qemu_executable_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture['argv'].__setitem__(
            0, '/unverified/qemu-system-riscv64'))
        self.assert_rejected(run)

    def test_extra_blockdev_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture['argv'].extend(
            ['-blockdev', 'node-name=unverified']))
        self.assert_rejected(run)

    def test_extra_device_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture['argv'].extend(
            ['-device', 'virtio-blk-device,drive=unverified']))
        self.assert_rejected(run)

    def test_duplicate_bios_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture['argv'].extend(['-bios', 'default']))
        self.assert_rejected(run)

    def test_base_drive_with_readonly_off_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)

        def base_drive(capture):
            index = capture['argv'].index('-drive') + 1
            capture['argv'][index] = (
                'file=/build/base.img,if=none,format=raw,readonly=off,id=x0'
            )
        self.mutate(run, base_drive)
        self.assert_rejected(run)

    def test_cow_drive_readonly_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)

        def readonly_cow(capture):
            index = capture['argv'].index('-drive') + 1
            capture['argv'][index] = (
                'file=/run/cow.qcow2,if=none,format=qcow2,readonly=on,id=x0'
            )
        self.mutate(run, readonly_cow)
        self.assert_rejected(run)

    def test_cow_drive_format_mismatch_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)

        def wrong_format(capture):
            index = capture['argv'].index('-drive') + 1
            capture['argv'][index] = (
                'file=/run/cow.qcow2,if=none,format=raw,id=x0'
            )
        self.mutate(run, wrong_format)
        self.assert_rejected(run)

    def test_base_image_not_frozen_profile_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        wrong = 'd' * 64

        def wrong_image(capture):
            capture['build']['base_img_sha256'] = wrong
            capture['cow_layer']['base_img_sha256'] = wrong
            for item in capture['read_only_inputs']:
                if item['role'] == 'base_img':
                    item['sha256_before'] = wrong
                    item['sha256_after'] = wrong
        self.mutate(run, wrong_image)
        self.assert_rejected(run)

    def test_nonzero_script_exit_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture['scripts'][0].__setitem__('exit_code', 1))
        result, output = self.analyze_run(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('SCRIPT_EXIT_lua-glibc:1', report['acceptance']['reasons'])
        self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_missing_script_exit_is_rejected(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        self.mutate(run, lambda capture: capture.pop('scripts'))
        self.assert_rejected(run)

    def test_changed_integrity_blocks_but_package_is_analyzed(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        run = make_run(self.base / 'run', ['lua-glibc'], serial, integrity='CHANGED')

        def changed(capture):
            capture['read_only_inputs'][0]['sha256_after'] = '0' * 64
        self.mutate(run, changed)
        result, output = self.analyze_run(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('INPUT_INTEGRITY_CHANGED', report['acceptance']['reasons'])


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

    def libctest_section(self, *, pass_after_end=False, noncanonical_first=False):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows
                 if row['group'] == 'libctest' and row['libc'] == 'glibc']
        parts = ['#### OS COMP TEST GROUP START libctest-glibc ####']
        for index, name in enumerate(names):
            _, kind, case = name.split(' ', 2)
            raw = f'entry-{kind}.exe {case}'
            if noncanonical_first and index == 0:
                parts += [
                    f'========== START {raw} ========== trailing',
                    'Fail!',
                    f'========== END {raw} ========== trailing',
                ]
            if pass_after_end:
                parts += [
                    f'========== START {raw} ==========',
                    f'========== END {raw} ==========',
                    'Pass!',
                ]
            else:
                parts += [
                    f'========== START {raw} ==========',
                    'Pass!',
                    f'========== END {raw} ==========',
                ]
        parts.append('#### OS COMP TEST GROUP END libctest-glibc ####')
        return ('\n'.join(parts) + '\n').encode(), len(names)

    def test_libctest_duplicate_pass_blocks(self):
        serial, _ = self.libctest_section()
        serial = serial.replace(b'Pass!\n', b'Pass!\nPass!\n', 1)
        run = make_run(self.base / 'run', ['libctest-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        first = report['groups'][0]['items'][0]
        self.assertIn('DUPLICATE_PASS:2', first['reasons'])
        self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_libctest_pass_after_end_blocks(self):
        serial, count = self.libctest_section(pass_after_end=True)
        run = make_run(self.base / 'run', ['libctest-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertEqual(report['groups'][0]['counts']['pass'], 0)
        self.assertEqual(report['totals']['accepted_pass'], 0)
        first = report['groups'][0]['items'][0]
        self.assertIn('NO_IN_ATTEMPT_PASS', first['reasons'])
        self.assertTrue(any('MALFORMED_OBSERVATION' in error
                            for error in report['groups'][0]['judge_errors']))

    def test_libctest_noncanonical_marker_blocks(self):
        serial, count = self.libctest_section(noncanonical_first=True)
        run = make_run(self.base / 'run', ['libctest-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertEqual(report['groups'][0]['counts']['pass'], count - 1)
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertTrue(any('MALFORMED_OBSERVATION' in error
                            for error in report['groups'][0]['judge_errors']))

    def test_libctest_missing_case_ends_blocks(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows
                 if row['group'] == 'libctest' and row['libc'] == 'glibc']
        parts = ['#### OS COMP TEST GROUP START libctest-glibc ####']
        for name in names:
            _, kind, case = name.split(' ', 2)
            parts.append(f'========== START entry-{kind}.exe {case} ==========')
            parts.append('Pass!')
        parts.append('#### OS COMP TEST GROUP END libctest-glibc ####')
        serial = ('\n'.join(parts) + '\n').encode()
        run = make_run(self.base / 'run', ['libctest-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertEqual(report['groups'][0]['counts']['pass'], len(names))
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertTrue(any('MALFORMED_OBSERVATION' in error
                            for error in report['groups'][0]['judge_errors']))

    def test_lua_trailing_failure_is_not_overwritten(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        serial = serial.replace(
            b'testcase lua date.lua success',
            b'testcase lua date.lua fail trailing-diagnostic\n'
            b'testcase lua date.lua success',
            1,
        )
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertEqual(report['totals']['accepted_pass'], 0)
        date = next(item for item in report['groups'][0]['items']
                    if item['name'] == 'lua date.lua')
        self.assertEqual(date['judgment'], 'JUDGE_ERROR')
        self.assertIn('DUPLICATE_OBSERVATION:2/1', date['reasons'])

    def test_lua_malformed_testcase_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        serial = serial.replace(
            b'testcase lua date.lua success',
            b'testcase lua date.lua unknown\n'
            b'testcase lua date.lua success',
            1,
        )
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertTrue(any('MALFORMED_OBSERVATION' in error
                            for error in report['groups'][0]['judge_errors']))

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

    def test_lua_fail_then_success_cannot_pass(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        serial = serial.replace(
            b'testcase lua date.lua success\n',
            b'testcase lua date.lua fail\ntestcase lua date.lua success\n',
            1,
        )
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        item = next(item for item in report['groups'][0]['items']
                    if item['name'] == 'lua date.lua')
        self.assertEqual(item['judgment'], 'JUDGE_ERROR')
        self.assertEqual(item['observations'], ['fail', 'success'])
        self.assertTrue(any('DUPLICATE_OBSERVATION' in reason for reason in item['reasons']))
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')

    def test_unknown_marker_is_reported_as_extra(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_net4.log', 'iperf', 'glibc')
        serial = serial.replace(
            b'#### OS COMP TEST GROUP END iperf-glibc ####',
            b'===== iperf surprise end: success =====\n'
            b'#### OS COMP TEST GROUP END iperf-glibc ####',
        )
        run = make_run(self.base / 'run', ['iperf-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        group = report['groups'][0]
        self.assertIn('surprise', [extra['name'] for extra in group['extras']])
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertTrue(any('EXTRA_ITEMS' in reason for reason in report['acceptance']['reasons']))

    def test_unknown_iozone_metric_is_reported_as_extra(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_perf.log', 'iozone', 'glibc')
        serial = serial.replace(
            b'#### OS COMP TEST GROUP END iozone-glibc ####',
            b'iozone throughput write/read measurements\n'
            b'Children see throughput for surprise writers\n'
            b'Max throughput per process = 123.4\n'
            b'#### OS COMP TEST GROUP END iozone-glibc ####',
        )
        run = make_run(self.base / 'run', ['iozone-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        group = report['groups'][0]
        self.assertIn('iozone write/read surprise writers (kb/sec)',
                      [extra['name'] for extra in group['extras']])
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')

    def test_busybox_planned_duplicate_is_not_a_retry(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'busybox', 'glibc')
        run = make_run(self.base / 'run', ['busybox-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        group = report['groups'][0]
        item = next(item for item in group['items']
                    if item['name'] == 'busybox echo "bbbbbbb" >> test.txt')
        self.assertEqual(item['judgment'], 'PASS')
        self.assertEqual(item['observations'], ['success', 'success'])
        self.assertFalse(any('DUPLICATE_OBSERVATION' in error for error in group['judge_errors']))

    def test_busybox_planned_duplicate_with_failure_blocks(self):
        serial = (
            b'#### OS COMP TEST GROUP START busybox-glibc ####\n'
            b'testcase busybox echo "bbbbbbb" >> test.txt fail\n'
            b'testcase busybox echo "bbbbbbb" >> test.txt success\n'
            b'#### OS COMP TEST GROUP END busybox-glibc ####\n'
        )
        run = make_run(self.base / 'run', ['busybox-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        item = next(item for item in report['groups'][0]['items']
                    if item['name'] == 'busybox echo "bbbbbbb" >> test.txt')
        self.assertEqual(item['judgment'], 'FAIL')
        self.assertEqual(item['observations'], ['fail', 'success'])
        self.assertIn('OBSERVED_FAILURE', item['reasons'])

    def test_busybox_unexpected_duplicate_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'busybox', 'glibc')
        serial = serial.replace(
            b'#### OS COMP TEST GROUP END busybox-glibc ####',
            b'testcase busybox date fail\n#### OS COMP TEST GROUP END busybox-glibc ####',
        )
        run = make_run(self.base / 'run', ['busybox-glibc'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        item = next(item for item in report['groups'][0]['items']
                    if item['name'] == 'busybox date')
        self.assertEqual(item['judgment'], 'JUDGE_ERROR')
        self.assertTrue(any('DUPLICATE_OBSERVATION' in reason for reason in item['reasons']))

    def test_cross_group_result_inside_lua_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        end = b'#### OS COMP TEST GROUP END lua-glibc ####'
        serial = serial.replace(end, b'TFAIL: delayed\n' + end)
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['protocol']['status'], 'ERROR')
        self.assertIn('CROSS_GROUP_RESULT:inside:1',
                      report['acceptance']['reasons'])
        self.assertEqual(report['protocol']['unattributed'][0]['kind'], 'cross_group')
        self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_unknown_ltp_flag_outside_invocation_blocks(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log([(name, ['TPASS: ok'], 0) for name in names])
        end = b'#### OS COMP TEST GROUP END ltp-musl ####'
        serial = serial.replace(end, b'TWHAT: delayed\n' + end)
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('LTP_UNKNOWN_RESULT:ltp-musl:1',
                      report['acceptance']['reasons'])
        self.assertEqual(report['groups'][0]['native']['unattributed'][0]['event'],
                         'TWHAT')
        self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_unattributed_ltp_failure_blocks(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log([(name, ['TPASS: ok'], 0) for name in names])
        end = b'#### OS COMP TEST GROUP END ltp-musl ####'
        serial = serial.replace(
            end, b'TFAIL: delayed failure outside invocation\n' + end
        )
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('LTP_UNATTRIBUTED_RESULT:ltp-musl:1',
                      report['acceptance']['reasons'])
        self.assertEqual(report['totals']['accepted_pass'], 0)
        unattributed = report['groups'][0]['native']['unattributed']
        self.assertEqual(unattributed[0]['event'], 'TFAIL')
        self.assertGreater(unattributed[0]['line'], 0)

    def test_non_ltp_result_after_group_end_blocks(self):
        serial = section_bytes(HISTORICAL / 'logs/ref_lua3.log', 'lua', 'glibc')
        serial += b'testcase lua date.lua fail\n'
        run = make_run(self.base / 'run', ['lua-glibc'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['protocol']['status'], 'ERROR')
        self.assertIn('NON_LTP_UNATTRIBUTED_RESULT:outside:1',
                      report['acceptance']['reasons'])
        self.assertEqual(report['protocol']['unattributed'][0]['event'], 'testcase')
        self.assertEqual(report['totals']['accepted_pass'], 0)

    def test_ltp_failure_after_group_end_blocks(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log([(name, ['TPASS: ok'], 0) for name in names])
        serial += b'TFAIL: delayed failure after group end\n'
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        result, output = self.run_analyze(run, J1, require_pass=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = load_report(output)
        self.assertEqual(report['protocol']['status'], 'ERROR')
        self.assertIn('LTP_UNATTRIBUTED_RESULT:outside:1',
                      report['acceptance']['reasons'])
        self.assertEqual(report['protocol']['unattributed'][0]['event'], 'TFAIL')
        self.assertGreater(report['protocol']['unattributed'][0]['line'], 0)
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertEqual(report['groups'][0]['native']['unattributed'], [])

    def test_ltp_summary_mismatch_blocks_j1(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log_with_summaries(names, mismatch_name=names[0])
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('LTP_SUMMARY_MISMATCH:ltp-musl:1', report['acceptance']['reasons'])
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertEqual(report['groups'][0]['counts']['pass'], 2820)

    def test_ltp_reversed_order_blocks(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log([(name, ['TPASS: ok'], 0) for name in reversed(names)])
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        result, output = self.run_analyze(run, J1)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = load_report(output)
        self.assertEqual(report['acceptance']['status'], 'BLOCKED')
        self.assertIn('LTP_ORDER_MISMATCH:ltp-musl', report['acceptance']['reasons'])
        self.assertEqual(report['totals']['accepted_pass'], 0)
        self.assertEqual(report['groups'][0]['counts']['pass'], 2820)

    def test_incomplete_ltp_summary_blocks_j2(self):
        rows = [json.loads(line) for line in
                (ROOT / 'tests/test001/profiles/regional-2025-with-ltp-v1.invocations.jsonl')
                .read_text().splitlines()]
        names = [row['name'] for row in rows if row['group'] == 'ltp' and row['libc'] == 'musl']
        serial = ltp_log_with_summaries(names, incomplete_name=names[0])
        run = make_run(self.base / 'run', ['ltp-musl'], serial)
        first, first_out = self.run_analyze(run, J1)
        second, second_out = self.run_analyze(run, J2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        a = load_report(first_out)
        b = load_report(second_out)
        self.assertEqual(a['acceptance']['status'], 'PASS')
        self.assertEqual(b['acceptance']['status'], 'BLOCKED')
        self.assertIn('LTP_SUMMARY_INCOMPLETE:ltp-musl:1', b['acceptance']['reasons'])
        self.assertEqual(b['groups'][0]['native']['summary_missing'], 0)
        self.assertEqual(b['groups'][0]['native']['summary_incomplete'], 1)

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
