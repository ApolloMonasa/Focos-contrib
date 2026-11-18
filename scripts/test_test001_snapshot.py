"""实现快照入口：实际分析代码必须来自已校验的私有副本。"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from test001.config import load_catalog, load_criteria, load_profile
from test001.errors import InputError


def load_launcher():
    spec = importlib.util.spec_from_file_location('test001_launcher',
                                                  ROOT / 'scripts/test001.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SnapshotTests(unittest.TestCase):
    """快照要包含实现与配置，并且修改源文件不能改变已建快照。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.launcher = load_launcher()

    def copy_repo(self):
        repo = self.base / 'repo'
        shutil.copytree(ROOT / 'scripts', repo / 'scripts')
        shutil.copytree(ROOT / 'tests/test001', repo / 'tests/test001')
        return repo

    def test_snapshot_contains_configuration_and_verifies(self):
        snapshot = self.launcher._prepare_snapshot(ROOT)
        self.addCleanup(shutil.rmtree, snapshot, True)
        self.assertTrue((snapshot / 'scripts/test001.py').is_file())
        self.assertTrue((snapshot / 'tests/test001/profiles/'
                         'regional-2025-with-ltp-v1.json').is_file())
        catalog = load_catalog(snapshot)
        profile = load_profile(snapshot, 'regional-2025-with-ltp-v1', catalog)
        criteria = load_criteria(snapshot, 'regional-2025-log-v1', catalog)
        self.assertEqual(criteria.criteria_id, 'regional-2025-log-v1')
        self.assertEqual(profile.profile_id, 'regional-2025-with-ltp-v1')

    def test_snapshot_is_not_affected_by_later_source_changes(self):
        repo = self.copy_repo()
        snapshot = self.launcher._prepare_snapshot(repo)
        self.addCleanup(shutil.rmtree, snapshot, True)
        target = snapshot / 'scripts/test001/ltp.py'
        before = target.read_bytes()
        (repo / 'scripts/test001/ltp.py').write_text(
            (repo / 'scripts/test001/ltp.py').read_text() + '\n# changed\n'
        )
        self.assertEqual(target.read_bytes(), before)
        # 快照仍对应复制时的实现身份，而不是改后的源文件。
        load_criteria(snapshot, 'regional-2025-log-v1')

    def test_source_change_before_snapshot_is_rejected(self):
        repo = self.copy_repo()
        source = repo / 'scripts/test001/ltp.py'
        source.write_text(source.read_text() + '\n# tampered\n')
        snapshot = self.launcher._prepare_snapshot(repo)
        self.addCleanup(shutil.rmtree, snapshot, True)
        with self.assertRaisesRegex(InputError, 'implementation file changed'):
            load_criteria(snapshot, 'regional-2025-log-v1')

    def test_symlink_in_implementation_is_rejected(self):
        repo = self.copy_repo()
        target = repo / 'scripts/test001/ltp.py'
        target.unlink()
        target.symlink_to(ROOT / 'scripts/test001/ltp.py')
        with self.assertRaises(OSError):
            self.launcher._prepare_snapshot(repo)

    def test_cli_reexecutes_from_snapshot(self):
        repo = self.copy_repo()
        output = self.base / 'report'
        log = ROOT / 'tests/test001/fixtures/historical/logs/ref_lua3.log'
        result = subprocess.run(
            [sys.executable, '-B', str(repo / 'scripts/test001.py'),
             'analyze', '--log', str(log), '--profile',
             'regional-2025-with-ltp-v1', '--criteria', 'regional-2025-log-v1',
             '--group', 'lua', '--libc', 'glibc', '--out', str(output)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(report['groups'][0]['counts']['pass'], 9)


if __name__ == '__main__':
    unittest.main()
