"""核对冻结配置、实现清单和历史回放夹具的身份。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from test001.config import (actual_implementation_files, load_catalog,
                            load_criteria, load_profile)
from test001.errors import InputError
from test001.strictjson import StrictJSONError, loads


class ProfileTests(unittest.TestCase):
    """PROFILE 的清单、分母和 shard 覆盖必须自洽。"""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(ROOT)
        cls.profile = load_profile(ROOT, 'regional-2025-with-ltp-v1', cls.catalog)

    def test_invocation_total_and_ltp_counts(self):
        """原 11 组两套 libc 与 LTP 的实际展开项共同构成分母。"""
        self.assertEqual(len(self.profile.invocations), 6514)
        self.assertEqual(self.profile.data['invocations']['total_selected'], 6514)
        ltp = [row for row in self.profile.invocations if row['group'] == 'ltp']
        self.assertEqual(len(ltp), 5660)
        self.assertEqual(sum(1 for row in ltp if row['libc'] == 'musl'), 2820)
        self.assertEqual(sum(1 for row in ltp if row['libc'] == 'glibc'), 2840)

    def test_shards_cover_every_invocation_once(self):
        """每个 shard 单组单 libc，范围无重叠且恰好覆盖全部调用。"""
        self.assertEqual(len(self.profile.shards), 24)
        covered = []
        for shard in self.profile.shards:
            items = self.profile.items_for(shard)
            self.assertTrue(items)
            self.assertTrue(all(item['group'] == shard.group for item in items))
            self.assertTrue(all(item['libc'] == shard.libc for item in items))
            covered.extend(range(shard.start, shard.end))
        self.assertEqual(covered, list(range(len(self.profile.invocations))))

    def test_regional_plan_matches_frozen_profile(self):
        """范围计划里的身份和分母不能与 PROFILE 漂移。"""
        plan = json.loads((ROOT / 'tests/regional-plan.json').read_text())
        self.assertEqual(plan['profile']['hash'], self.profile.profile_hash)
        self.assertEqual(plan['case_count'], len(self.profile.invocations))
        by_libc = {
            libc: sum(1 for row in self.profile.invocations if row['libc'] == libc)
            for libc in self.profile.libcs
        }
        self.assertEqual(plan['case_count_by_libc'], by_libc)
        for libc in ('musl', 'glibc'):
            self.assertEqual(
                sum(group['case_count_by_libc'][libc] for group in plan['groups']),
                plan['case_count_by_libc'][libc],
            )
        self.assertEqual(sum(group['case_count'] for group in plan['groups']),
                         plan['case_count'])
        ltp = next(group for group in plan['groups'] if group['name'] == 'ltp')
        self.assertEqual(ltp['case_count'], 5660)

    def test_planned_duplicate_is_frozen(self):
        """busybox 镜像脚本计划重复的命令不能当成意外重试。"""
        planned = self.profile.data['groups']['busybox']['planned_occurrences']
        self.assertEqual(planned, {'busybox echo "bbbbbbb" >> test.txt': 2})

    def test_ltp_rows_freeze_binary_hash_and_path(self):
        """LTP 每一行都有镜像路径、长度、模式和 SHA256。"""
        for row in self.profile.invocations:
            if row['group'] != 'ltp':
                continue
            self.assertTrue(row['path'].startswith('ltp/testcases/bin/'))
            self.assertEqual(len(row['sha256']), 64)
            self.assertGreater(row['size'], 0)
            self.assertEqual(row['mode'], '0o755')

    def test_profile_hash_is_checked(self):
        """改动 PROFILE 字段而不更新身份会被拒绝。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tests/test001').mkdir(parents=True)
            shutil.copy(ROOT / 'tests/test001/catalog.json', root / 'tests/test001/catalog.json')
            shutil.copytree(ROOT / 'tests/test001/profiles', root / 'tests/test001/profiles')
            path = root / 'tests/test001/profiles/regional-2025-with-ltp-v1.json'
            data = json.loads(path.read_text())
            data['revision'] += 1
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
            with self.assertRaisesRegex(InputError, 'hash mismatch'):
                load_profile(root, 'regional-2025-with-ltp-v1')


class CriteriaTests(unittest.TestCase):
    """CRITERIA 的支持声明、适配器哈希和实现清单必须可核对。"""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(ROOT)
        cls.profile = load_profile(ROOT, 'regional-2025-with-ltp-v1', cls.catalog)
        cls.v1 = load_criteria(ROOT, 'regional-2025-log-v1', cls.catalog)
        cls.v2 = load_criteria(ROOT, 'regional-2025-log-v2', cls.catalog)

    def test_both_criteria_support_the_frozen_profile(self):
        """J1/J2 都必须精确支持同一 PROFILE 哈希。"""
        self.v1.check_profile(self.profile)
        self.v2.check_profile(self.profile)
        self.assertEqual(self.v1.data['mapping']['profile_hash'],
                         self.profile.profile_hash)
        self.assertNotEqual(self.v1.criteria_hash, self.v2.criteria_hash)

    def test_adapters_cover_all_groups(self):
        """适配器集合与 PROFILE 组集合完全一致，LTP 使用内建解析器。"""
        self.assertEqual(set(self.v1.data['adapters']), set(self.profile.groups))
        self.assertEqual(self.v1.data['adapters']['ltp']['kind'], 'builtin')
        self.assertFalse(self.v1.data['acceptance']['ltp_summary_required'])
        self.assertTrue(self.v2.data['acceptance']['ltp_summary_required'])

    def test_implementation_manifest_is_current(self):
        """改动分析器或适配器后必须重新生成清单和 CRITERIA 身份。"""
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/update_test001_manifest.py'), '--check'],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ManifestEnumerationTests(unittest.TestCase):
    """更新器和分析器共用枚举：缺失根、符号链接不能静默消失。"""

    def test_missing_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts/test001').mkdir(parents=True)
            with self.assertRaisesRegex(InputError, 'implementation root is missing'):
                actual_implementation_files(
                    root, ['scripts/test001', 'scripts/missing-tree']
                )

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'scripts/test001'
            directory.mkdir(parents=True)
            target = root / 'real.py'
            target.write_text('x')
            (directory / 'link.py').symlink_to(target)
            with self.assertRaisesRegex(InputError, 'symlink'):
                actual_implementation_files(root, ['scripts/test001'])

    def test_pycache_is_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'scripts/test001'
            (directory / '__pycache__').mkdir(parents=True)
            (directory / 'a.py').write_text('x')
            (directory / '__pycache__' / 'a.pyc').write_bytes(b'x')
            found = actual_implementation_files(root, ['scripts/test001'])
            self.assertEqual(set(found), {'scripts/test001/a.py'})


class StrictJSONTests(unittest.TestCase):
    """重复键、NaN 和非对象输入不能进入配置或报告。"""

    def test_rejects_duplicate_keys(self):
        with self.assertRaisesRegex(StrictJSONError, 'duplicate'):
            loads('{"a": 1, "a": 2}', label='test')

    def test_rejects_nonfinite_numbers(self):
        for raw in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(StrictJSONError, 'non-finite'):
                    loads(raw, label='test')

    def test_accepts_ordinary_object(self):
        self.assertEqual(loads('{"a": 1}', label='test'), {'a': 1})


class HistoryFixtureTests(unittest.TestCase):
    """历史副本去掉了邮箱；来源、原始哈希和夹具哈希都要对得上。"""

    def setUp(self):
        base = ROOT / 'tests/test001/fixtures/historical'
        self.index = json.loads((base / 'index.json').read_text())
        self.base = base

    def test_every_case_has_a_matching_log_and_expected_file(self):
        self.assertEqual(len(self.index['cases']), 15)
        for case in self.index['cases']:
            with self.subTest(sample=case['sample']):
                log = (self.base / case['log']).read_bytes()
                expected = (self.base / case['expected']).read_bytes()
                from test001.fsutil import sha256_bytes
                self.assertEqual(sha256_bytes(log), case['log_sha256'])
                self.assertEqual(sha256_bytes(expected), case['expected_sha256'])
                self.assertNotIn(b'giometti' + b'@' + b'linux.it', log)

    def test_redaction_records_original_and_fixture_hashes(self):
        self.assertEqual(len(self.index['redactions']), 7)
        for item in self.index['redactions']:
            self.assertTrue(item['original_sha256'])
            self.assertTrue(item['fixture_sha256'])
            self.assertNotEqual(item['original_sha256'], item['fixture_sha256'])
            self.assertIn('redacted.invalid', item['redaction'])


if __name__ == '__main__':
    unittest.main()
