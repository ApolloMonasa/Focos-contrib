#!/usr/bin/env python3
"""TEST-001 入口：先复制并校验私有的实现快照，再从快照执行分析。"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

IMPLEMENTATION_PATHS = (
    'scripts/test001.py',
    'scripts/test001',
    'tests/test001/catalog.json',
    'tests/test001/profiles',
    'tests/test001/criteria',
)


def _copy_file(source: Path, destination: Path):
    if source.is_symlink() or not source.is_file():
        raise OSError(f'implementation member is not a regular file: {source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def _copy_tree(source: Path, destination: Path):
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if '__pycache__' in relative.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            raise OSError(f'implementation tree contains a symlink: {path}')
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        else:
            raise OSError(f'implementation tree contains a special file: {path}')


def _prepare_snapshot(repo_root: Path):
    """复制实际执行所需的实现/配置；复制不跟随符号链接。"""
    snapshot = Path(tempfile.mkdtemp(prefix='test001-impl-'))
    try:
        for relative in IMPLEMENTATION_PATHS:
            source = repo_root / relative
            target = snapshot / relative
            if source.is_dir():
                _copy_tree(source, target)
            elif source.is_file():
                _copy_file(source, target)
            else:
                raise OSError(f'implementation path is missing: {source}')
    except BaseException:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    return snapshot


def _child(argv):
    snapshot = _prepare_snapshot(Path(__file__).resolve().parents[1])
    environment = os.environ.copy()
    environment['TEST001_SNAPSHOT'] = '1'
    environment.pop('PYTHONPATH', None)
    try:
        completed = subprocess.run(
            [sys.executable, '-E', '-s', '-B',
             str(snapshot / 'scripts/test001.py'), *argv],
            env=environment,
        )
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)
    return completed.returncode if completed.returncode >= 0 else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if os.environ.get('TEST001_SNAPSHOT') == '1':
        from test001.cli import main as cli_main
        return cli_main(argv)
    try:
        return _child(argv)
    except OSError as exc:
        print(f'test001: cannot prepare verified implementation snapshot: {exc}',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
