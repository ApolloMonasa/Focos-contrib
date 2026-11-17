#!/usr/bin/env python3
"""构建 TEST-001 参考 Linux 环境；输入校验、确定性归档与环境准备属于同一契约。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
from itertools import combinations
import json
import os
from pathlib import Path
import shlex
import sys
import tarfile
import tempfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory import NAME, REFERENCE_LOCK, INVENTORY, digest, load_inventory
from reference_manifest import build_identity, initramfs_payload_sha256

MODULES = ['failover', 'net_failover', 'virtio_ring', 'virtio', 'virtio_net',
           'virtio_mmio', 'virtio_blk', 'crc16', 'mbcache', 'jbd2', 'ext4']


IMPLEMENTATION_FILES = {
    'builder_sha256': Path(__file__).resolve(),
    'inventory_code_sha256': Path(__file__).resolve().parents[1]/'inventory.py',
    'manifest_code_sha256': Path(__file__).resolve().parents[1]/'reference_manifest.py',
}


def file_attributes(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_mode)


def file_signature(path):
    return path.resolve(),file_attributes(path.stat())


def extract_busybox(image: Path, explicit: Path | None, expected=None) -> bytes:
    def unchanged_descriptor(source):
        if expected is not None and file_attributes(os.fstat(source.fileno())) != expected[1]:
            raise ValueError('busybox input changed during extraction')
    if explicit:
        with explicit.open('rb') as source:
            unchanged_descriptor(source)
            data=source.read()
            unchanged_descriptor(source)
            return data
    try:
        from ext4 import Volume
    except ImportError as exc:
        raise ValueError('install ext4 or pass --busybox') from exc
    with image.open('rb') as source:
        unchanged_descriptor(source)
        handle = Volume(source).inode_at('/musl/busybox').open()
        try:
            data=handle.read()
            unchanged_descriptor(source)
            return data
        finally:
            handle.close()


def module_order(targets: list[str], dependency_table: str) -> list[str]:
    """只从 APK 的 modules.dep 推导顺序；缺失依赖/重复声明/环均拒绝构建。"""
    dependencies = {}
    def name(path):
        return Path(path).name.split('.ko', 1)[0]
    for line in dependency_table.splitlines():
        if not line.strip():
            continue
        module, separator, deps = line.partition(':')
        if not separator:
            raise ValueError('malformed modules.dep')
        key = name(module)
        if key in targets:
            if key in dependencies:
                raise ValueError('duplicate module dependency entry: '+key)
            dependencies[key] = [name(dep) for dep in deps.split()]
    order, active, done = [], set(), set()
    def visit(module):
        if module in done: return
        if module in active: raise ValueError('module dependency cycle: '+module)
        if module not in targets or module not in dependencies:
            raise ValueError('required module/dependency missing: '+module)
        active.add(module)
        for dependency in dependencies[module]: visit(dependency)
        active.remove(module)
        done.add(module)
        order.append(module)
    for module in targets: visit(module)
    return order


def read_kernel_payload(apk: Path, release: str, expected_sha256: str, extra: tuple[str, ...] = ()):
    """只从校验过的 APK 字节快照解包，返回不可变字节，不经过共享输出目录。"""
    targets = MODULES + list(extra)
    payloads = {}
    raw = apk.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('APK changed before extraction')
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        def read_one(candidates, description):
            if len(candidates) != 1:
                raise ValueError(f'expected one {description}, found {len(candidates)}')
            with archive.extractfile(candidates[0]) as handle:
                return gzip.decompress(handle.read())
        tables = [m for m in members if m.name.lstrip('./') == f'lib/modules/{release}/modules.dep']
        if len(tables) != 1:
            raise ValueError('missing/duplicate modules.dep for release')
        with archive.extractfile(tables[0]) as handle:
            targets = module_order(targets, handle.read().decode('utf-8'))
        kernel = read_one([m for m in members if m.name.lstrip('./') == 'boot/vmlinuz-lts'], 'kernel')
        for module in targets:
            payloads[module] = read_one([
                m for m in members if m.name.lstrip('./').startswith(f'lib/modules/{release}/')
                and m.name.endswith(f'/{module}.ko.gz')
            ], f'{release}/{module}.ko.gz')
    return kernel, tuple((module,payloads[module]) for module in targets)


def check_immutable_output(path, data):
    for component in (path.absolute(),*path.absolute().parents):
        if component.is_symlink():
            raise ValueError('build output contains a symlink: '+str(component))
    if not path.exists():
        return False
    if not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data:
        raise ValueError('existing build output differs; use new output paths: '+str(path))
    return True


def publish_immutable(path, data):
    """私有文件写完后以 link 原子创建；并发同名文件只能复用同样的字节。"""
    if check_immutable_output(path,data):
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.reference-',dir=path.parent) as stage:
        stage.write(data)
        stage.flush()
        os.fchmod(stage.fileno(),0o644)
        try:
            os.link(stage.name,path)
        except FileExistsError:
            if not check_immutable_output(path,data):
                raise ValueError('build output disappeared during publication: '+str(path))


def entry(name: str, mode: int, data: bytes, ino: int) -> bytes:
    encoded = name.encode() + b'\0'
    header = b'070701' + b''.join(f'{v:08x}'.encode() for v in
        (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0))
    out = header + encoded
    return out + b'\0' * (-len(out) % 4) + data + b'\0' * (-len(data) % 4)


def archive_bytes(files):
    dirs = {str(parent) for name in files for parent in Path(name).parents if str(parent) != '.'}
    records = [entry(directory,0o040755,b'',ino) for ino,directory in enumerate(sorted(dirs),1)]
    records += [entry(name,mode,data,ino) for ino,(name,(mode,data)) in enumerate(sorted(files.items()),len(dirs)+1)]
    return b''.join(records)+entry('TRAILER!!!',0,b'',0)


def build_init(libcs: list[str], scripts: list[str], modules: list[str], release: str, network: bool, build_id: str | None = None) -> str:
    # 生成命令一律经过 must；只有被测脚本退出码与环境错误分开记录。
    lines = ['''#!/busybox sh
fail() {
  /busybox echo "REF_FAIL $1"
  /busybox poweroff -f
  exit 1
}
must() {
  stage="$1"
  shift
  "$@" || fail "$stage"
}
/busybox echo "REF_PROTOCOL 1"''']
    if build_id:
        lines.append('/busybox echo REF_BUILD_ID ' + build_id)
    def must(stage, *args):
        lines.append('must ' + shlex.join([stage, '/busybox', *args]))
    must('mkdir-root', 'mkdir', '-p', '/proc', '/sys', '/dev', '/mnt')
    for kind, dest in [('proc', '/proc'), ('sysfs', '/sys'), ('devtmpfs', '/dev')]:
        must(f'mount-{kind}', 'mount', '-t', kind, kind, dest)
    for module in modules:
        path = f'/lib/modules/{release}/{module}.ko'
        must(f'module-missing:{module}', 'test', '-f', path)
        must(f'module-load:{module}', 'insmod', path)
    lines.append('/busybox echo REF_MODULES_OK')
    must('mount-ext4', 'mount', '-t', 'ext4', '/dev/vda', '/mnt')
    lines.append('/busybox echo REF_MOUNT_OK')
    if network or 'iperf_testcode.sh' in scripts:
        must('network-loopback', 'ifconfig', 'lo', '127.0.0.1', 'up')
    if network:
        must('network-dummy', 'ifconfig', 'dummy0', '10.0.0.1', 'netmask', '255.255.255.0', 'up')
    # 先准备全部 libc；避免后一套环境失败时已有部分运行被误作完整基线。
    for libc in libcs:
        base = f'/mnt/{libc}'
        must(f'busybox:{libc}', 'test', '-x', base + '/busybox')
        must(f'mkdir:{libc}', 'mkdir', '-p', *[base + p for p in
             ['/proc', '/dev', '/tmp', '/var/tmp', '/bin', '/etc']])
        for path in ['/proc', '/dev']:
            must(f'bind-{path[1:]}:{libc}', 'mount', '--bind', path, base + path)
        for applet in ['sh', 'busybox', 'ls']:
            must(f'link-{applet}:{libc}', 'ln', '-sf', '/busybox', base + '/bin/' + applet)
        if 'basic_testcode.sh' in scripts:
            must(f'basic-file:{libc}', 'test', '-f', base + '/basic/run-all.sh')
            must(f'chmod-basic:{libc}', 'chmod', '+x', base + '/basic/run-all.sh')
        for file, value in [('hosts', '127.0.0.1 localhost'), ('nsswitch.conf', 'hosts: files dns')]:
            must(f'config-{file}:{libc}', 'sh', '-c', f"printf '%s\\n' '{value}' > {base}/etc/{file}")
        for script in scripts:
            must(f'script:{libc}:{script}', 'test', '-r', base + '/' + script)
        must(f'chroot-ready:{libc}', 'chroot', base, '/busybox', 'sh', '-c', ':')
        lines.append(f'/busybox echo REF_PREPARED {libc}')
    for libc in libcs:
        for script in scripts:
            lines.append(f'/busybox echo REF RUN {libc} {script}')
            # lmbench 部分结果没有标签；记录 shell trace 以绑定命令和指标，脚本内容不改。
            trace = ' -x' if script == 'lmbench_testcode.sh' else ''
            lines.append(f'/busybox chroot /mnt/{libc} /busybox sh{trace} /{script}')
            lines.append('result=$?')
            lines.append(f'/busybox echo REF RESULT {libc} {script} "$result"')
    lines += ['/busybox echo REF_ALL_DONE', '/busybox poweroff -f', '']
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--kernel-apk', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--kernel-out', type=Path, default=Path('Image'))
    parser.add_argument('--modules-dir', type=Path, default=Path('modules'))
    parser.add_argument('--busybox', type=Path)
    parser.add_argument('--release', help='默认取 reference.lock.json 的 release')
    parser.add_argument('--libcs', nargs='+', default=['glibc'])
    parser.add_argument('--scripts', nargs='+', required=True)
    parser.add_argument('--network-dummy', action='store_true')
    parser.add_argument('--allow-unpinned-inputs', action='store_true', help='显式实验输入；manifest 标为未冻结')
    args = parser.parse_args(argv)
    fingerprints={p:file_signature(p) for p in [*IMPLEMENTATION_FILES.values(),INVENTORY,REFERENCE_LOCK]}
    implementation_hashes = {key: digest(path) for key, path in IMPLEMENTATION_FILES.items()}
    inventory_bytes = INVENTORY.read_bytes()
    lock_bytes = REFERENCE_LOCK.read_bytes()
    inventory = load_inventory(raw=inventory_bytes)
    lock = json.loads(lock_bytes)
    inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
    lock_hash = hashlib.sha256(lock_bytes).hexdigest()
    frozen = {path: implementation_hashes[key] for key, path in IMPLEMENTATION_FILES.items()}
    frozen.update({INVENTORY: inventory_hash, REFERENCE_LOCK: lock_hash})
    def unchanged():
        checked = {}
        for path, initial in frozen.items():
            if not path.is_file() or file_signature(path) != fingerprints[path]:
                raise ValueError('build source/input changed: '+str(path))
            key = fingerprints[path][1]
            if key not in checked:
                checked[key] = digest(path)
                if file_signature(path) != fingerprints[path]:
                    raise ValueError('build source/input changed while hashing: '+str(path))
            if checked[key] != initial:
                raise ValueError('build source/input changed: '+str(path))
    args.release = args.release or lock['kernel']['release']
    scripts = {f'{g}_testcode.sh' for g in inventory['groups']}
    if (not NAME.fullmatch(args.release) or
            any(v not in scripts for v in args.scripts) or
            any(v not in ('musl', 'glibc') for v in args.libcs) or
            len(set(args.libcs)) != len(args.libcs) or len(set(args.scripts)) != len(args.scripts)):
        raise ValueError('invalid release, scripts or duplicate selections')
    protected_inputs = [args.image, args.kernel_apk, INVENTORY, REFERENCE_LOCK, *IMPLEMENTATION_FILES.values()] + ([args.busybox] if args.busybox else [])
    inputs_paths = {p.resolve() for p in protected_inputs}
    outputs_paths = [args.out, args.out.with_suffix('.manifest.json'), args.kernel_out] + [
        args.modules_dir/f'{m}.ko' for m in MODULES + (['dummy'] if args.network_dummy or 'netperf_testcode.sh' in args.scripts else [])]
    def check_outputs():
        resolved_outputs = [p.resolve() for p in outputs_paths]
        hardlink_overlap = any(out.exists() and source.exists() and out.samefile(source) for out in outputs_paths for source in protected_inputs)
        shared_outputs = any(a.exists() and b.exists() and a.samefile(b) for a,b in combinations(outputs_paths,2))
        if inputs_paths.intersection(resolved_outputs) or hardlink_overlap or shared_outputs or len(set(resolved_outputs)) != len(resolved_outputs):
            raise ValueError('input/output paths overlap')
    check_outputs()
    args.network_dummy = args.network_dummy or 'netperf_testcode.sh' in args.scripts
    fingerprints.update({p:file_signature(p) for p in [args.image,args.kernel_apk,*([args.busybox] if args.busybox else [])]})
    inputs = {'image': digest(args.image), 'kernel_apk': digest(args.kernel_apk)}
    frozen.update({args.image: inputs['image'], args.kernel_apk: inputs['kernel_apk']})
    if args.busybox:
        frozen[args.busybox] = digest(args.busybox)
    for path, initial in list(frozen.items()):
        canonical=fingerprints[path][0]
        frozen.setdefault(canonical,initial)
        fingerprints.setdefault(canonical,fingerprints[path])
    pinned = (inputs['image'] == inventory['image']['uncompressed_sha256'] and
              inputs['kernel_apk'] == lock['kernel']['sha256'] and
              args.release == lock['kernel']['release'] and args.busybox is None)
    if not pinned and not args.allow_unpinned_inputs:
        raise ValueError('input hashes/release do not match frozen reference; explicit inputs are experimental')
    busybox = bytes(extract_busybox(args.image,args.busybox,fingerprints[args.busybox or args.image]))
    if not busybox:
        raise ValueError('empty busybox')
    check_outputs()
    kernel, module_payloads = read_kernel_payload(args.kernel_apk,args.release,inputs['kernel_apk'],
                                                 ('dummy',) if args.network_dummy else ())
    modules = [module for module,data in module_payloads]
    manifest = {'schema_version': 2, 'protocol': 1, 'inputs_pinned': pinned, 'inputs': inputs,
                'busybox_sha256': hashlib.sha256(busybox).hexdigest(), 'module_order': modules,
                **implementation_hashes,
                'inventory_sha256': inventory_hash,
                'reference_lock_sha256': lock_hash,
                'zlib_version': zlib.ZLIB_RUNTIME_VERSION,
                'selection': {'libcs': args.libcs, 'scripts': args.scripts, 'release': args.release,
                              'network_dummy': args.network_dummy}}
    placeholder = '0'*64
    init = build_init(args.libcs, args.scripts, modules, args.release, args.network_dummy, placeholder)
    files = {'init': (0o100755, init.encode()), 'busybox': (0o100755, busybox)}
    for module,data in module_payloads:
        files[f'lib/modules/{args.release}/{module}.ko'] = (0o100644,data)
    manifest['content'] = {'kernel_sha256': hashlib.sha256(kernel).hexdigest(),
                           'initramfs_payload_sha256': initramfs_payload_sha256(archive_bytes(files),placeholder)}
    manifest['build_id'] = build_identity(manifest)
    files['init'] = (0o100755,build_init(args.libcs,args.scripts,modules,args.release,args.network_dummy,manifest['build_id']).encode())
    payload = archive_bytes(files)
    if initramfs_payload_sha256(payload,manifest['build_id']) != manifest['content']['initramfs_payload_sha256']:
        raise ValueError('initramfs payload changed while embedding identity')
    unchanged()
    check_outputs()
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed,filename='',mode='wb',mtime=0,compresslevel=9) as handle:
        handle.write(payload)
    initramfs = compressed.getvalue()
    manifest['outputs'] = {'kernel_sha256': manifest['content']['kernel_sha256'],
                           'initramfs_sha256': hashlib.sha256(initramfs).hexdigest()}
    publications = [(args.kernel_out,kernel),*[(args.modules_dir/f'{module}.ko',data) for module,data in module_payloads],
                    (args.out,initramfs)]
    manifest_path = args.out.with_suffix('.manifest.json')
    manifest_bytes = (json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    for path,data in [*publications,(manifest_path,manifest_bytes)]:
        check_immutable_output(path,data)
    for path,data in publications:
        publish_immutable(path,data)
    unchanged()
    check_outputs()
    for path,data in publications:
        if not check_immutable_output(path,data):
            raise ValueError('published build output disappeared: '+str(path))
    publish_immutable(manifest_path,manifest_bytes)
    command = ['qemu-system-riscv64', '-machine', 'virt', '-m', '1G', '-smp', '1', '-nographic',
               '-no-reboot', '-snapshot', '-bios', 'default', '-kernel', str(args.kernel_out),
               '-initrd', str(args.out), '-append', 'console=ttyS0 rdinit=/init',
               '-drive', f'file={args.image},if=none,format=raw,id=x0', '-device', 'virtio-blk-device,drive=x0']
    print(shlex.join(command))
    print(json.dumps(manifest['outputs']))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, tarfile.TarError, EOFError) as exc:
        raise SystemExit(f'build reference: {exc}')
