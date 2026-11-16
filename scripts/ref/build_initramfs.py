#!/usr/bin/env python3
"""构建 TEST-001 参考 Linux 环境；输入校验、确定性归档与环境准备属于同一契约。"""
from __future__ import annotations

import argparse
import gzip
import io
import json
from pathlib import Path
import shlex
import sys
import tarfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory import NAME, REFERENCE_LOCK, digest, load_inventory
from reference_manifest import build_identity

MODULES = ['failover', 'net_failover', 'virtio_ring', 'virtio', 'virtio_net',
           'virtio_mmio', 'virtio_blk', 'crc16', 'mbcache', 'jbd2', 'ext4']


def extract_busybox(image: Path, explicit: Path | None) -> bytes:
    if explicit:
        return explicit.read_bytes()
    try:
        from ext4 import Volume
    except ImportError as exc:
        raise ValueError('install ext4 or pass --busybox') from exc
    with image.open('rb') as source:
        handle = Volume(source).inode_at('/musl/busybox').open()
        try:
            return handle.read()
        finally:
            handle.close()


def extract_kernel_and_modules(apk: Path, release: str, kernel_out: Path,
                               modules_dir: Path, extra: tuple[str, ...] = ()) -> list[str]:
    targets = MODULES + list(extra)
    payloads = {}
    with tarfile.open(apk, 'r:gz') as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        def read_one(candidates, description):
            if len(candidates) != 1:
                raise ValueError(f'expected one {description}, found {len(candidates)}')
            with archive.extractfile(candidates[0]) as handle:
                return gzip.decompress(handle.read())
        kernel = read_one([m for m in members if m.name.lstrip('./') == 'boot/vmlinuz-lts'], 'kernel')
        for module in targets:
            payloads[module] = read_one([
                m for m in members if m.name.lstrip('./').startswith(f'lib/modules/{release}/')
                and m.name.endswith(f'/{module}.ko.gz')
            ], f'{release}/{module}.ko.gz')
    kernel_out.parent.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)
    kernel_out.write_bytes(kernel)
    for module, data in payloads.items():
        (modules_dir / f'{module}.ko').write_bytes(data)
    return targets


def entry(name: str, mode: int, data: bytes, ino: int) -> bytes:
    encoded = name.encode() + b'\0'
    header = b'070701' + b''.join(f'{v:08x}'.encode() for v in
        (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0))
    out = header + encoded
    return out + b'\0' * (-len(out) % 4) + data + b'\0' * (-len(data) % 4)


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
    inventory = load_inventory()
    lock = json.loads(REFERENCE_LOCK.read_text())
    args.release = args.release or lock['kernel']['release']
    scripts = {f'{g}_testcode.sh' for g in inventory['groups']}
    if (not NAME.fullmatch(args.release) or
            any(v not in scripts for v in args.scripts) or
            any(v not in ('musl', 'glibc') for v in args.libcs) or
            len(set(args.libcs)) != len(args.libcs) or len(set(args.scripts)) != len(args.scripts)):
        raise ValueError('invalid release, scripts or duplicate selections')
    inputs_paths = {p.resolve() for p in (args.image, args.kernel_apk, args.busybox) if p is not None}
    outputs_paths = [args.out, args.out.with_suffix('.manifest.json'), args.kernel_out] + [
        args.modules_dir/f'{m}.ko' for m in MODULES + (['dummy'] if args.network_dummy or 'netperf_testcode.sh' in args.scripts else [])]
    resolved_outputs = [p.resolve() for p in outputs_paths]
    if inputs_paths.intersection(resolved_outputs) or len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError('input/output paths overlap')
    args.network_dummy = args.network_dummy or 'netperf_testcode.sh' in args.scripts
    inputs = {'image': digest(args.image), 'kernel_apk': digest(args.kernel_apk)}
    pinned = (inputs['image'] == inventory['image']['uncompressed_sha256'] and
              inputs['kernel_apk'] == lock['kernel']['sha256'] and
              args.release == lock['kernel']['release'] and args.busybox is None)
    if not pinned and not args.allow_unpinned_inputs:
        raise ValueError('input hashes/release do not match frozen reference; explicit inputs are experimental')
    busybox = extract_busybox(args.image, args.busybox)
    if not busybox:
        raise ValueError('empty busybox')
    modules = extract_kernel_and_modules(args.kernel_apk, args.release, args.kernel_out,
                                        args.modules_dir, ('dummy',) if args.network_dummy else ())
    import hashlib
    manifest = {'schema_version': 1, 'protocol': 1, 'inputs_pinned': pinned, 'inputs': inputs,
                'busybox_sha256': hashlib.sha256(busybox).hexdigest(),
                'builder_sha256': digest(Path(__file__)),
                'inventory_code_sha256': digest(Path(__file__).resolve().parents[1]/'inventory.py'),
                'manifest_code_sha256': digest(Path(__file__).resolve().parents[1]/'reference_manifest.py'),
                'inventory_sha256': digest(Path(__file__).resolve().parents[2]/'tests/inventory.json'),
                'reference_lock_sha256': digest(REFERENCE_LOCK),
                'zlib_version': zlib.ZLIB_RUNTIME_VERSION,
                'selection': {'libcs': args.libcs, 'scripts': args.scripts, 'release': args.release,
                              'network_dummy': args.network_dummy}}
    manifest['build_id'] = build_identity(manifest)
    init = build_init(args.libcs, args.scripts, modules, args.release, args.network_dummy, manifest['build_id'])
    files = {'init': (0o100755, init.encode()), 'busybox': (0o100755, busybox)}
    for module in modules:
        files[f'lib/modules/{args.release}/{module}.ko'] = (0o100644, (args.modules_dir/f'{module}.ko').read_bytes())
    dirs = {str(parent) for name in files for parent in Path(name).parents if str(parent) != '.'}
    buffer = io.BytesIO()
    for ino, directory in enumerate(sorted(dirs), 1):
        buffer.write(entry(directory, 0o040755, b'', ino))
    for ino, (name, (mode, data)) in enumerate(sorted(files.items()), len(dirs)+1):
        buffer.write(entry(name, mode, data, ino))
    buffer.write(entry('TRAILER!!!', 0, b'', 0))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0, compresslevel=9) as handle:
            handle.write(buffer.getvalue())
    manifest['outputs'] = {'kernel_sha256': digest(args.kernel_out), 'initramfs_sha256': digest(args.out)}
    args.out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
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
