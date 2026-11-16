"""把参考输入、构建身份、实际构件和串口协议绑定，拒绝未冻结实验输入。"""
import hashlib
import gzip
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import os
import zlib

from inventory import NAME, REFERENCE_LOCK, INVENTORY, digest


def load_manifest(path, *, raw=None):
    """采集和回放共用结构校验；读取字段前拒绝语法合法但类型错误的 JSON。"""
    def invalid_constant(value):
        raise ValueError('non-finite reference manifest value: '+value)
    manifest = json.loads(Path(path).read_bytes() if raw is None else raw, parse_constant=invalid_constant)
    if not isinstance(manifest,dict) or any(not isinstance(manifest.get(key),dict) for key in ('inputs','selection','outputs','content')):
        raise ValueError('reference manifest must contain object fields: inputs, selection, outputs, content')
    if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 2 or
            type(manifest.get('protocol')) is not int or manifest['protocol'] != 1 or
            type(manifest.get('inputs_pinned')) is not bool):
        raise ValueError('unsupported reference manifest schema/protocol/pinning')
    sha_fields=[(manifest,key) for key in ('build_id','inventory_sha256','reference_lock_sha256')]
    sha_fields += [(manifest[section],key) for section,keys in [
        ('inputs',('image','kernel_apk')),('outputs',('kernel_sha256','initramfs_sha256')),
        ('content',('kernel_sha256','initramfs_payload_sha256'))] for key in keys]
    sha_fields += [(manifest,key) for key in ('builder_sha256','inventory_code_sha256','manifest_code_sha256','busybox_sha256') if key in manifest]
    if any(not isinstance(obj.get(key),str) or not re.fullmatch(r'[0-9a-f]{64}',obj[key]) for obj,key in sha_fields):
        raise ValueError('invalid reference manifest SHA256 field')
    selection=manifest['selection']
    for key in ('libcs','scripts'):
        values=selection.get(key)
        if not isinstance(values,list) or not values or any(not isinstance(v,str) for v in values) or len(set(values)) != len(values):
            raise ValueError('invalid reference selection: '+key)
    if (any(v not in ('musl','glibc') for v in selection['libcs']) or
            any(not NAME.fullmatch(v) or not v.endswith('_testcode.sh') for v in selection['scripts']) or
            not isinstance(selection.get('release'),str) or not NAME.fullmatch(selection['release']) or
            ('network_dummy' in selection and type(selection['network_dummy']) is not bool)):
        raise ValueError('invalid reference selection values')
    if manifest['build_id'] != build_identity(manifest):
        raise ValueError('reference build identity mismatch')
    return manifest


def build_identity(manifest):
    payload = {key: value for key, value in manifest.items() if key not in ('build_id', 'outputs')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def initramfs_payload_sha256(raw, build_id):
    """只归零 newc 中 /init 的嵌入 ID，其余字节（含元数据）全部绑定。

    仅接受构建器的单一 newc 归档；拒绝重名、路径别名、硬链接、尾随归档和截断。
    不能对整个二进制做字符串替换：模块或 busybox 中可能也包含同形文本。
    """
    offset, names, identity_span = 0, set(), None
    while True:
        header = raw[offset:offset+110]
        if not re.fullmatch(rb'070701[0-9a-fA-F]{104}', header):
            raise ValueError('invalid/truncated initramfs newc header')
        fields = [int(header[i:i+8],16) for i in range(6,110,8)]
        _, mode, _, _, nlink, _, size, _, _, _, _, name_size, checksum = fields
        name_start = offset+110
        name_end = name_start+name_size
        data_start = (name_end+3) & ~3
        data_end = data_start+size
        following = (data_end+3) & ~3
        if (name_size < 1 or following > len(raw) or raw[name_end-1:name_end] != b'\0' or
                b'\0' in raw[name_start:name_end-1] or checksum or nlink != 1 or
                any(raw[name_end:data_start]) or any(raw[data_end:following])):
            raise ValueError('invalid/truncated initramfs newc entry')
        name = raw[name_start:name_end-1]
        if name == b'TRAILER!!!':
            if size or following != len(raw) or identity_span is None:
                raise ValueError('invalid initramfs trailer, tail or missing init')
            break
        if (not re.fullmatch(rb'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name) or
                any(part in (b'.',b'..') for part in name.split(b'/')) or name in names or
                mode & 0o170000 not in (0o100000,0o040000) or
                (mode & 0o170000 == 0o040000 and size)):
            raise ValueError('unsupported/duplicate initramfs path or entry')
        names.add(name)
        if name == b'init':
            if mode != 0o100755:
                raise ValueError('initramfs init must be an executable regular file')
            matches = list(re.finditer(rb'^/busybox echo REF_BUILD_ID ([0-9a-f]{64})$',raw[data_start:data_end],re.M))
            if len(matches) != 1 or matches[0][1] != build_id.encode('ascii'):
                raise ValueError('initramfs embedded build identity mismatch')
            identity_span = (data_start+matches[0].start(1),data_start+matches[0].end(1))
        offset = following
    start, end = identity_span
    return hashlib.sha256(raw[:start]+b'0'*64+raw[end:]).hexdigest()


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
    manifest = load_manifest(path)
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
    content = manifest['content']
    if content.get('kernel_sha256') != manifest['outputs']['kernel_sha256']:
        raise ValueError('reference kernel content identity mismatch')
    try:
        payload = initramfs_payload_sha256(gzip.decompress(Path(artifacts[1]).read_bytes()),manifest['build_id'])
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError('invalid compressed reference initramfs') from exc
    if payload != content.get('initramfs_payload_sha256'):
        raise ValueError('reference initramfs payload identity mismatch')
    return {'manifest_sha256': digest(Path(path)),
            'build_id': manifest['build_id'], 'inputs_pinned': True, 'image_sha256': manifest['inputs']['image']}
