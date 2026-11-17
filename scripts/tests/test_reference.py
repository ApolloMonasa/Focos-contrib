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
exec(compile((ROOT/'scripts/ref/build_initramfs.py').read_bytes(),str(ROOT/'scripts/ref/build_initramfs.py'),'exec'),build.__dict__)
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

    def write_apk(self, modules, release=RELEASE, *, kernel=b'kernel', module_suffix=b''):
        with tarfile.open(self.apk, 'w:gz') as archive:
            data = ''.join(f'kernel/{m}.ko.gz:\n' for m in modules).encode()
            item = tarfile.TarInfo(f'lib/modules/{release}/modules.dep');item.size=len(data)
            archive.addfile(item, io.BytesIO(data))
            for name, raw in [('boot/vmlinuz-lts', kernel)] + [
                (f'lib/modules/{release}/kernel/{m}.ko.gz', b'module:' + m.encode()+module_suffix) for m in modules
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

    def test_build_identity_changes_with_actual_apk_kernel_or_module_content(self):
        baseline = self.root/'base/init.gz'
        self.invoke(baseline)
        old = json.loads(baseline.with_suffix('.manifest.json').read_text())['build_id']
        for target in ['kernel', 'module']:
            self.write_apk(build.MODULES+['dummy'],kernel=b'changed kernel' if target=='kernel' else b'kernel',
                           module_suffix=b':changed' if target=='module' else b'')
            out = self.root/target/'init.gz'
            with self.subTest(target=target):
                self.invoke(out)
                changed = json.loads(out.with_suffix('.manifest.json').read_text())
                self.assertNotEqual(old, changed['build_id'])

    def test_foreign_shared_outputs_cannot_be_adopted_as_this_apk_payload(self):
        original=build.build_init
        for target in ['kernel','module']:
            with self.subTest(target=target):
                out=self.root/target/'init.gz';polluted=False
                def generate(*args,**kwargs):
                    nonlocal polluted
                    if not polluted:
                        path=out.parent/'Image' if target=='kernel' else out.parent/'modules/virtio.ko'
                        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'from another build')
                        polluted=True
                    return original(*args,**kwargs)
                with patch.object(build,'build_init',side_effect=generate),self.assertRaises(ValueError):self.invoke(out)
                self.assertFalse(out.with_suffix('.manifest.json').exists())

    def test_rewrite_and_restore_input_during_extraction_is_not_accepted(self):
        original=build.extract_busybox;before=self.busybox.read_bytes();metadata=self.busybox.stat()
        def extract(*args):
            data=original(*args)
            self.busybox.write_bytes(b'changed');self.busybox.write_bytes(before)
            os.utime(self.busybox,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
            return data
        with patch.object(build,'extract_busybox',side_effect=extract),self.assertRaisesRegex(ValueError,'changed'):
            self.invoke(self.root/'restored/init.gz')

    def test_busybox_descriptor_must_match_the_frozen_input_even_if_alias_is_restored(self):
        alias=self.root/'busybox-alias';alias.symlink_to(self.busybox)
        other=self.root/'foreign';other.write_bytes(b'foreign busybox')
        real_open=Path.open;original=build.extract_busybox;reading=False
        def extract(*args):
            nonlocal reading
            reading=True
            try:return original(*args)
            finally:reading=False
        def open_path(path,*args,**kwargs):
            mode=args[0] if args else kwargs.get('mode','r')
            if reading and path==alias and mode=='rb':
                alias.unlink();alias.symlink_to(other)
                handle=real_open(path,*args,**kwargs)
                alias.unlink();alias.symlink_to(self.busybox)
                return handle
            return real_open(path,*args,**kwargs)
        with patch.object(build,'extract_busybox',side_effect=extract),patch.object(Path,'open',new=open_path),self.assertRaisesRegex(ValueError,'changed'):
            self.invoke(self.root/'descriptor/init.gz',('--busybox',str(alias)))

    def test_existing_different_build_is_preserved_and_identical_build_is_reused(self):
        out=self.root/'immutable/init.gz';self.invoke(out)
        files=[p for p in out.parent.rglob('*') if p.is_file()]
        before={p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in files}
        self.invoke(out)
        self.assertEqual({p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in files},before)
        self.write_apk(build.MODULES+['dummy'],kernel=b'other kernel',module_suffix=b':other')
        with self.assertRaises(ValueError):self.invoke(out)
        self.assertEqual({p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in files},before)

    def test_publication_create_race_never_overwrites_a_winner(self):
        import concurrent.futures,threading
        real_link=os.link
        for identical in [True,False]:
            with self.subTest(identical=identical),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);target=root/'artifact';barrier=threading.Barrier(2)
                def link(source,destination):
                    barrier.wait(timeout=5)
                    return real_link(source,destination)
                data=[b'first',b'first' if identical else b'other']
                def publish(payload):
                    try:build.publish_immutable(target,payload);return True
                    except ValueError:return False
                with patch.object(build.os,'link',side_effect=link),concurrent.futures.ThreadPoolExecutor(2) as pool:
                    futures=[pool.submit(publish,payload) for payload in data]
                    results=[future.result(timeout=10) for future in futures]
                self.assertEqual(sum(results),2 if identical else 1)
                for succeeded,payload in zip(results,data):
                    if succeeded:self.assertEqual(target.read_bytes(),payload)
                self.assertEqual(list(root.glob('.reference-*')),[])

    def test_output_parent_symlink_is_rejected_without_outside_writes(self):
        external=self.root/'external';external.mkdir();sentinel=external/'keep';sentinel.write_bytes(b'keep')
        alias=self.root/'alias';alias.symlink_to(external,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):self.invoke(alias/'init.gz')
        self.assertEqual(list(external.iterdir()),[sentinel])

    def test_parallel_pinned_and_experimental_builds_cannot_mix_shared_outputs(self):
        import itertools,select,shutil
        source_code='''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ref import build_initramfs as b
apk,out,shared,image,inventory,lock=map(Path,sys.argv[2:8])
b.INVENTORY=inventory;b.REFERENCE_LOCK=lock
b.extract_busybox=lambda *args:b'synthetic busybox'
original=b.build_init
paused=False
def generate(*args,**kwargs):
    global paused
    if len(sys.argv)>8 and not paused:
        paused=True;os.write(int(sys.argv[8]),b'R');os.read(int(sys.argv[9]),1)
    return original(*args,**kwargs)
b.build_init=generate
b.main(['--image',str(image),'--kernel-apk',str(apk),'--out',str(out),'--kernel-out',str(shared/'Image'),
        '--modules-dir',str(shared/'modules'),'--scripts','lua_testcode.sh','--allow-unpinned-inputs'])
'''
        for paused_role,differing in itertools.product(['pinned','experiment'],['kernel','module']):
            with self.subTest(paused=paused_role,differing=differing),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);shared=root/'shared';image=root/'disk';image.write_bytes(b'image')
                apks={}
                for role in ['pinned','experiment']:
                    kernel=b'shared kernel' if differing=='module' else ('kernel:'+role).encode()
                    self.write_apk(build.MODULES+['dummy'],kernel=kernel,module_suffix=(':'+role).encode())
                    apks[role]=root/(role+'.apk');shutil.copyfile(self.apk,apks[role])
                inv=json.loads((ROOT/'tests/inventory.json').read_text());inv['image']['uncompressed_sha256']=hashlib.sha256(b'image').hexdigest()
                inventory=root/'inventory.json';inventory.write_text(json.dumps(inv))
                lock_data=json.loads((ROOT/'tests/reference.lock.json').read_text());lock_data['kernel']['sha256']=hashlib.sha256(apks['pinned'].read_bytes()).hexdigest()
                lock=root/'lock.json';lock.write_text(json.dumps(lock_data))
                outs={role:root/role/'init.gz' for role in apks}
                def command(role):return [sys.executable,'-B','-X','pycache_prefix='+str(root/'cache'/role),'-c',source_code,str(ROOT/'scripts'),str(apks[role]),str(outs[role]),str(shared),str(image),str(inventory),str(lock)]
                ready_read,ready_write=os.pipe();resume_read,resume_write=os.pipe()
                process=subprocess.Popen(command(paused_role)+[str(ready_write),str(resume_read)],pass_fds=(ready_write,resume_read),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                os.close(ready_write);os.close(resume_read)
                try:
                    self.assertTrue(select.select([ready_read],[],[],10)[0],'build did not reach controlled interleave')
                    self.assertEqual(os.read(ready_read,1),b'R')
                    winner='experiment' if paused_role=='pinned' else 'pinned'
                    result=subprocess.run(command(winner),capture_output=True,timeout=20)
                    os.write(resume_write,b'R')
                    stdout,stderr=process.communicate(timeout=20)
                    self.assertEqual(result.returncode,0,result.stderr.decode())
                    self.assertNotEqual(process.returncode,0,stdout.decode()+stderr.decode())
                    self.assertFalse(outs[paused_role].with_suffix('.manifest.json').exists())
                    manifest=json.loads(outs[winner].with_suffix('.manifest.json').read_text())
                    self.assertIs(manifest['inputs_pinned'],winner=='pinned')
                    self.assertEqual((shared/'Image').read_bytes(),b'shared kernel' if differing=='module' else ('kernel:'+winner).encode())
                    archive=gzip.decompress(outs[winner].read_bytes())
                    for module in build.MODULES:self.assertIn(('module:'+module+':'+winner).encode(),archive)
                finally:
                    os.close(ready_read);os.close(resume_write)
                    if process.poll() is None:process.kill();process.communicate()

    def test_relative_build_inputs_are_not_rehashed_twice_for_the_same_boundary(self):
        reads=[];original=build.digest
        def digest(path):
            if path.is_file() and path.samefile(self.busybox):reads.append(str(path))
            return original(path)
        out=self.root/'result/init.gz'
        with patch.object(build,'digest',side_effect=digest),patch.object(build,'extract_busybox',return_value=b'busybox'),contextlib.redirect_stdout(io.StringIO()):
            build.main(['--image',os.path.relpath(self.busybox),'--kernel-apk',str(self.apk),
                        '--out',str(out),'--kernel-out',str(out.parent/'Image'),'--modules-dir',str(out.parent/'modules'),
                        '--scripts','lua_testcode.sh','--allow-unpinned-inputs'])
        self.assertEqual(len(reads),3,reads)  # 初始固定 + 两次发布边界，各一次。

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
