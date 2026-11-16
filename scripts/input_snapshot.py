"""一次执行的文件快照：哈希按 inode 去重，路径绑定独立监视。"""
from pathlib import Path
import stat


class InputSnapshot:
    def __init__(self, hash_file):
        self.hash_file = hash_file
        self.states, self.expected, self.cache = {}, {}, {}
        self.quick_content, self.mismatches = set(), set()

    @staticmethod
    def state(path):
        try:
            resolved = path.resolve()
            info = path.stat()
            return resolved, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                              info.st_ctime_ns, info.st_mode)
        except (OSError, RuntimeError):
            return None

    def digest(self, path):
        path = Path(path).absolute()
        state = self.state(path)
        if state is None or not stat.S_ISREG(state[1][-1]):
            raise ValueError('input is not a regular file: '+str(path))
        resolved, fingerprint = state
        for alias in (path, resolved):
            if alias in self.states and self.states[alias] != state:
                raise ValueError('input changed during snapshot: '+str(alias))
        key = fingerprint[:2]
        if key in self.cache:
            previous, sha = self.cache[key]
            if fingerprint != previous:
                raise ValueError('input changed during snapshot: '+str(path))
        else:
            sha = self.hash_file(resolved)
            if self.state(path) != state or self.state(resolved) != state:
                raise ValueError('input changed while hashing: '+str(path))
            self.cache[key] = fingerprint, sha
        for alias in (path, resolved):
            self.states[alias] = state
            self.expected.setdefault(alias, sha)
        return sha

    def watch(self, path, expected, *, bulk=False):
        path = Path(path).absolute()
        state = self.state(path)
        actual = self.digest(path) if state is not None else None
        aliases = {path, state[0]} if state else {path}
        for alias in aliases:
            if actual != expected:
                self.mismatches.add(str(alias))
            self.states.setdefault(alias, state)
            self.expected[alias] = expected
            if not bulk:
                self.quick_content.add(alias)

    def changes(self, *, full=False):
        changed, hashes = set(self.mismatches), {}
        for path, original in self.states.items():
            current = self.state(path)
            if current != original:
                changed.add(str(path))
                continue
            if current is None:
                continue
            if full or path in self.quick_content:
                resolved, fingerprint = current
                key = fingerprint[:2]
                if key not in hashes:
                    try:
                        hashes[key] = self.hash_file(resolved)
                    except OSError:
                        hashes[key] = None
                if hashes[key] != self.expected[path] or self.state(path) != original:
                    changed.add(str(path))
        return sorted(changed)
