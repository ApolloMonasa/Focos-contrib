"""适配器资源限额与输出发布边界；不启动 QEMU。"""
import errno
import hashlib
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
from test001.fsutil import OutputDir, create_output_dir


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
        writer = OutputDir(output)
        self.addCleanup(writer.close)
        with mock.patch.object(
                writer, 'sync', side_effect=[PublishError('sync failed'), None],
        ):
            with self.assertRaises(PublishError):
                writer.write_atomic('report.json', b'{}')
        self.assertFalse((output / 'report.json').exists())

    def test_complete_publish_leaves_no_temporary_file(self):
        output = create_output_dir(self.root / 'out')
        writer = OutputDir(output)
        self.addCleanup(writer.close)
        writer.write_atomic('report.json', b'{}')
        self.assertEqual([path.name for path in output.iterdir()], ['report.json'])


if __name__ == '__main__':
    unittest.main()
