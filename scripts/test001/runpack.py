"""只读运行包：固定布局、清单哈希和显式 shard 选择。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Profile
from .errors import InputError
from .fsutil import read_regular_bytes, sha256_bytes
from .strictjson import StrictJSONError, loads

CAPTURE_KEYS = {
    'schema_version', 'kind', 'run_id', 'profile', 'selection', 'subject',
    'platform', 'capture', 'input_integrity', 'serial', 'build', 'firmware',
    'campaign',
}
CAPTURE_STATUS = {'EXITED', 'TIMEOUT', 'OUTPUT_LIMIT', 'START_ERROR', 'NOT_RECORDED'}
INTEGRITY = {'VERIFIED', 'CHANGED', 'UNVERIFIED'}


@dataclass
class RunPackage:
    path: Path
    capture: dict
    capture_sha256: str
    serial_bytes: bytes
    serial_sha256: str
    firmware_sha256: str
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
    _require_keys(firmware, {'file', 'sha256', 'size'}, label='capture firmware')
    _check_unknown(firmware, {'file', 'sha256', 'size'}, label='capture firmware')
    if firmware['file'] != 'firmware.bin' or type(firmware['size']) is not int or firmware['size'] < 0:
        raise InputError('capture firmware entry is invalid')
    _hex(firmware['sha256'], label='capture firmware')
    build = capture['build']
    _require_keys(build, {'manifest_sha256'}, label='capture build')
    _check_unknown(build, {'manifest_sha256'}, label='capture build')
    _hex(build['manifest_sha256'], label='capture build')
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
    return RunPackage(
        path=path, capture=capture, capture_sha256=sha256_bytes(capture_raw),
        serial_bytes=serial_raw, serial_sha256=sha256_bytes(serial_raw),
        firmware_sha256=sha256_bytes(firmware_raw), selection=list(selection),
    )
