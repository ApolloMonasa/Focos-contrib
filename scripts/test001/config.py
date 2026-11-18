"""读取并核对冻结的 PROFILE、CRITERIA 与分析实现清单。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import InputError
from .fsutil import read_regular_bytes, sha256_bytes, sha256_file
from .strictjson import StrictJSONError, canonical_hash, loads

ID_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
SHA_RE = re.compile(r'[0-9a-f]{64}\Z')
SHARD_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
MODE_RE = re.compile(r'0o[0-7]{3,4}\Z')
INVOCATION_KEYS = {
    'ordinal', 'invocation_id', 'group', 'libc', 'name', 'kind', 'argv',
    'cwd', 'source', 'judge', 'path', 'sha256', 'size', 'mode',
}
GROUP_KEYS = {
    'kind', 'adapter', 'cases_per_libc', 'script_file', 'script_sha256',
    'judge_file', 'judge_sha256', 'judge_source', 'version', 'wrapper_file',
    'wrapper_sha256', 'data_trees', 'planned_occurrences',
}
PROFILE_KEYS = {
    'schema_version', 'kind', 'profile_id', 'revision', 'frozen_at',
    'description', 'inputs', 'groups', 'order', 'libcs', 'invocations',
    'shards', 'environment', 'protocol', 'profile_hash',
}
CRITERIA_KEYS = {
    'schema_version', 'kind', 'criteria_id', 'revision', 'frozen_at',
    'supported_profiles', 'adapters', 'mapping', 'acceptance',
    'implementation', 'criteria_hash',
}
ACCEPTANCE_KEYS = {
    'external_log_acceptance', 'require_origin', 'require_capture_status',
    'require_capture_exit_code', 'require_input_integrity',
    'require_protocol', 'require_sections', 'require_all_judgments',
    'forbid_extras', 'forbid_judge_errors', 'ltp_summary_required',
}
ADAPTER_KEYS = {'kind', 'entry', 'sha256', 'timeout_seconds', 'max_output_bytes', 'version'}
LIBACS = ('musl', 'glibc')


def _is_int(value) -> bool:
    return type(value) is int


def _safe_member(path: str, *, label: str) -> None:
    if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path or '\x00' in path:
        raise InputError(f'{label}: unsafe path: {path!r}')
    parts = PurePosixPath(path).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        raise InputError(f'{label}: unsafe path: {path!r}')


def _require_keys(value: dict, required: set, *, label: str):
    if not isinstance(value, dict):
        raise InputError(f'{label}: expected an object')
    missing = sorted(required - set(value))
    if missing:
        raise InputError(f'{label}: missing fields: {", ".join(missing)}')


def _check_unknown_keys(value: dict, allowed: set, *, label: str):
    extra = sorted(set(value) - allowed)
    if extra:
        raise InputError(f'{label}: unknown fields: {", ".join(extra)}')


def _repo_path(root: Path, relative: str, *, label: str) -> Path:
    _safe_member(relative, label=label)
    return root / relative


@dataclass(frozen=True)
class Shard:
    shard_id: str
    group: str
    libc: str
    start: int
    end: int

    @property
    def selected_total(self) -> int:
        return self.end - self.start


@dataclass
class Profile:
    root: Path
    path: Path
    file_sha256: str
    data: dict
    invocations: list[dict]
    shards: list[Shard]

    @property
    def profile_id(self) -> str:
        return self.data['profile_id']

    @property
    def revision(self) -> int:
        return self.data['revision']

    @property
    def profile_hash(self) -> str:
        return self.data['profile_hash']

    @property
    def groups(self) -> list[str]:
        return list(self.data['order'])

    @property
    def libcs(self) -> list[str]:
        return list(self.data['libcs'])

    def shard(self, shard_id: str) -> Shard:
        for shard in self.shards:
            if shard.shard_id == shard_id:
                return shard
        raise InputError(f'unknown shard: {shard_id}')

    def shards_for(self, group: str, libc: str) -> list[Shard]:
        if group not in self.groups:
            raise InputError(f'unknown group: {group}')
        if libc not in self.libcs:
            raise InputError(f'unknown libc: {libc}')
        result = [s for s in self.shards if s.group == group and s.libc == libc]
        if not result:
            raise InputError(f'profile has no shard for {group}-{libc}')
        return result

    def items_for(self, shard: Shard) -> list[dict]:
        return self.invocations[shard.start:shard.end]


@dataclass
class Criteria:
    root: Path
    path: Path
    base_dir: Path
    file_sha256: str
    data: dict
    implementation_files: list[dict]

    @property
    def criteria_id(self) -> str:
        return self.data['criteria_id']

    @property
    def revision(self) -> int:
        return self.data['revision']

    @property
    def criteria_hash(self) -> str:
        return self.data['criteria_hash']

    def supports(self, profile: Profile):
        for item in self.data['supported_profiles']:
            if (item.get('profile_id') == profile.profile_id
                    and item.get('revision') == profile.revision
                    and item.get('profile_hash') == profile.profile_hash):
                return
        raise InputError(
            f'criteria {self.criteria_id} does not support profile '
            f'{profile.profile_id} revision {profile.revision}'
        )

    def adapter(self, group: str) -> dict:
        try:
            return self.data['adapters'][group]
        except KeyError as exc:
            raise InputError(f'criteria has no adapter for group: {group}') from exc

    def check_profile(self, profile: Profile):
        self.supports(profile)
        mapping = self.data['mapping']
        if mapping.get('mode') != 'identity':
            raise InputError('unsupported criteria mapping mode')
        if mapping.get('profile_hash') != profile.profile_hash:
            raise InputError('criteria mapping does not name the selected profile hash')
        adapters = set(self.data['adapters'])
        if adapters != set(profile.groups):
            missing = sorted(set(profile.groups) - adapters)
            extra = sorted(adapters - set(profile.groups))
            raise InputError(
                f'criteria adapters do not match profile groups; '
                f'missing={missing}, extra={extra}'
            )
        judge = profile.data['inputs']['judge']
        official = judge.get('official_files', {})
        team = judge.get('team_files', {})
        for group in profile.groups:
            meta = profile.data['groups'][group]
            adapter = self.data['adapters'][group]
            if meta['kind'] == 'ltp':
                if adapter['kind'] != 'builtin':
                    raise InputError(f'criteria adapter kind does not match LTP group: {group}')
                continue
            if adapter['kind'] != 'subprocess':
                raise InputError(f'criteria adapter kind does not match non-LTP group: {group}')
            name = Path(meta['judge_file']).name
            source = official if meta.get('judge_source') == 'official' else team
            expected = source.get(name)
            if expected is None or meta.get('judge_sha256') != expected or adapter['sha256'] != expected:
                raise InputError(f'criteria judge identity does not match profile: {group}')


def _load_json_file(path: Path, *, label: str) -> tuple[bytes, dict]:
    raw = read_regular_bytes(path, label=label, max_bytes=16 * 1024 * 1024)
    try:
        data = loads(raw, label=label)
    except StrictJSONError as exc:
        raise InputError(str(exc)) from exc
    if not isinstance(data, dict):
        raise InputError(f'{label}: expected a JSON object')
    return raw, data


def load_catalog(root: Path) -> dict:
    path = root / 'tests/test001/catalog.json'
    _, data = _load_json_file(path, label='test001 catalog')
    _require_keys(data, {'schema_version', 'kind', 'profiles', 'criteria'},
                  label='test001 catalog')
    if data['schema_version'] != 2 or data['kind'] != 'test001_catalog':
        raise InputError('unsupported test001 catalog schema')
    for section in ('profiles', 'criteria'):
        table = data[section]
        if not isinstance(table, dict) or not table:
            raise InputError(f'test001 catalog: {section} must be a non-empty object')
        for key, value in table.items():
            if not ID_RE.fullmatch(key):
                raise InputError(f'test001 catalog: invalid {section} id: {key}')
            if not isinstance(value, dict):
                raise InputError(f'test001 catalog: invalid {section} entry: {key}')
            _require_keys(value, {'path'}, label=f'catalog {section}.{key}')
            _check_unknown_keys(value, {'path'}, label=f'catalog {section}.{key}')
            _safe_member(value['path'], label=f'catalog {section}.{key}')
    return data


def _catalog_path(root: Path, catalog: dict, section: str, item_id: str) -> Path:
    try:
        relative = catalog[section][item_id]['path']
    except KeyError as exc:
        raise InputError(f'unknown {section}: {item_id}') from exc
    return root / 'tests/test001' / relative


def load_profile(root: Path, profile_id: str, catalog: dict | None = None) -> Profile:
    catalog = catalog or load_catalog(root)
    path = _catalog_path(root, catalog, 'profiles', profile_id)
    raw, data = _load_json_file(path, label=f'profile {profile_id}')
    _require_keys(data, PROFILE_KEYS, label=f'profile {profile_id}')
    _check_unknown_keys(data, PROFILE_KEYS, label=f'profile {profile_id}')
    if data['schema_version'] != 2 or data['kind'] != 'test001_profile':
        raise InputError('unsupported profile schema')
    if data['profile_id'] != profile_id or not _is_int(data['revision']) or data['revision'] < 1:
        raise InputError('profile id/revision mismatch')
    expected_hash = canonical_hash(data, omit=('profile_hash',))
    if data['profile_hash'] != expected_hash:
        raise InputError('profile hash mismatch')

    inputs = data['inputs']
    _require_keys(inputs, {'testsuite', 'image', 'ltp', 'judge'}, label='profile inputs')
    for key in ('testsuite', 'image', 'ltp', 'judge'):
        if not isinstance(inputs[key], dict):
            raise InputError(f'profile input is not an object: {key}')
    testsuite = inputs['testsuite']
    if (not isinstance(testsuite.get('commit'), str)
            or not re.fullmatch(r'[0-9a-f]{40}', testsuite['commit'])):
        raise InputError('profile testsuite commit is invalid')
    image = inputs['image']
    for key in ('compressed_sha256', 'uncompressed_sha256'):
        if not isinstance(image.get(key), str) or not SHA_RE.fullmatch(image[key]):
            raise InputError(f'profile image hash is invalid: {key}')
    for key in ('compressed_size', 'uncompressed_size'):
        if not _is_int(image.get(key)) or image[key] <= 0:
            raise InputError(f'profile image size is invalid: {key}')
    judge = inputs['judge']
    for key in ('official_files', 'team_files'):
        table = judge.get(key)
        if not isinstance(table, dict) or not table:
            raise InputError(f'profile judge table is invalid: {key}')
        if any(not isinstance(value, str) or not SHA_RE.fullmatch(value)
               for value in table.values()):
            raise InputError(f'profile judge hash is invalid: {key}')

    groups = data['order']
    libcs = data['libcs']
    if (not isinstance(groups, list) or not groups or len(set(groups)) != len(groups)
            or any(not isinstance(g, str) or not ID_RE.fullmatch(g) for g in groups)):
        raise InputError('profile order is invalid')
    if libcs != list(LIBACS):
        raise InputError('profile libcs must be ["musl", "glibc"]')
    if not isinstance(data['groups'], dict) or set(data['groups']) != set(groups):
        raise InputError('profile group metadata does not match order')
    environment = data['environment']
    _require_keys(environment, {'platform', 'subject', 'cwd', 'root',
                                'network_required', 'serial_limit_bytes'},
                  label='profile environment')
    _check_unknown_keys(environment, {'platform', 'subject', 'cwd', 'root',
                                      'network_required', 'serial_limit_bytes', 'notes'},
                        label='profile environment')
    if (not isinstance(environment['serial_limit_bytes'], int)
            or environment['serial_limit_bytes'] <= 0
            or environment['serial_limit_bytes'] > 1024 * 1024 * 1024):
        raise InputError('profile serial_limit_bytes is invalid')
    if environment['root'] is not True:
        raise InputError('profile environment must require root')
    if environment['platform'] != 'qemu_riscv64' or environment['subject'] != 'reference_linux':
        raise InputError('profile environment platform/subject is invalid')
    if environment['cwd'] != {'musl': '/musl', 'glibc': '/glibc'}:
        raise InputError('profile environment cwd is invalid')
    if (not isinstance(environment['network_required'], list)
            or any(not isinstance(group, str) or group not in groups
                   for group in environment['network_required'])):
        raise InputError('profile network_required is invalid')

    group_meta = data['groups']
    for group in groups:
        meta = group_meta[group]
        if not isinstance(meta, dict):
            raise InputError(f'profile group metadata is invalid: {group}')
        _check_unknown_keys(meta, GROUP_KEYS, label=f'profile group {group}')
        _require_keys(meta, {'kind', 'adapter', 'cases_per_libc'}, label=f'profile group {group}')
        if meta['kind'] not in ('non_ltp', 'ltp') or not ID_RE.fullmatch(str(meta['adapter'])):
            raise InputError(f'profile group kind/adapter is invalid: {group}')
        counts = meta['cases_per_libc']
        if meta['kind'] == 'non_ltp':
            _require_keys(meta, {'kind', 'adapter', 'cases_per_libc', 'script_file',
                                 'script_sha256', 'judge_file', 'judge_sha256',
                                 'judge_source'}, label=f'profile group {group}')
            if not _is_int(counts) or counts <= 0:
                raise InputError(f'profile group case count is invalid: {group}')
            if meta['judge_source'] not in ('official', 'team'):
                raise InputError(f'profile group judge source is invalid: {group}')
            if not isinstance(meta['judge_sha256'], str) or not SHA_RE.fullmatch(meta['judge_sha256']):
                raise InputError(f'profile group judge hash is invalid: {group}')
            if meta['script_file'] != f'{group}_testcode.sh':
                raise InputError(f'profile group script file is invalid: {group}')
            planned = meta.get('planned_occurrences', {})
            if (not isinstance(planned, dict)
                    or any(not isinstance(name, str) or not name
                           or not _is_int(count) or count < 1
                           for name, count in planned.items())):
                raise InputError(f'profile group planned occurrences are invalid: {group}')
            hashes = meta['script_sha256']
            if not isinstance(hashes, dict) or set(hashes) != set(libcs):
                raise InputError(f'profile group script hashes are invalid: {group}')
            if any(not isinstance(value, str) or not SHA_RE.fullmatch(value)
                   for value in hashes.values()):
                raise InputError(f'profile group script hashes are invalid: {group}')
        else:
            _require_keys(meta, {'kind', 'adapter', 'cases_per_libc', 'version',
                                 'wrapper_file', 'wrapper_sha256', 'data_trees'},
                          label=f'profile group {group}')
            _require_keys(counts, set(libcs), label=f'profile group {group} cases')
            if any(not _is_int(counts[libc]) or counts[libc] <= 0 for libc in libcs):
                raise InputError(f'profile group case count is invalid: {group}')
            if meta['wrapper_file'] != 'ltp_testcode.sh':
                raise InputError(f'profile LTP wrapper file is invalid: {group}')
            hashes = meta['wrapper_sha256']
            if not isinstance(hashes, dict) or set(hashes) != set(libcs):
                raise InputError('profile LTP wrapper hashes are invalid')
            if any(not isinstance(value, str) or not SHA_RE.fullmatch(value)
                   for value in hashes.values()):
                raise InputError('profile LTP wrapper hashes are invalid')
            trees = meta['data_trees']
            if not isinstance(trees, dict) or set(trees) != set(libcs):
                raise InputError('profile LTP data trees are invalid')
            for libc in libcs:
                if not isinstance(trees[libc], dict) or set(trees[libc]) != {'datafiles', 'ftrace_stress'}:
                    raise InputError('profile LTP data trees are invalid')
                for value in trees[libc].values():
                    if (not isinstance(value, dict)
                            or not isinstance(value.get('sha256'), str)
                            or not SHA_RE.fullmatch(value['sha256'])
                            or not _is_int(value.get('files')) or value['files'] <= 0):
                        raise InputError('profile LTP data tree hash is invalid')

    inv_spec = data['invocations']
    _require_keys(inv_spec, {'file', 'sha256', 'line_count', 'by_group_libc', 'total_selected'},
                  label='profile invocations')
    _check_unknown_keys(inv_spec, {'file', 'sha256', 'line_count', 'by_group_libc', 'total_selected'},
                        label='profile invocations')
    _safe_member(inv_spec['file'], label='profile invocation file')
    inv_path = path.parent / inv_spec['file']
    inv_raw = read_regular_bytes(inv_path, label='profile invocation list',
                                max_bytes=64 * 1024 * 1024)
    inv_hash = sha256_bytes(inv_raw)
    if inv_hash != inv_spec['sha256']:
        raise InputError('profile invocation list hash mismatch')
    try:
        lines = inv_raw.decode('utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise InputError('profile invocation list is not UTF-8') from exc
    if not lines or any(not line.strip() for line in lines):
        raise InputError('profile invocation list is empty or has blank lines')
    if len(lines) != inv_spec['line_count'] or len(lines) != inv_spec['total_selected']:
        raise InputError('profile invocation count mismatch')

    invocations = []
    seen_ids = set()
    seen_names = set()
    counts = {group: {libc: 0 for libc in libcs} for group in groups}
    for ordinal, line in enumerate(lines):
        try:
            row = loads(line, label=f'invocation line {ordinal + 1}')
        except StrictJSONError as exc:
            raise InputError(str(exc)) from exc
        if not isinstance(row, dict):
            raise InputError(f'invocation line {ordinal + 1}: expected an object')
        _require_keys(row, INVOCATION_KEYS - {'judge', 'path', 'sha256', 'size', 'mode'},
                      label=f'invocation line {ordinal + 1}')
        if row['kind'] == 'judge_item' and 'judge' not in row:
            raise InputError(f'invocation line {ordinal + 1}: missing judge')
        _check_unknown_keys(row, INVOCATION_KEYS, label=f'invocation line {ordinal + 1}')
        if row['ordinal'] != ordinal:
            raise InputError(f'invocation line {ordinal + 1}: ordinal mismatch')
        if row['group'] not in groups or row['libc'] not in libcs:
            raise InputError(f'invocation line {ordinal + 1}: unknown group/libc')
        if (not isinstance(row['name'], str) or not row['name'] or len(row['name']) > 512
                or '\n' in row['name'] or '\x00' in row['name']):
            raise InputError(f'invocation line {ordinal + 1}: invalid name')
        if not isinstance(row['invocation_id'], str) or not row['invocation_id']:
            raise InputError(f'invocation line {ordinal + 1}: invalid invocation id')
        if row['invocation_id'] in seen_ids:
            raise InputError(f'invocation line {ordinal + 1}: duplicate invocation id')
        seen_ids.add(row['invocation_id'])
        meta = group_meta[row['group']]
        expected_id = (f'ltp/{row["libc"]}/{row["name"]}'
                       if meta['kind'] == 'ltp'
                       else f'{row["group"]}/{row["libc"]}/{row["name"]}')
        if row['invocation_id'] != expected_id:
            raise InputError(f'invocation line {ordinal + 1}: invocation id does not match group/libc/name')
        name_key = (row['group'], row['libc'], row['name'])
        if name_key in seen_names:
            raise InputError(f'invocation line {ordinal + 1}: duplicate case name')
        seen_names.add(name_key)
        if row['cwd'] != f'/{row["libc"]}':
            raise InputError(f'invocation line {ordinal + 1}: cwd does not match libc')
        if row['argv']:
            raise InputError(f'invocation line {ordinal + 1}: unexpected argv')
        expected_source = ('image:ltp_testcode.sh' if meta['kind'] == 'ltp'
                           else f'image:{row["group"]}_testcode.sh')
        if row['source'] != expected_source:
            raise InputError(f'invocation line {ordinal + 1}: source does not match group')
        if row['kind'] not in ('judge_item', 'elf', 'script', 'data', 'other'):
            raise InputError(f'invocation line {ordinal + 1}: invalid kind')
        if not isinstance(row['argv'], list) or any(not isinstance(x, str) for x in row['argv']):
            raise InputError(f'invocation line {ordinal + 1}: invalid argv')
        if not isinstance(row['cwd'], str) or not row['cwd'].startswith('/'):
            raise InputError(f'invocation line {ordinal + 1}: invalid cwd')
        if not isinstance(row['source'], str) or not row['source']:
            raise InputError(f'invocation line {ordinal + 1}: invalid source')
        if meta['kind'] == 'ltp':
            for key in ('path', 'sha256', 'size', 'mode'):
                if key not in row:
                    raise InputError(f'invocation line {ordinal + 1}: missing {key}')
            if (not isinstance(row['path'], str)
                    or not row['path'].startswith('ltp/testcases/bin/')
                    or PurePosixPath(row['path']).name != row['name']):
                raise InputError(f'invocation line {ordinal + 1}: invalid LTP path')
            if not isinstance(row['sha256'], str) or not SHA_RE.fullmatch(row['sha256']):
                raise InputError(f'invocation line {ordinal + 1}: invalid sha256')
            if not _is_int(row['size']) or row['size'] < 0:
                raise InputError(f'invocation line {ordinal + 1}: invalid size')
            if not isinstance(row['mode'], str) or not MODE_RE.fullmatch(row['mode']):
                raise InputError(f'invocation line {ordinal + 1}: invalid mode')
        else:
            if 'judge' not in row or not isinstance(row['judge'], str):
                raise InputError(f'invocation line {ordinal + 1}: missing judge')
            if row['kind'] != 'judge_item' or row['judge'] != meta['judge_file']:
                raise InputError(f'invocation line {ordinal + 1}: judge/kind does not match group')
        counts[row['group']][row['libc']] += 1
        invocations.append(row)

    for group in groups:
        meta = group_meta[group]
        if meta['kind'] != 'non_ltp':
            continue
        group_names = {row['name'] for row in invocations if row['group'] == group}
        for name in meta.get('planned_occurrences', {}):
            if name not in group_names:
                raise InputError(f'profile planned occurrence names an unknown case: {group}/{name}')

    declared = inv_spec['by_group_libc']
    if not isinstance(declared, dict) or set(declared) != set(groups):
        raise InputError('profile invocation group counts are invalid')
    for group in groups:
        if not isinstance(declared[group], dict) or set(declared[group]) != set(libcs):
            raise InputError(f'profile invocation counts are invalid: {group}')
        meta = group_meta[group]
        for libc in libcs:
            if counts[group][libc] != declared[group][libc]:
                raise InputError(f'profile invocation count mismatch: {group}-{libc}')
            expected_cases = meta['cases_per_libc'] if meta['kind'] == 'non_ltp' else meta['cases_per_libc'][libc]
            if counts[group][libc] != expected_cases:
                raise InputError(f'profile group case count mismatch: {group}-{libc}')

    if not isinstance(data['shards'], list) or not data['shards']:
        raise InputError('profile shards must be a non-empty list')
    shards = []
    shard_ids = set()
    for raw_shard in data['shards']:
        _require_keys(raw_shard, {'shard_id', 'group', 'libc', 'start', 'end', 'selected_total'},
                      label='profile shard')
        _check_unknown_keys(raw_shard, {'shard_id', 'group', 'libc', 'start', 'end', 'selected_total'},
                            label='profile shard')
        shard_id = raw_shard['shard_id']
        if (not isinstance(shard_id, str) or not SHARD_RE.fullmatch(shard_id)
                or shard_id in shard_ids):
            raise InputError(f'profile shard id is invalid/duplicate: {shard_id!r}')
        shard_ids.add(shard_id)
        if (raw_shard['group'] not in groups or raw_shard['libc'] not in libcs
                or not _is_int(raw_shard['start']) or not _is_int(raw_shard['end'])
                or not 0 <= raw_shard['start'] < raw_shard['end'] <= len(invocations)
                or raw_shard['selected_total'] != raw_shard['end'] - raw_shard['start']):
            raise InputError(f'profile shard range is invalid: {shard_id}')
        shard = Shard(shard_id, raw_shard['group'], raw_shard['libc'],
                      raw_shard['start'], raw_shard['end'])
        for row in invocations[shard.start:shard.end]:
            if row['group'] != shard.group or row['libc'] != shard.libc:
                raise InputError(f'profile shard range crosses group/libc: {shard_id}')
        shards.append(shard)
    shards.sort(key=lambda s: s.start)
    cursor = 0
    for shard in shards:
        if shard.start != cursor:
            raise InputError('profile shards do not exactly cover the invocation list')
        cursor = shard.end
    if cursor != len(invocations):
        raise InputError('profile shards do not exactly cover the invocation list')

    return Profile(root=root, path=path, file_sha256=sha256_bytes(raw),
                   data=data, invocations=invocations, shards=shards)


def actual_implementation_files(root: Path, roots: list[str]) -> dict[str, Path]:
    """实现清单与分析器共享的枚举：缺失根、符号链接和特殊文件都拒绝。"""
    actual = {}
    for relative in roots:
        _safe_member(relative, label='implementation root')
        path = root / relative
        if not path.exists():
            raise InputError(f'implementation root is missing: {relative}')
        if path.is_symlink():
            raise InputError(f'implementation root is a symlink: {relative}')
        if path.is_file():
            actual[relative] = path
            continue
        if not path.is_dir():
            raise InputError(f'implementation root is not a file or directory: {relative}')
        for item in path.rglob('*'):
            if '__pycache__' in item.parts or item.suffix in ('.pyc', '.pyo'):
                continue
            if item.is_symlink():
                raise InputError(f'implementation contains a symlink: {item}')
            if item.is_dir():
                continue
            if not item.is_file():
                raise InputError(f'implementation contains a non-regular file: {item}')
            actual[item.relative_to(root).as_posix()] = item
    return actual


def _verify_implementation(root: Path, criteria_path: Path, data: dict) -> list[dict]:
    spec = data['implementation']
    _require_keys(spec, {'manifest', 'sha256', 'roots'}, label='criteria implementation')
    _check_unknown_keys(spec, {'manifest', 'sha256', 'roots'}, label='criteria implementation')
    _safe_member(spec['manifest'], label='implementation manifest')
    if not isinstance(spec['roots'], list) or not spec['roots']:
        raise InputError('criteria implementation roots are invalid')
    manifest_path = criteria_path.parent / spec['manifest']
    raw = read_regular_bytes(manifest_path, label='implementation manifest',
                             max_bytes=4 * 1024 * 1024)
    if sha256_bytes(raw) != spec['sha256']:
        raise InputError('implementation manifest hash mismatch')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise InputError('implementation manifest is not UTF-8') from exc
    expected = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split('  ', 1)
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]):
            raise InputError('malformed implementation manifest line')
        relative = parts[1]
        _safe_member(relative, label='implementation manifest')
        if relative in expected:
            raise InputError(f'duplicate implementation manifest entry: {relative}')
        expected[relative] = parts[0]
    actual = actual_implementation_files(root, spec['roots'])
    if set(expected) != set(actual):
        missing = sorted(set(actual) - set(expected))
        extra = sorted(set(expected) - set(actual))
        raise InputError(
            'implementation manifest does not match files; '
            f'missing from manifest={missing[:8]}, stale={extra[:8]}'
        )
    files = []
    for relative in sorted(actual):
        digest = sha256_file(actual[relative])
        if digest != expected[relative]:
            raise InputError(f'implementation file changed: {relative}')
        files.append({'path': relative, 'sha256': digest})
    return files


def load_criteria(root: Path, criteria_id: str, catalog: dict | None = None) -> Criteria:
    catalog = catalog or load_catalog(root)
    path = _catalog_path(root, catalog, 'criteria', criteria_id)
    raw, data = _load_json_file(path, label=f'criteria {criteria_id}')
    _require_keys(data, CRITERIA_KEYS, label=f'criteria {criteria_id}')
    _check_unknown_keys(data, CRITERIA_KEYS, label=f'criteria {criteria_id}')
    if data['schema_version'] != 2 or data['kind'] != 'test001_criteria':
        raise InputError('unsupported criteria schema')
    if data['criteria_id'] != criteria_id or not _is_int(data['revision']) or data['revision'] < 1:
        raise InputError('criteria id/revision mismatch')
    expected_hash = canonical_hash(data, omit=('criteria_hash',))
    if data['criteria_hash'] != expected_hash:
        raise InputError('criteria hash mismatch')
    profiles = data['supported_profiles']
    if not isinstance(profiles, list) or not profiles:
        raise InputError('criteria supported_profiles is empty')
    for item in profiles:
        _require_keys(item, {'profile_id', 'revision', 'profile_hash'}, label='supported profile')
        _check_unknown_keys(item, {'profile_id', 'revision', 'profile_hash'}, label='supported profile')
        if (not isinstance(item['profile_id'], str) or not ID_RE.fullmatch(item['profile_id'])
                or not _is_int(item['revision'])
                or not isinstance(item['profile_hash'], str) or not SHA_RE.fullmatch(item['profile_hash'])):
            raise InputError('criteria supported profile entry is invalid')
    mapping = data['mapping']
    _require_keys(mapping, {'mode', 'profile_hash'}, label='criteria mapping')
    _check_unknown_keys(mapping, {'mode', 'profile_hash', 'notes'}, label='criteria mapping')
    if mapping['mode'] != 'identity' or not isinstance(mapping['profile_hash'], str) or not SHA_RE.fullmatch(mapping['profile_hash']):
        raise InputError('criteria mapping is invalid')
    acceptance = data['acceptance']
    _require_keys(acceptance, ACCEPTANCE_KEYS, label='criteria acceptance')
    _check_unknown_keys(acceptance, ACCEPTANCE_KEYS, label='criteria acceptance')
    if acceptance['external_log_acceptance'] != 'BLOCKED':
        raise InputError('external logs must not be accepted')
    for key in ('forbid_extras', 'forbid_judge_errors', 'ltp_summary_required'):
        if not isinstance(acceptance[key], bool):
            raise InputError(f'criteria acceptance {key} must be boolean')
    expected_policy = {
        'require_origin': 'recorded_capture',
        'require_capture_status': 'EXITED',
        'require_input_integrity': 'VERIFIED',
        'require_protocol': 'VALID',
        'require_sections': 'COMPLETE',
        'require_all_judgments': 'PASS',
    }
    for key, expected in expected_policy.items():
        if acceptance[key] != expected:
            raise InputError(f'criteria acceptance {key} must be {expected}')
    if acceptance['require_capture_exit_code'] != 0:
        raise InputError('criteria require_capture_exit_code must be 0')

    adapters = data['adapters']
    if not isinstance(adapters, dict) or not adapters:
        raise InputError('criteria adapters is empty')
    for group, spec in adapters.items():
        if not ID_RE.fullmatch(group) or not isinstance(spec, dict):
            raise InputError(f'criteria adapter is invalid: {group}')
        _check_unknown_keys(spec, ADAPTER_KEYS, label=f'criteria adapter {group}')
        _require_keys(spec, {'kind', 'entry'}, label=f'criteria adapter {group}')
        if spec['kind'] == 'builtin':
            if spec['entry'] != 'ltp_native':
                raise InputError(f'criteria adapter {group}: unknown builtin')
        elif spec['kind'] == 'subprocess':
            _safe_member(spec['entry'], label=f'criteria adapter {group}')
            expected = spec.get('sha256')
            if not isinstance(expected, str) or not SHA_RE.fullmatch(expected):
                raise InputError(f'criteria adapter {group}: missing entry hash')
            if (not _is_int(spec.get('timeout_seconds')) or not 1 <= spec['timeout_seconds'] <= 600
                    or not _is_int(spec.get('max_output_bytes'))
                    or not 1024 <= spec['max_output_bytes'] <= 64 * 1024 * 1024):
                raise InputError(f'criteria adapter {group}: invalid limits')
        else:
            raise InputError(f'criteria adapter {group}: invalid kind')

    implementation = _verify_implementation(root, path, data)
    implementation_by_path = {item['path']: item['sha256'] for item in implementation}
    for group, spec in adapters.items():
        if spec['kind'] != 'subprocess':
            continue
        relative = (path.parent / spec['entry']).relative_to(root).as_posix()
        actual = implementation_by_path.get(relative)
        if actual is None or actual != spec['sha256']:
            raise InputError(f'criteria adapter {group}: entry hash does not match implementation')

    return Criteria(root=root, path=path, base_dir=path.parent,
                    file_sha256=sha256_bytes(raw), data=data,
                    implementation_files=implementation)
