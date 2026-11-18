"""冻结结果适配器的私有快照、子进程执行与输出规范化。"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Criteria, Profile
from .errors import InputError
from .fsutil import sha256_bytes, sha256_file
from .strictjson import StrictJSONError, loads


@dataclass
class AdapterOutput:
    items: dict[str, dict]
    error: str | None = None
    stderr: str = ''


def _walk_regular_files(directory: Path) -> list[Path]:
    if directory.is_symlink():
        raise InputError(f'adapter directory is a symlink: {directory}')
    if not directory.is_dir():
        raise InputError(f'adapter directory is missing: {directory}')
    files = []
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise InputError(f'adapter tree contains a symlink: {path}')
        if path.is_dir():
            continue
        if not path.is_file():
            raise InputError(f'adapter tree contains a special file: {path}')
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        files.append(path)
    return sorted(files)


def prepare_adapter_snapshot(criteria: Criteria, profile: Profile, destination: Path) -> Path:
    """复制 CRITERIA 私有快照；names.json 由本次 PROFILE 生成。"""
    source = criteria.base_dir / 'adapters'
    target = destination / 'adapters'
    target.mkdir()
    implementation = {item['path']: item['sha256'] for item in criteria.implementation_files}
    for path in _walk_regular_files(source):
        relative = path.relative_to(source)
        actual = path.relative_to(criteria.root).as_posix()
        # 以相对仓库的路径核对实现清单；拒绝清单外的文件。
        if actual not in implementation:
            raise InputError(f'adapter file is not in the implementation manifest: {actual}')
        raw = path.read_bytes()
        if sha256_bytes(raw) != implementation[actual]:
            raise InputError(f'adapter file changed while freezing: {actual}')
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
        output.chmod(0o444)
        if sha256_file(output) != implementation[actual]:
            raise InputError(f'adapter snapshot changed: {actual}')

    names = {}
    for group in profile.groups:
        meta = profile.data['groups'][group]
        if meta['kind'] == 'ltp':
            continue
        by_libc = {}
        for libc in profile.libcs:
            shard = profile.shards_for(group, libc)[0]
            by_libc[libc] = [item['name'] for item in profile.items_for(shard)]
        if len({tuple(value) for value in by_libc.values()}) != 1:
            raise InputError(f'group names differ by libc: {group}')
        names[group] = {'subcases': by_libc['musl']}
    (target / 'names.json').write_text(
        json.dumps(names, ensure_ascii=False, sort_keys=True), encoding='utf-8'
    )
    return destination


def _number(value, *, label: str):
    if type(value) is bool or type(value) not in (int, float):
        raise ValueError(f'{label} is not a number')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{label} is not a finite non-negative number')
    return number


def _integer(value, *, label: str):
    if type(value) is not int or value < 0:
        raise ValueError(f'{label} is not a non-negative integer')
    return value


def normalize_adapter_items(raw: bytes, *, max_output: int) -> dict[str, dict]:
    if len(raw) > max_output:
        raise ValueError(f'adapter output exceeds {max_output} bytes')
    try:
        data = loads(raw, label='adapter output')
    except StrictJSONError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, list) or not data:
        raise ValueError('adapter output must be a non-empty JSON list')
    result = {}
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f'adapter item {index} is not an object')
        name = item.get('name')
        if (not isinstance(name, str) or not name or name != name.strip()
                or len(name) > 512 or '\n' in name or '\x00' in name):
            raise ValueError(f'adapter item {index} has an invalid name')
        if name in result:
            raise ValueError(f'duplicate adapter item: {name}')
        all_value = item.get('all', item.get('total'))
        if 'all' in item and 'total' in item and item['all'] != item['total']:
            raise ValueError(f'conflicting all/total for {name}')
        has_result = 'result' in item
        if all_value is None and has_result:
            all_count = 1
        else:
            all_count = _integer(all_value, label=f'all for {name}')
            if all_count <= 0 or all_count > 1_000_000:
                raise ValueError(f'all count is out of range for {name}')
        passed = item.get('pass')
        if passed is None:
            if not has_result:
                raise ValueError(f'missing pass count for {name}')
            passed_count = int(_number(item['result'], label=f'result for {name}') > 0)
        else:
            passed_count = _integer(passed, label=f'pass for {name}')
            if passed_count > all_count:
                raise ValueError(f'pass count exceeds all for {name}')
        score = _number(item.get('score', passed_count), label=f'score for {name}')
        result[name] = {
            'name': name, 'all': all_count, 'pass': passed_count, 'score': score,
        }
        if has_result:
            result[name]['result'] = _number(item['result'], label=f'result for {name}')
        if 'baseline' in item:
            result[name]['baseline'] = _number(item['baseline'], label=f'baseline for {name}')
        if 'unit' in item:
            unit = item['unit']
            if not isinstance(unit, str) or len(unit) > 64:
                raise ValueError(f'unit is invalid for {name}')
            result[name]['unit'] = unit
    return result


def _adapter_environment() -> dict[str, str]:
    environment = {
        'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
        'LANG': 'C.UTF-8',
        'LC_ALL': 'C.UTF-8',
        'TZ': 'UTC',
        'PYTHONHASHSEED': '0',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    return environment


def run_adapter(criteria: Criteria, group: str, section_text: str,
                snapshot_root: Path) -> AdapterOutput:
    spec = criteria.adapter(group)
    if spec['kind'] != 'subprocess':
        raise InputError(f'group {group} is not a subprocess adapter')
    entry = snapshot_root / spec['entry']
    if not entry.is_file() or entry.is_symlink():
        return AdapterOutput({}, error=f'adapter entry is missing: {spec["entry"]}')
    if sha256_file(entry) != spec['sha256']:
        return AdapterOutput({}, error=f'adapter entry hash mismatch: {spec["entry"]}')

    # 环境已白名单；不能再加 -E，否则 PYTHONHASHSEED/PYTHONIOENCODING 会被忽略。
    command = [sys.executable, '-s', '-S', '-B', str(entry)]
    try:
        stdout, stderr, returncode, error = _run_bounded(
            command, section_text.encode('utf-8'), timeout=spec['timeout_seconds'],
            limit=spec['max_output_bytes'], cwd=snapshot_root,
            environment=_adapter_environment(),
        )
    except OSError as exc:
        return AdapterOutput({}, error=f'adapter failed to start: {exc}')
    stderr_text = stderr.decode('utf-8', 'replace')[:2000]
    if error:
        return AdapterOutput({}, error=error, stderr=stderr_text)
    if returncode != 0:
        detail = stderr_text.strip() or stdout.decode('utf-8', 'replace')[:500]
        return AdapterOutput({}, error=f'adapter exited {returncode}: {detail}',
                             stderr=stderr_text)
    try:
        items = normalize_adapter_items(stdout, max_output=spec['max_output_bytes'])
    except (ValueError, TypeError, OverflowError) as exc:
        return AdapterOutput({}, error=f'invalid adapter output: {exc}', stderr=stderr_text)
    return AdapterOutput(items, stderr=stderr_text)


def _run_bounded(command: list[str], input_bytes: bytes, *, timeout: int, limit: int,
                 cwd: Path, environment: dict[str, str]):
    """流式读取两个输出管道；超过总字节限额就终止整个进程组。"""
    process = subprocess.Popen(
        command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, start_new_session=True,
    )
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    lock = threading.Lock()
    state = {'total': 0, 'overflow': False, 'timeout': False}

    def terminate():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def reader(pipe, parts):
        try:
            while True:
                try:
                    chunk = pipe.read(65536)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                with lock:
                    if state['total'] + len(chunk) > limit:
                        state['overflow'] = True
                        terminate()
                        break
                    state['total'] += len(chunk)
                    parts.append(chunk)
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def writer():
        try:
            process.stdin.write(input_bytes)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    threads = [
        threading.Thread(target=reader, args=(process.stdout, stdout_parts), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, stderr_parts), daemon=True),
        threading.Thread(target=writer, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        state['timeout'] = True
        terminate()
        process.wait()
    for thread in threads:
        thread.join(timeout=5)
    for pipe in (process.stdout, process.stderr, process.stdin):
        try:
            pipe.close()
        except OSError:
            pass
    error = None
    if state['overflow']:
        error = 'adapter output exceeds limit'
    elif state['timeout']:
        error = f'adapter timed out after {timeout}s'
    return b''.join(stdout_parts), b''.join(stderr_parts), process.returncode, error
