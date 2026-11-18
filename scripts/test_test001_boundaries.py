"""适配器资源限额与输出发布边界；不启动 QEMU。"""
import errno
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from test001.adapters import run_adapter
from test001.errors import InputError, PublishError
from test001.fsutil import create_output_dir


class FakeCriteria:
    def __init__(self, spec):
        self.spec = spec

    def adapter(self, group):
        return self.spec


class AdapterLimitTests(unittest.TestCase):
    """输出限额必须在读取过程中执行，而不是先全部放进内存。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'adapters').mkdir()

    def adapter(self, script: str, *, limit=1024, timeout=5):
        path = self.root / 'adapters' / 'judge_fake.py'
        path.write_text(script)
        spec = {
            'kind': 'subprocess', 'entry': 'adapters/judge_fake.py',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'timeout_seconds': timeout, 'max_output_bytes': limit,
        }
        return run_adapter(FakeCriteria(spec), 'fake', '', self.root)

    def test_oversized_output_is_terminated(self):
        started = time.monotonic()
        result = self.adapter(
            "import sys\nsys.stdout.write('x' * 2000000)\n", limit=1024
        )
        self.assertIn('exceeds limit', result.error)
        self.assertLess(time.monotonic() - started, 5)

    def test_hash_seed_is_effective(self):
        script = (
            "import json, os, sys\n"
            "sys.stderr.write('seed=' + os.environ.get('PYTHONHASHSEED', '') + "
            "' random=' + str(sys.flags.hash_randomization) + '\\n')\n"
            "print(json.dumps([{'name': 'x', 'all': 1, 'pass': 1, 'score': 1}]))\n"
        )
        result = self.adapter(script)
        self.assertIsNone(result.error)
        self.assertIn('seed=0 random=0', result.stderr)

    def test_timeout_kills_process_group(self):
        result = self.adapter('import time\ntime.sleep(10)\n', timeout=1)
        self.assertIn('timed out', result.error)


class OutputBoundaryTests(unittest.TestCase):
    """mkdir 基础设施错误是发布失败；同步失败不能留下完成标记。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_mkdir_io_error_is_publish_failure(self):
        with mock.patch('test001.fsutil.os.mkdir',
                        side_effect=OSError(errno.EIO, 'I/O error')):
            with self.assertRaises(PublishError):
                create_output_dir(self.root / 'out')

    def test_existing_output_is_input_error(self):
        output = self.root / 'out'
        output.mkdir()
        with self.assertRaises(InputError):
            create_output_dir(output)

    def test_sync_failure_removes_completion_file(self):
        output = create_output_dir(self.root / 'out')
        self.addCleanup(output.close)
        with mock.patch.object(
                output, 'sync', side_effect=[PublishError('sync failed'), None],
        ):
            with self.assertRaises(PublishError):
                output.write_atomic('report.json', b'{}')
        self.assertFalse((output.path / 'report.json').exists())

    def test_complete_publish_leaves_no_temporary_file(self):
        output = create_output_dir(self.root / 'out')
        self.addCleanup(output.close)
        output.write_atomic('report.json', b'{}')
        self.assertEqual([path.name for path in output.path.iterdir()], ['report.json'])

    def test_intermediate_symlink_is_rejected(self):
        real = self.root / 'real' / 'sub'
        real.mkdir(parents=True)
        link = self.root / 'link'
        link.symlink_to(self.root / 'real', target_is_directory=True)
        with self.assertRaises(InputError):
            create_output_dir(link / 'sub' / 'out')
        self.assertFalse((real / 'out').exists())

    def test_ancestor_swap_before_component_open_cannot_redirect(self):
        base = self.root / 'base'
        (base / 'sub').mkdir(parents=True)
        evil = self.root / 'evil'
        (evil / 'sub').mkdir(parents=True)
        real_open = os.open
        swapped = False

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if not swapped and path == 'base' and dir_fd is not None:
                swapped = True
                base.rename(self.root / 'base-real')
                (self.root / 'base').symlink_to(evil, target_is_directory=True)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch('test001.fsutil.os.open', side_effect=fake_open):
            with self.assertRaises(InputError):
                create_output_dir(base / 'sub' / 'out')
        self.assertFalse((evil / 'sub' / 'out').exists())

    def test_created_handle_survives_parent_rename(self):
        parent = self.root / 'parent'
        parent.mkdir()
        output = create_output_dir(parent / 'out')
        self.addCleanup(output.close)
        parent.rename(self.root / 'renamed')
        output.write('marker', b'x')
        self.assertEqual((self.root / 'renamed' / 'out' / 'marker').read_bytes(), b'x')
        self.assertFalse((self.root / 'parent').exists())


if __name__ == '__main__':
    unittest.main()
