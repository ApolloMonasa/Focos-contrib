"""只读运行包：固定布局、完整采集身份、前后完整性校验和显式 shard 选择。"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .config import Profile
from .errors import InputError
from .fsutil import read_regular_bytes, sha256_bytes
from .strictjson import StrictJSONError, canonical_bytes, loads

CAPTURE_KEYS = {
    'schema_version', 'kind', 'run_id', 'profile', 'selection', 'subject',
    'platform', 'capture', 'input_integrity', 'serial', 'build', 'firmware',
    'campaign', 'qemu', 'argv', 'started_at', 'finished_at', 'limits',
    'cow_layer', 'read_only_inputs', 'capture_implementation',
}
CAPTURE_STATUS = {'EXITED', 'TIMEOUT', 'OUTPUT_LIMIT', 'START_ERROR', 'NOT_RECORDED'}
INTEGRITY = {'VERIFIED', 'CHANGED', 'UNVERIFIED'}
READ_ONLY_ROLES = {'kernel', 'initramfs', 'base_img', 'firmware', 'qemu'}
BUILD_KEYS = {'manifest_sha256', 'kernel_sha256', 'initramfs_sha256', 'base_img_sha256'}
QEMU_KEYS = {'path', 'sha256', 'version'}
FIRMWARE_KEYS = {'file', 'sha256', 'size', 'source', 'version', 'original_path'}
LIMIT_KEYS = {'timeout_seconds', 'serial_bytes', 'output_bytes'}
COW_KEYS = {'path', 'format', 'sha256', 'size', 'created', 'base_img_sha256'}
READ_KEYS = {'role', 'path', 'sha256_before', 'sha256_after'}
IMPLEMENTATION_KEYS = {'files'}
IMPLEMENTATION_FILE_KEYS = {'path', 'sha256'}


@dataclass
class RunPackage:
    path: Path
    capture: dict
    capture_sha256: str
    serial_bytes: bytes
    serial_sha256: str
    firmware_sha256: str
    qemu_sha256: str
    capture_implementation_sha256: str
    selection: list[str]


def _require_keys(value: dict, required: set, *, label: str):
    if not isinstance(value, dict):
        raise InputError(f'{label}: expected an object')
    missing = sorted(required - set(value))
    if missing:
        raise InputError(f'{label}: missing fields: {", ".join(missing)}')


def _check_unknown(value: dict, allowed: set, *, label: str):
    extra = sorted(set(value) - allowed)
    if extra:
        raise InputError(f'{label}: unknown fields: {", ".join(extra)}')


def _hex(value, *, label: str):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise InputError(f'{label}: expected a lowercase SHA256')
    return value


def _string(value, *, label: str, nonempty: bool = True):
    if not isinstance(value, str) or (nonempty and not value):
        raise InputError(f'{label}: expected a non-empty string')
    return value


def _safe_relative(value, *, label: str):
    _string(value, label=label)
    if value.startswith('/') or '\\' in value or '\x00' in value:
        raise InputError(f'{label}: unsafe relative path')
    parts = PurePosixPath(value).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        raise InputError(f'{label}: unsafe relative path')
    return value


def _timestamp(value, *, label: str) -> datetime:
    _string(value, label=label)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise InputError(f'{label}: invalid ISO-8601 timestamp') from exc
    if parsed.tzinfo is None:
        raise InputError(f'{label}: timestamp needs a timezone')
    return parsed


def _positive_int(value, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise InputError(f'{label}: expected a positive integer')
    return value


FLAGS_WITH_VALUE = {'-machine', '-m', '-smp', '-bios', '-kernel',
                     '-initrd', '-append', '-drive', '-device'}
FLAGS_NO_VALUE = {'-nographic', '-no-reboot'}
DRIVE_KEYS = {'file', 'format', 'if', 'id', 'readonly'}
APPEND = 'console=ttyS0 rdinit=/init'


def _parse_drive(value: str) -> dict:
    if not isinstance(value, str) or not value:
        raise InputError('capture -drive has no value')
    options = {}
    for part in value.split(','):
        key, separator, option = part.partition('=')
        if not separator or not key or key in options:
            raise InputError('capture -drive options are malformed or duplicated')
        options[key] = option
    if set(options) - DRIVE_KEYS:
        raise InputError('capture -drive has unregistered options')
    if not options.get('file'):
        raise InputError('capture -drive has no file')
    if options.get('if') != 'none':
        raise InputError('capture -drive must use if=none')
    if not options.get('id'):
        raise InputError('capture -drive has no id')
    if 'readonly' in options and options['readonly'] not in ('on', 'off'):
        raise InputError('capture -drive readonly is invalid')
    if 'format' not in options:
        raise InputError('capture -drive has no format')
    return options


def _parse_device(value: str) -> dict:
    parts = value.split(',')
    if not parts or parts[0] != 'virtio-blk-device':
        raise InputError('capture -device is not the frozen virtio block device')
    options = {}
    for part in parts[1:]:
        key, separator, option = part.partition('=')
        if not separator or not key or key in options:
            raise InputError('capture -device options are malformed or duplicated')
        options[key] = option
    if set(options) != {'drive'} or not options['drive']:
        raise InputError('capture -device must only bind the COW drive')
    return options


def _parse_argv(argv):
    values = {}
    flags = set()
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in FLAGS_NO_VALUE:
            if flag in flags:
                raise InputError(f'capture argv repeats {flag}')
            flags.add(flag)
            index += 1
        elif flag in FLAGS_WITH_VALUE:
            if flag in values:
                raise InputError(f'capture argv repeats {flag}')
            if index + 1 >= len(argv) or not argv[index + 1]:
                raise InputError(f'capture argv has no value for {flag}')
            values[flag] = argv[index + 1]
            index += 2
        else:
            raise InputError(f'capture argv has unregistered argument: {flag}')
    return values, flags


def _check_argv(argv, paths: dict, qemu_path: str, cow: dict):
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(value, str) or not value or '\x00' in value
                   for value in argv)):
        raise InputError('capture argv is invalid')
    if argv[0] != qemu_path:
        raise InputError('capture argv does not name the exact recorded QEMU binary')
    values, _ = _parse_argv(argv)
    for flag in ('-machine', '-m', '-smp', '-bios', '-kernel', '-initrd',
                 '-append', '-drive', '-device'):
        if flag not in values:
            raise InputError(f'capture argv is missing {flag}')
    if values['-machine'] != 'virt':
        raise InputError('capture argv -machine is not virt')
    if not re.fullmatch(r'[1-9][0-9]*[MG]', values['-m']):
        raise InputError('capture argv -m is not a frozen memory size')
    if not re.fullmatch(r'[1-9][0-9]*', values['-smp']):
        raise InputError('capture argv -smp is invalid')
    if values['-append'] != APPEND:
        raise InputError('capture argv -append is not the frozen command line')
    if values['-bios'] != paths['firmware']:
        raise InputError('capture argv -bios is not the verified firmware')
    if values['-kernel'] != paths['kernel']:
        raise InputError('capture argv -kernel is not the verified kernel')
    if values['-initrd'] != paths['initramfs']:
        raise InputError('capture argv -initrd is not the verified initramfs')
    drive = _parse_drive(values['-drive'])
    if drive['file'] == paths['base_img']:
        raise InputError('capture argv attaches the base image as a writable drive')
    if drive['file'] != cow['path']:
        raise InputError('capture argv drive is not the exclusive COW layer')
    if drive['format'] != cow['format']:
        raise InputError('capture argv drive format does not match the COW layer')
    if drive.get('readonly') == 'on':
        raise InputError('capture argv COW layer is read-only')
    device = _parse_device(values['-device'])
    if device['drive'] != drive['id']:
        raise InputError('capture argv device does not attach the COW drive')


def _check_read_only_inputs(items, expected: dict[str, str], integrity: str):
    if not isinstance(items, list) or not items:
        raise InputError('capture read_only_inputs is invalid')
    roles = {}
    for index, item in enumerate(items):
        _require_keys(item, READ_KEYS, label=f'read_only_inputs[{index}]')
        _check_unknown(item, READ_KEYS, label=f'read_only_inputs[{index}]')
        role = _string(item['role'], label=f'read_only_inputs[{index}].role')
        if role in roles:
            raise InputError(f'capture read_only_inputs has duplicate role: {role}')
        _string(item['path'], label=f'read_only_inputs[{index}].path')
        before = _hex(item['sha256_before'], label=f'read_only_inputs[{index}].before')
        after = _hex(item['sha256_after'], label=f'read_only_inputs[{index}].after')
        roles[role] = (item['path'], before, after)
    if set(roles) != READ_ONLY_ROLES:
        missing = sorted(READ_ONLY_ROLES - set(roles))
        extra = sorted(set(roles) - READ_ONLY_ROLES)
        raise InputError(f'capture read_only_inputs roles mismatch; missing={missing}, extra={extra}')
    paths = {}
    for role, expected_hash in expected.items():
        path, before, after = roles[role]
        paths[role] = path
        if expected_hash not in (before, after):
            raise InputError(f'capture read_only_inputs {role} does not match frozen identity')
        if integrity == 'VERIFIED' and before != after:
            raise InputError(f'capture read_only_inputs {role} changed under VERIFIED')
    return paths


def load_run_package(path: Path, profile: Profile) -> RunPackage:
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_dir():
        raise InputError(f'run package is not a directory: {path}')
    entries = sorted(item.name for item in path.iterdir())
    allowed = {'capture.json', 'serial.log', 'firmware.bin'}
    if set(entries) != allowed:
        missing = sorted(allowed - set(entries))
        extra = sorted(set(entries) - allowed)
        raise InputError(f'run package layout mismatch; missing={missing}, extra={extra}')
    for name in entries:
        item = path / name
        if item.is_symlink() or not item.is_file():
            raise InputError(f'run package member is not a regular file: {name}')

    capture_raw = read_regular_bytes(path / 'capture.json', label='capture manifest',
                                     max_bytes=1024 * 1024)
    try:
        capture = loads(capture_raw, label='capture manifest')
    except StrictJSONError as exc:
        raise InputError(str(exc)) from exc
    _require_keys(capture, CAPTURE_KEYS, label='capture manifest')
    _check_unknown(capture, CAPTURE_KEYS, label='capture manifest')
    if capture['schema_version'] != 2 or capture['kind'] != 'test001_capture':
        raise InputError('unsupported capture schema')
    try:
        uuid.UUID(str(capture['run_id']))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InputError('capture run_id is not a UUID') from exc

    profile_spec = capture['profile']
    _require_keys(profile_spec, {'profile_id', 'revision', 'profile_hash'}, label='capture profile')
    _check_unknown(profile_spec, {'profile_id', 'revision', 'profile_hash'}, label='capture profile')
    if (profile_spec['profile_id'] != profile.profile_id
            or profile_spec['revision'] != profile.revision
            or profile_spec['profile_hash'] != profile.profile_hash):
        raise InputError('capture profile does not match the selected profile')
    expected_hashes = {shard.shard_id for shard in profile.shards}
    selection = capture['selection']
    if (not isinstance(selection, list) or not selection
            or any(not isinstance(item, str) for item in selection)
            or len(set(selection)) != len(selection)):
        raise InputError('capture selection is invalid')
    for shard_id in selection:
        if shard_id not in expected_hashes:
            raise InputError(f'capture selection names an unknown shard: {shard_id}')
    if capture['subject'] != 'reference_linux' or capture['platform'] != 'qemu_riscv64':
        raise InputError('capture subject/platform is not allowed in this criteria')
    if capture['input_integrity'] not in INTEGRITY:
        raise InputError('capture input_integrity is invalid')

    capture_state = capture['capture']
    _require_keys(capture_state, {'status', 'exit_code'}, label='capture state')
    _check_unknown(capture_state, {'status', 'exit_code'}, label='capture state')
    if capture_state['status'] not in CAPTURE_STATUS:
        raise InputError('capture status is invalid')
    exit_code = capture_state['exit_code']
    if exit_code is not None and (type(exit_code) is not int or not 0 <= exit_code <= 255):
        raise InputError('capture exit_code is invalid')
    if capture_state['status'] == 'EXITED' and exit_code is None:
        raise InputError('capture EXITED requires an exit code')

    serial = capture['serial']
    _require_keys(serial, {'file', 'sha256', 'size'}, label='capture serial')
    _check_unknown(serial, {'file', 'sha256', 'size'}, label='capture serial')
    if serial['file'] != 'serial.log' or type(serial['size']) is not int or serial['size'] < 0:
        raise InputError('capture serial entry is invalid')
    _hex(serial['sha256'], label='capture serial')

    firmware = capture['firmware']
    _require_keys(firmware, FIRMWARE_KEYS, label='capture firmware')
    _check_unknown(firmware, FIRMWARE_KEYS, label='capture firmware')
    if firmware['file'] != 'firmware.bin' or type(firmware['size']) is not int or firmware['size'] < 0:
        raise InputError('capture firmware entry is invalid')
    _hex(firmware['sha256'], label='capture firmware')
    _string(firmware['source'], label='capture firmware source')
    _string(firmware['version'], label='capture firmware version')
    _string(firmware['original_path'], label='capture firmware original_path')

    build = capture['build']
    _require_keys(build, BUILD_KEYS, label='capture build')
    _check_unknown(build, BUILD_KEYS, label='capture build')
    for key in BUILD_KEYS:
        _hex(build[key], label=f'capture build {key}')
    frozen_image = profile.data.get('inputs', {}).get('image', {}).get(
        'uncompressed_sha256')
    _hex(frozen_image, label='profile image uncompressed_sha256')
    if build['base_img_sha256'] != frozen_image:
        raise InputError('capture base image does not match the frozen profile image')

    qemu = capture['qemu']
    _require_keys(qemu, QEMU_KEYS, label='capture qemu')
    _check_unknown(qemu, QEMU_KEYS, label='capture qemu')
    _string(qemu['path'], label='capture qemu path')
    _string(qemu['version'], label='capture qemu version')
    _hex(qemu['sha256'], label='capture qemu sha256')

    started = _timestamp(capture['started_at'], label='capture started_at')
    finished = _timestamp(capture['finished_at'], label='capture finished_at')
    if finished < started:
        raise InputError('capture finished_at precedes started_at')

    limits = capture['limits']
    _require_keys(limits, LIMIT_KEYS, label='capture limits')
    _check_unknown(limits, LIMIT_KEYS, label='capture limits')
    for key in LIMIT_KEYS:
        _positive_int(limits[key], label=f'capture limits {key}')
    if serial['size'] > limits['serial_bytes']:
        raise InputError('capture serial size exceeds serial limit')

    cow = capture['cow_layer']
    _require_keys(cow, COW_KEYS, label='capture cow_layer')
    _check_unknown(cow, COW_KEYS, label='capture cow_layer')
    _string(cow['path'], label='capture cow_layer path')
    if '\x00' in cow['path']:
        raise InputError('capture cow_layer path contains NUL')
    _string(cow['format'], label='capture cow_layer format')
    _hex(cow['sha256'], label='capture cow_layer sha256')
    if type(cow['size']) is not int or cow['size'] < 0:
        raise InputError('capture cow_layer size is invalid')
    if cow['created'] is not True:
        raise InputError('capture cow_layer was not created')
    _hex(cow['base_img_sha256'], label='capture cow_layer base_img_sha256')
    if cow['base_img_sha256'] != build['base_img_sha256']:
        raise InputError('capture cow_layer base image binding mismatch')

    expected_read_only = {
        'kernel': build['kernel_sha256'],
        'initramfs': build['initramfs_sha256'],
        'base_img': build['base_img_sha256'],
        'firmware': firmware['sha256'],
        'qemu': qemu['sha256'],
    }
    paths = _check_read_only_inputs(capture['read_only_inputs'], expected_read_only,
                                    capture['input_integrity'])
    if paths['qemu'] != qemu['path']:
        raise InputError('capture qemu path does not match read-only identity')
    _check_argv(capture['argv'], paths, qemu['path'], cow)

    implementation = capture['capture_implementation']
    _require_keys(implementation, IMPLEMENTATION_KEYS, label='capture implementation')
    _check_unknown(implementation, IMPLEMENTATION_KEYS, label='capture implementation')
    files = implementation['files']
    if not isinstance(files, list) or not files:
        raise InputError('capture implementation files are invalid')
    seen_paths = set()
    for index, item in enumerate(files):
        _require_keys(item, IMPLEMENTATION_FILE_KEYS, label=f'capture implementation file {index}')
        _check_unknown(item, IMPLEMENTATION_FILE_KEYS, label=f'capture implementation file {index}')
        relative = _safe_relative(item['path'], label=f'capture implementation file {index} path')
        if relative in seen_paths:
            raise InputError(f'capture implementation has duplicate path: {relative}')
        seen_paths.add(relative)
        _hex(item['sha256'], label=f'capture implementation file {index} sha256')

    campaign = capture['campaign']
    if campaign is not None:
        _require_keys(campaign, {'campaign_id', 'slot_id', 'campaign_sha256'}, label='capture campaign')
        _check_unknown(campaign, {'campaign_id', 'slot_id', 'campaign_sha256'}, label='capture campaign')
        slot = campaign['slot_id']
        if (not isinstance(campaign['campaign_id'], str) or not campaign['campaign_id']
                or not isinstance(slot, str) or not slot or slot in ('.', '..')
                or any(character not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-' for character in slot)):
            raise InputError('capture campaign binding is invalid')
        _hex(campaign['campaign_sha256'], label='capture campaign')

    limit = profile.data['environment']['serial_limit_bytes']
    serial_raw = read_regular_bytes(path / 'serial.log', label='run serial log',
                                    max_bytes=limit)
    firmware_raw = read_regular_bytes(path / 'firmware.bin', label='run firmware',
                                      max_bytes=limit)
    if len(serial_raw) != serial['size'] or sha256_bytes(serial_raw) != serial['sha256']:
        raise InputError('run serial log hash/size mismatch')
    if len(firmware_raw) != firmware['size'] or sha256_bytes(firmware_raw) != firmware['sha256']:
        raise InputError('run firmware hash/size mismatch')
    implementation_hash = sha256_bytes(canonical_bytes(implementation))
    return RunPackage(
        path=path, capture=capture, capture_sha256=sha256_bytes(capture_raw),
        serial_bytes=serial_raw, serial_sha256=sha256_bytes(serial_raw),
        firmware_sha256=sha256_bytes(firmware_raw), qemu_sha256=qemu['sha256'],
        capture_implementation_sha256=implementation_hash,
        selection=list(selection),
    )
