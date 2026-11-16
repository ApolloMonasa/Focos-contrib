"""把参考输入、构建身份、实际构件和串口协议绑定，拒绝未冻结实验输入。"""
import hashlib
import json
from pathlib import Path
import re

from inventory import REFERENCE_LOCK, INVENTORY, digest


def build_identity(manifest):
    payload = {key: value for key, value in manifest.items() if key not in ('build_id', 'outputs')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_manifest(path, artifacts, text, inventory, selected, libc, inventory_path=INVENTORY):
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
            'build_id': manifest['build_id'], 'inputs_pinned': True}
