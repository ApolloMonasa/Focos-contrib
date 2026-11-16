"""实际执行生成的 shell（仅替换机器操作），验证失败边界和确定性归档。"""
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('refbuild', ROOT / 'scripts/ref/build_initramfs.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
RELEASE = json.loads((ROOT/'tests/reference.lock.json').read_text())['kernel']['release']


class BuildTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.busybox = self.root / 'busybox'
        self.busybox.write_bytes(b'synthetic busybox')
        self.apk = self.root / 'kernel.apk'
        self.write_apk(build.MODULES + ['dummy'])

    def write_apk(self, modules, release=RELEASE):
        with tarfile.open(self.apk, 'w:gz') as archive:
            for name, raw in [('boot/vmlinuz-lts', b'kernel')] + [
                (f'lib/modules/{release}/kernel/{m}.ko.gz', b'module:' + m.encode()) for m in modules
            ]:
                data = gzip.compress(raw, mtime=0)
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))

    def invoke(self, out, extra=()):
        args = ['--image', str(self.busybox), '--kernel-apk', str(self.apk),
                '--busybox', str(self.busybox), '--out', str(out),
                '--kernel-out', str(out.parent / 'Image'), '--modules-dir', str(out.parent / 'modules'),
                '--scripts', 'lua_testcode.sh', '--allow-unpinned-inputs'] + list(extra)
        with contextlib.redirect_stdout(io.StringIO()):
            return build.main(args)

    def test_same_payload_has_identical_archive_across_time_and_filename(self):
        a, b = self.root / 'one/a.gz', self.root / 'two/b.gz'
        with patch('time.time', return_value=1000):
            self.invoke(a)
        with patch('time.time', return_value=2000):
            self.invoke(b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(json.loads(a.with_suffix('.manifest.json').read_text()),
                         json.loads(b.with_suffix('.manifest.json').read_text()))

    def test_missing_modules_wrong_release_and_requested_dummy_fail(self):
        for modules, release, args in [
            (build.MODULES[:-1], RELEASE, ()),
            (build.MODULES, 'wrong-release', ()),
            (build.MODULES, RELEASE, ('--network-dummy',)),
        ]:
            with self.subTest(modules=modules, release=release):
                self.write_apk(modules, release)
                out = self.root / 'rejected.gz'
                with self.assertRaises((ValueError, SystemExit)):
                    self.invoke(out, args)
                self.assertFalse(out.exists())

    def test_unpinned_inputs_are_rejected_by_default(self):
        with self.assertRaises((ValueError, SystemExit)):
            build.main(['--image', str(self.busybox), '--kernel-apk', str(self.apk),
                        '--out', str(self.root/'bad.gz'), '--scripts', 'lua_testcode.sh'])

    def test_output_cannot_replace_an_input_or_another_output(self):
        before = self.busybox.read_bytes()
        for out, extra in [(self.busybox, ()), (self.root/'out.gz', ('--kernel-out', str(self.busybox))),
                           (self.root/'out.gz', ('--kernel-out', str(self.root/'out.gz')))]:
            with self.subTest(out=out, extra=extra), self.assertRaisesRegex(ValueError, 'overlap'):
                self.invoke(out, extra)
        self.assertEqual(self.busybox.read_bytes(), before)

    def test_release_and_scripts_are_validated(self):
        for args in [('--release', '../bad'), ('--scripts', 'missing.sh'),
                     ('--libcs', 'glibc', 'glibc')]:
            with self.subTest(args=args), self.assertRaises((ValueError, SystemExit)):
                self.invoke(self.root/'bad.gz', args)


class InitTests(unittest.TestCase):
    def test_every_preparation_failure_stops_before_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shim = root / 'busybox'
            shim.write_text('''#!/bin/sh
cmd="$1"; shift
case "$cmd" in
 echo) echo "$@"; exit 0 ;;
 poweroff) exit 0 ;;
esac
n=0
[ ! -f "$CALLS" ] || read -r n < "$CALLS"
n=$((n+1)); echo "$n" > "$CALLS"
echo "$cmd $*" >> "$TRACE"
[ "$n" != "$FAIL_AT" ] || exit 7
exit 0
''')
            shim.chmod(0o755)
            init = root / 'init'
            init.write_text(build.build_init(['glibc'], ['basic_testcode.sh'], build.MODULES + ['dummy'],
                                            RELEASE, True).replace('/busybox', str(shim)))
            env = dict(os.environ, CALLS=str(root/'calls'), TRACE=str(root/'trace'), FAIL_AT='-1')
            result = subprocess.run(['/bin/sh', str(init)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('REF_ALL_DONE', result.stdout)
            commands = (root/'trace').read_text().splitlines()
            for index, command in enumerate(commands, 1):
                # 最后一次 chroot 是被测脚本；其非零退出由 REF RESULT 单独记录。
                if 'chroot ' in command and '_testcode.sh' in command:
                    continue
                with self.subTest(command=command):
                    (root/'calls').unlink(missing_ok=True)
                    env['FAIL_AT'] = str(index)
                    result = subprocess.run(['/bin/sh', str(init)], env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('REF_FAIL ', result.stdout)
                    self.assertNotIn('REF RUN ', result.stdout)
                    self.assertNotIn('REF_ALL_DONE', result.stdout)
