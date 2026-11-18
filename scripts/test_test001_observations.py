"""原始观测守卫：适配器不能通过覆盖最后一次结果吞掉重复或未知项。"""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from test001.observations import (LMBENCH_COMMANDS, check_observations,
                                  extract_metrics, observed_names)


class DuplicateObservationTests(unittest.TestCase):
    """同一名称出现多次必须可见；计划重复由 PROFILE 单独计数。"""

    def test_lua_fail_then_success_is_duplicate_and_failure(self):
        text = (
            'testcase lua date.lua fail\n'
            'testcase lua date.lua success\n'
        )
        guard = check_observations('lua', text, ['lua date.lua'])
        self.assertEqual(guard.counts['lua date.lua'], 2)
        self.assertEqual(guard.duplicates, ['lua date.lua'])
        self.assertIn('lua date.lua', guard.failures)

    def test_lua_trailing_diagnostic_failure_is_not_lost(self):
        text = (
            'testcase lua date.lua fail trailing-diagnostic\n'
            'testcase lua date.lua success\n'
        )
        guard = check_observations('lua', text, ['lua date.lua'])
        self.assertEqual(guard.counts['lua date.lua'], 2)
        self.assertEqual(guard.duplicates, ['lua date.lua'])
        self.assertIn('lua date.lua', guard.failures)
        self.assertEqual(guard.statuses['lua date.lua'], ['fail', 'success'])

    def test_lua_malformed_testcase_is_flagged(self):
        text = 'testcase lua date.lua unknown\n'
        guard = check_observations('lua', text, ['lua date.lua'])
        self.assertEqual(guard.malformed, ['testcase lua date.lua unknown'])
        self.assertEqual(guard.duplicates, [])

    def test_lua_ambiguous_statuses_are_flagged(self):
        text = 'testcase lua date.lua fail success\n'
        guard = check_observations('lua', text, ['lua date.lua'])
        self.assertEqual(guard.malformed, ['testcase lua date.lua fail success'])

    def test_busybox_unknown_testcase_is_extra(self):
        text = 'testcase busybox surprise success\n'
        guard = check_observations('busybox', text, ['busybox date'])
        self.assertEqual(guard.extras, ['busybox surprise'])
        self.assertEqual(guard.counts['busybox surprise'], 1)

    def test_libctest_names_are_normalized(self):
        text = (
            '========== START entry-static.exe argv ==========\nPass!\n'
            '========== END entry-static.exe argv ==========\n'
            '========== START entry-dynamic.exe tls ==========\nPass!\n'
            '========== END entry-dynamic.exe tls ==========\n'
        )
        self.assertEqual(
            observed_names('libctest', text),
            ['libctest static argv', 'libctest dynamic tls'],
        )

    def test_libctest_missing_end_is_preserved_and_malformed(self):
        text = (
            '========== START entry-static.exe argv ==========\n'
            'Pass!\n'
        )
        guard = check_observations('libctest', text, ['libctest static argv'])
        self.assertEqual(guard.counts['libctest static argv'], 1)
        self.assertTrue(any('unclosed START' in line for line in guard.malformed))

    def test_basic_missing_end_is_preserved_and_malformed(self):
        text = (
            '========== START test_brk ==========\n'
            'Pass!\n'
        )
        guard = check_observations('basic', text, ['test_brk'])
        self.assertEqual(guard.counts['test_brk'], 1)
        self.assertTrue(any('unclosed START' in line for line in guard.malformed))

    def test_libctest_end_mismatch_is_malformed(self):
        text = (
            '========== START entry-static.exe argv ==========\n'
            'Pass!\n'
            '========== END entry-static.exe basename ==========\n'
        )
        guard = check_observations('libctest', text, ['libctest static argv'])
        self.assertEqual(guard.counts['libctest static argv'], 1)
        self.assertTrue(any('does not match' in line for line in guard.malformed))

    def test_basic_duplicate_start_is_visible(self):
        text = (
            '========== START test_brk ==========\n'
            '========== END test_brk ==========\n'
            '========== START test_brk ==========\n'
            '========== END test_brk ==========\n'
        )
        guard = check_observations('basic', text, ['test_brk'])
        self.assertEqual(guard.duplicates, ['test_brk'])


class PerformanceMetricTests(unittest.TestCase):
    """性能原值/单位必须能从原始段保留，adapter 的折叠不影响报告。"""

    def test_libcbench_time_is_preserved(self):
        text = (
            'b_malloc_sparse (0)\n'
            '  time: 0.061429400, virt: 38992, res: 38980, dirty: 38980\n'
        )
        metrics = extract_metrics('libcbench', text, ['b_malloc_sparse (0)'])
        self.assertEqual(len(metrics), 1)
        self.assertAlmostEqual(metrics[0]['value'], 0.0614294)
        self.assertEqual(metrics[0]['unit'], 's')
        self.assertEqual(metrics[0]['name'], 'b_malloc_sparse (0)')

    def test_lmbench_value_and_unit_are_preserved(self):
        metrics = extract_metrics(
            'lmbench', 'Simple syscall: 0.5762 microseconds\n', []
        )
        self.assertEqual(len(metrics), 1)
        self.assertAlmostEqual(metrics[0]['value'], 0.5762)
        self.assertEqual(metrics[0]['unit'], 'microseconds')
        self.assertEqual(metrics[0]['label'], 'Simple syscall')

    def test_lmbench_anonymous_metrics_keep_trace_identity(self):
        text = ('+ ./lmbench_all bw_file_rd -P 1 512k io_only /var/tmp/XXX\n'
                '0.524288 123.45\n'
                '+ ./lmbench_all lat_mmap -P 1 512k /var/tmp/XXX\n'
                '0.524288 6.75\n')
        names = [f'item-{i}' for i in range(24)]
        metrics = extract_metrics('lmbench', text, names)
        self.assertEqual([m['name'] for m in metrics], ['item-19', 'item-16'])
        self.assertEqual([m['value'] for m in metrics], [123.45, 6.75])
        self.assertEqual([m['unit'] for m in metrics], ['MB/sec', 'microseconds'])
        self.assertEqual(metrics[0]['raw'], '0.524288 123.45')

    def test_lmbench_tables_keep_all_measurements(self):
        text = ('file system latency\n10k 100 200 300\n'
                'context switch overhead\n2 1.25\n96 2.5\n')
        metrics = extract_metrics('lmbench', text, [f'item-{i}' for i in range(24)])
        self.assertEqual([m['value'] for m in metrics], [200, 300, 1.25, 2.5])
        self.assertEqual(metrics[0]['files'], 100)
        self.assertEqual(metrics[-1]['processes'], 96)

    def test_lmbench_unbound_numbers_are_not_assigned(self):
        self.assertEqual(extract_metrics('lmbench', '0.524288 123.45\n', []), [])


class FrozenTableTests(unittest.TestCase):
    """lmbench 守卫命令表必须与 CRITERIA 适配器的表完全一致。"""

    def test_lmbench_command_table_matches_adapter(self):
        path = ROOT / 'tests/test001/criteria/v1/adapters/team/lmbench_trace.py'
        spec = importlib.util.spec_from_file_location('frozen_lmbench_trace', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.COMMANDS, LMBENCH_COMMANDS)


class UnknownMetricTests(unittest.TestCase):
    """团队判据只遍历冻结名称时，守卫仍要从原始日志补出未知项。"""

    def test_marker_unknown_name_is_extra(self):
        text = '===== iperf surprise end: success =====\n'
        guard = check_observations('iperf', text, ['normal'])
        self.assertEqual(guard.extras, ['surprise'])

    def test_iozone_unknown_metric_is_extra(self):
        text = (
            'iozone throughput write/read measurements\n'
            'Children see throughput for surprise writers\n'
            'Max throughput per process = 123.4\n'
        )
        guard = check_observations(
            'iozone', text, ['iozone write/read 4 initial writers (kb/sec)']
        )
        self.assertEqual(
            guard.extras, ['iozone write/read surprise writers (kb/sec)']
        )

    def test_unixbench_unknown_name_is_extra(self):
        text = 'Unixbench surprise: 123.4\n'
        guard = check_observations('unixbench', text, ['Unixbench known'])
        self.assertEqual(guard.extras, ['Unixbench surprise'])

    def test_lmbench_known_trace_maps_to_expected_name(self):
        expected = ['lmbench syscall'] + [f'lmbench {index}' for index in range(1, 24)]
        guard = check_observations(
            'lmbench', '+ ./lmbench_all lat_syscall -P 1 null\n', expected
        )
        self.assertEqual(guard.counts['lmbench syscall'], 1)
        self.assertEqual(guard.extras, [])

    def test_lmbench_unknown_trace_is_extra(self):
        guard = check_observations(
            'lmbench', '+ ./lmbench_all surprise command\n', ['lmbench syscall']
        )
        self.assertEqual(guard.extras, ['lmbench surprise command'])

    def test_libcbench_unknown_label_is_extra(self):
        text = 'b_surprise (0)\n  time: 0.1, virt: 1, res: 1\n'
        guard = check_observations('libcbench', text, ['b_known (0)'])
        self.assertEqual(guard.extras, ['b_surprise (0)'])


if __name__ == '__main__':
    unittest.main()
