"""把参考输入、构建身份、实际构件和串口协议绑定，拒绝未冻结实验输入。"""
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import os

from inventory import REFERENCE_LOCK, INVENTORY, digest


def build_identity(manifest):
    payload = {key: value for key, value in manifest.items() if key not in ('build_id', 'outputs')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def strict_qemu_command(command, artifacts, image):
    """严格采集仅允许构建器的直接 QEMU 命令，路径绑定后以 argv 执行。"""
    if len(artifacts) != 2 or image is None:
        raise ValueError('strict capture requires kernel, initramfs and reference image')
    args = shlex.split(command)
    if len(args) != 22 or Path(args[0]).name != 'qemu-system-riscv64':
        raise ValueError('strict capture requires the direct builder QEMU command')
    kernel, initramfs = [str(Path(p).resolve()) for p in artifacts]
    disk = str(Path(image).resolve())
    if ',' in disk:
        raise ValueError('commas in reference disk paths are unsupported')
    expected = [args[0], '-machine', 'virt', '-m', '1G', '-smp', '1', '-nographic',
                '-no-reboot', '-snapshot', '-bios', 'default', '-kernel', kernel,
                '-initrd', initramfs, '-append', 'console=ttyS0 rdinit=/init',
                '-drive', f'file={disk},if=none,format=raw,id=x0', '-device', 'virtio-blk-device,drive=x0']
    normalized = list(args)
    normalized[13] = str(Path(args[13]).resolve())
    normalized[15] = str(Path(args[15]).resolve())
    prefix, suffix = 'file=', ',if=none,format=raw,id=x0'
    if not args[19].startswith(prefix) or not args[19].endswith(suffix):
        raise ValueError('strict capture disk format does not match builder command')
    disk_argument = args[19][len(prefix):-len(suffix)]
    if ',' in disk_argument:
        raise ValueError('ambiguous QEMU drive path')
    normalized[19] = f'file={Path(disk_argument).resolve()}{suffix}'
    if normalized != expected:
        raise ValueError('QEMU command paths/options do not match declared reference artifacts')
    return expected


def qemu_identity(executable):
    """固定 PATH 解析结果与真实文件；版本探测前后文件身份必须一致。"""
    invoked = shutil.which(executable)
    if invoked is None:
        raise ValueError('QEMU executable not found: '+executable)
    path = Path(invoked).resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('QEMU executable is not an executable file')
    initial = digest(path)
    try:
        result = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('QEMU version probe failed') from exc
    version = result.stdout.splitlines()[0] if result.stdout.splitlines() else ''
    if not version.startswith('QEMU emulator version ') or len(result.stdout) > 4096:
        raise ValueError('QEMU version output is invalid')
    if digest(path) != initial or Path(invoked).resolve() != path:
        raise ValueError('QEMU executable changed during version probe')
    return {'requested': executable, 'invoked_path': str(Path(invoked).absolute()),
            'path': str(path), 'sha256': initial, 'version': version}


def verify_manifest(path, artifacts, text, inventory, selected, libc, inventory_path=INVENTORY, image=None):
    manifest = json.loads(Path(path).read_text())
    if not isinstance(manifest, dict):
        raise ValueError('reference manifest must be an object')
    if any(not isinstance(manifest.get(key), dict) for key in ('inputs', 'selection', 'outputs')):
        raise ValueError('invalid reference manifest fields')
    if manifest.get('schema_version') != 1 or manifest.get('protocol') != 1:
        raise ValueError('unsupported reference manifest')
    if manifest.get('build_id') != build_identity(manifest):
        raise ValueError('reference build identity mismatch')
    ids = [match[1] for line in text.splitlines()
           if (match := re.fullmatch(r'REF_BUILD_ID ([0-9a-f]{64})', line))]
    if ids != [manifest['build_id']]:
        raise ValueError('log does not match reference build identity')
    lock = json.loads(REFERENCE_LOCK.read_text())
    if (manifest.get('inputs_pinned') is not True or
            manifest.get('inputs', {}).get('image') != inventory['image']['uncompressed_sha256'] or
            manifest.get('inputs', {}).get('kernel_apk') != lock['kernel']['sha256'] or
            manifest.get('selection', {}).get('release') != lock['kernel']['release']):
        raise ValueError('reference inputs are not the frozen inputs')
    if manifest.get('inventory_sha256') != digest(inventory_path) or manifest.get('reference_lock_sha256') != digest(REFERENCE_LOCK):
        raise ValueError('reference input lock changed since build')
    if image is None or digest(Path(image)) != manifest['inputs']['image']:
        raise ValueError('actual reference disk hash does not match manifest')
    selection = manifest['selection']
    if (len(artifacts) != 2 or not isinstance(selection.get('libcs'), list) or not selection['libcs'] or
            any(c not in ('musl', 'glibc') for c in selection['libcs']) or
            len(set(selection['libcs'])) != len(selection['libcs']) or
            not isinstance(selection.get('scripts'), list) or not selection['scripts'] or
            any(s not in {f'{g}_testcode.sh' for g in inventory['groups']} for s in selection['scripts']) or
            len(set(selection['scripts'])) != len(selection['scripts'])):
        raise ValueError('invalid reference build selection/artifacts')
    if libc not in selection['libcs'] or any(f'{group}_testcode.sh' not in selection['scripts'] for group in selected):
        raise ValueError('reference manifest does not include selected groups/libc')
    runs = re.findall(r'^REF RUN (musl|glibc) ([a-z0-9_]+_testcode\.sh)\r?$', text, re.M)
    if runs != [(c, s) for c in selection['libcs'] for s in selection['scripts']]:
        raise ValueError('reference attempts do not match build selection/order')
    for artifact, key in zip(artifacts, ['kernel_sha256', 'initramfs_sha256']):
        if digest(Path(artifact)) != manifest['outputs'][key]:
            raise ValueError('reference artifact hash mismatch: ' + key)
    return {'manifest_sha256': digest(Path(path)),
            'build_id': manifest['build_id'], 'inputs_pinned': True, 'image_sha256': manifest['inputs']['image']}
