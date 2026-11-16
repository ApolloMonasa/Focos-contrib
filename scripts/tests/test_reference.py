"""实际执行生成的 shell（仅替换机器操作），验证失败边界和确定性归档。"""
import contextlib
import gzip
import hashlib
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
            data = ''.join(f'kernel/{m}.ko.gz:\n' for m in modules).encode()
            item = tarfile.TarInfo(f'lib/modules/{release}/modules.dep');item.size=len(data)
            archive.addfile(item, io.BytesIO(data))
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

    def test_module_dependency_sort_rejects_missing_and_cycles(self):
        dep = 'a.ko.gz: b.ko.gz\nb.ko.gz:\n'
        self.assertEqual(build.module_order(['a','b'],dep),['b','a'])
        with self.assertRaises(ValueError): build.module_order(['a'],dep)
        with self.assertRaises(ValueError): build.module_order(['a','b'],'a.ko.gz: b.ko.gz\nb.ko.gz: a.ko.gz\n')

    def test_implementation_change_during_extraction_rejects_publication(self):
        original=build.extract_busybox
        for field in ['builder_sha256','inventory_code_sha256','manifest_code_sha256']:
            with self.subTest(field=field):
                sources={key:self.root/(key+'.py') for key in ['builder_sha256','inventory_code_sha256','manifest_code_sha256']}
                for path in sources.values():path.write_bytes(b'original source')
                def extract(*args):
                    sources[field].write_bytes(b'edited during image extraction')
                    return original(*args)
                out=self.root/(field+'.gz')
                with patch.object(build,'IMPLEMENTATION_FILES',sources),patch.object(build,'extract_busybox',side_effect=extract),self.assertRaisesRegex(ValueError,'changed'):
                    self.invoke(out)
                self.assertFalse(out.with_suffix('.manifest.json').exists())

    def test_output_cannot_overwrite_implementation_or_its_hardlink(self):
        source=self.root/'builder.py';source.write_bytes(b'implementation')
        alias=self.root/'alias.py';os.link(source,alias)
        for out in [source,alias]:
            with self.subTest(out=out),patch.object(build,'IMPLEMENTATION_FILES',{'builder_sha256':source}),self.assertRaisesRegex(ValueError,'overlap'):
                self.invoke(out)
        self.assertEqual(source.read_bytes(),b'implementation')

    def test_build_identity_changes_with_extracted_kernel_or_module_content(self):
        baseline = self.root/'base/init.gz'
        self.invoke(baseline)
        old = json.loads(baseline.with_suffix('.manifest.json').read_text())['build_id']
        original = build.extract_kernel_and_modules
        for target in ['kernel', 'module']:
            def extract(apk, release, kernel, modules, extra):
                order = original(apk, release, kernel, modules, extra)
                path = kernel if target == 'kernel' else modules/(order[0]+'.ko')
                path.write_bytes(path.read_bytes()+b'changed payload')
                return order
            out = self.root/target/'init.gz'
            with self.subTest(target=target), patch.object(build, 'extract_kernel_and_modules', side_effect=extract):
                self.invoke(out)
                changed = json.loads(out.with_suffix('.manifest.json').read_text())
                self.assertNotEqual(old, changed['build_id'])

    def test_output_hardlinks_are_rejected_before_any_write(self):
        from itertools import combinations
        for left, right in combinations(['Image','init.gz','init.manifest.json','modules/virtio.ko','modules/ext4.ko'],2):
            with self.subTest(left=left,right=right), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                a,b=root/left,root/right
                a.parent.mkdir(parents=True,exist_ok=True);b.parent.mkdir(parents=True,exist_ok=True)
                a.write_bytes(b'preserve');os.link(a,b)
                with self.assertRaisesRegex(ValueError,'overlap'):
                    self.invoke(root/'init.gz')
                self.assertEqual(a.read_bytes(),b'preserve')
                self.assertEqual(b.read_bytes(),b'preserve')


class PayloadIdentityTests(unittest.TestCase):
    def test_only_embedded_init_id_is_normalized_and_all_other_content_is_bound(self):
        from reference_manifest import initramfs_payload_sha256
        identity='a'*64
        init=b'#!/busybox sh\n/busybox echo REF_BUILD_ID '+identity.encode()+b'\necho ready\n'
        # 同形字串可出现在其他文件；不能从其内容中删去或归零。
        files={'init':(0o100755,init),'lib/module.ko':(0o100644,init),'busybox':(0o100755,b'busybox')}
        raw=build.archive_bytes(files)
        normalized=dict(files);normalized['init']=(0o100755,init.replace(identity.encode(),b'0'*64))
        expected=hashlib.sha256(build.archive_bytes(normalized)).hexdigest()
        self.assertEqual(initramfs_payload_sha256(raw,identity),expected)
        for name in files:
            changed=dict(files);mode,data=files[name];changed[name]=(mode,data+b'# changed\n')
            with self.subTest(name=name):
                self.assertNotEqual(initramfs_payload_sha256(build.archive_bytes(changed),identity),expected)

    def test_ambiguous_truncated_or_overlaid_cpio_cannot_claim_payload_identity(self):
        from reference_manifest import initramfs_payload_sha256
        identity='a'*64
        init=b'#!/busybox sh\n/busybox echo REF_BUILD_ID '+identity.encode()+b'\n'
        record=build.entry('init',0o100755,init,1)
        trailer=build.entry('TRAILER!!!',0,b'',0)
        raw=record+trailer
        hardlink=raw[:38]+b'00000002'+raw[46:]
        invalid=[b'',raw[:109],raw[:-1],raw+raw,record+record+trailer,hardlink,
                 raw[:54]+b'ffffffff'+raw[62:],raw[:94]+b'ffffffff'+raw[102:],
                 raw[:115]+b'x'+raw[116:],
                 build.entry('init',0o120777,init,1)+trailer,
                 build.entry('init',0o100755,init+init,1)+trailer,
                 build.entry('init',0o100755,b'no ID',1)+trailer]
        invalid += [build.entry(alias,0o100755,init,1)+trailer for alias in ['./init','/init','a/../init']]
        for blob in invalid:
            with self.subTest(blob=blob[:40]),self.assertRaises(ValueError):
                initramfs_payload_sha256(blob,identity)
        with self.assertRaisesRegex(ValueError,'identity'):
            initramfs_payload_sha256(raw,'b'*64)


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
printf '%s\\n' "$cmd $*" >> "$TRACE"
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
            self.assertEqual(len(commands), int((root/'calls').read_text()))
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
