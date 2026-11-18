"""输入快照与独占输出目录；不跟随符号链接。"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path

from .errors import InputError, PublishError


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_regular_bytes(path: Path, *, label: str, max_bytes: int | None = None) -> bytes:
    """只读取普通文件；拒绝符号链接和特殊文件，并检查读取前后身份。"""
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise InputError(f'{label}: cannot open {path}: {exc}') from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InputError(f'{label}: not a regular file: {path}')
        if max_bytes is not None and before.st_size > max_bytes:
            raise InputError(f'{label}: file exceeds {max_bytes} bytes: {path}')
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise InputError(f'{label}: file exceeds {max_bytes} bytes: {path}')
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise InputError(f'{label}: file changed while reading: {path}')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


def _reject_symlink_ancestors(path: Path, *, label: str):
    for component in (path, *path.parents):
        try:
            if component.is_symlink():
                raise InputError(f'{label}: path contains a symlink: {component}')
        except OSError as exc:
            raise InputError(f'{label}: cannot inspect {component}: {exc}') from exc


def create_output_dir(path: Path, *, label: str = 'output') -> 'OutputDir':
    """独占地创建并立即持有目录句柄；后续发布不再按路径重新打开。"""
    path = Path(path).absolute()
    parent = path.parent
    name = path.name
    if not name or name in ('.', '..'):
        raise InputError(f'{label}: invalid output directory: {path}')
    _reject_symlink_ancestors(parent, label=label)
    try:
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            raise InputError(f'{label}: cannot open parent directory: {parent}: {exc}') from exc
        raise PublishError(f'{label}: cannot open parent directory: {parent}: {exc}') from exc
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise InputError(f'{label}: output already exists: {path}') from exc
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise InputError(f'{label}: cannot create output: {exc}') from exc
        except OSError as exc:
            raise PublishError(f'{label}: cannot create output: {exc}') from exc
        try:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise PublishError(f'{label}: cannot open created output: {exc}') from exc
    finally:
        os.close(parent_fd)
    return OutputDir(path, descriptor)


class OutputDir:
    """从创建到发布持有同一个目录句柄；路径被改名也不会写错目录。"""

    def __init__(self, path: Path, descriptor: int):
        self.path = path
        self._fd = descriptor

    def write(self, name: str, raw: bytes, *, mode: int = 0o600):
        if '/' in name or name in ('', '.', '..'):
            raise PublishError(f'invalid output member: {name!r}')
        try:
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode, dir_fd=self._fd,
            )
        except OSError as exc:
            raise PublishError(f'cannot create output member {name}: {exc}') from exc
        try:
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise PublishError(f'cannot write output member {name}')
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def sync(self):
        """把目录项变化刷到磁盘；失败视为发布失败。"""
        try:
            os.fsync(self._fd)
        except OSError as exc:
            raise PublishError(f'cannot sync output directory: {exc}') from exc

    def write_atomic(self, name: str, raw: bytes, *, mode: int = 0o600):
        """先写临时名，再原子改名；崩溃时不会留下同名完成文件。"""
        if '/' in name or name in ('', '.', '..'):
            raise PublishError(f'invalid output member: {name!r}')
        temporary = f'.{name}.tmp'
        self.write(temporary, raw, mode=mode)
        try:
            os.rename(temporary, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
        except OSError as exc:
            raise PublishError(f'cannot publish output member {name}: {exc}') from exc
        try:
            self.sync()
        except PublishError as exc:
            # 目录同步失败时不能把已可见的 report.json 留成完成标记。
            try:
                os.unlink(name, dir_fd=self._fd)
                self.sync()
            except OSError as cleanup:
                raise PublishError(
                    f'cannot publish output member {name}: {exc}; cleanup failed: {cleanup}'
                ) from exc
            raise

    def close(self):
        if getattr(self, '_fd', None) is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
