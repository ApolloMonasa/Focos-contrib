"""判据必须拒绝错误文本、非有限数值和冲突结果。"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

TEAM = Path(__file__).resolve().parents[1] / "judge_team"


def judge(name, text):
    proc = subprocess.run([sys.executable, str(TEAM / f"judge_{name}.py")],
                          input=text, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


class JudgeTests(unittest.TestCase):
    def test_lmbench_error_numbers_are_not_measurements(self):
        rows = judge("lmbench", "fork failed: errno 12\nexec failed: errno 2\nshell failed: errno 127\n")
        self.assertFalse(any(row["pass"] for row in rows))

    def test_lmbench_units_and_finite_positive_values(self):
        for value in ("-1", "0", "NaN", "Inf", "1e999"):
            with self.subTest(value=value):
                rows = judge("lmbench", f"Simple syscall: {value} microseconds\n")
                self.assertEqual(rows[0]["pass"], 0)
        self.assertEqual(judge("lmbench", "Simple syscall: 1.2 milliseconds\n")[0]["pass"], 0)
        self.assertEqual(judge("lmbench", "Simple syscall: 1.2 microseconds\n")[0]["pass"], 1)

    def test_lmbench_anonymous_values_need_command_context(self):
        rows = judge("lmbench", "0.524288 123.4\n0.524288 456.7\n96 12.4\n")
        self.assertFalse(any(row["pass"] for row in rows))
        text = "+ ./lmbench_all bw_file_rd -P 1 512k io_only /var/tmp/XXX\n0.524288 123.4\n"
        rows = judge("lmbench", text)
        self.assertEqual([r["pass"] for r in rows[19:23]], [1, 0, 0, 0])

    def test_libcbench_error_cannot_bridge_label_and_time(self):
        text = "b_malloc_sparse (0)\nmalloc failed: errno 12\ntime: 1.2, virt: 0, res: 0\n"
        self.assertEqual(judge("libcbench", text)[0]["pass"], 0)
        for value in ("-1", "NaN", "Inf", "1e999"):
            with self.subTest(value=value):
                rows = judge("libcbench", f"b_malloc_sparse (0)\ntime: {value}, virt: 0, res: 0\n")
                self.assertEqual(rows[0]["pass"], 0)

    def test_conflicting_or_duplicate_results_cannot_pass(self):
        examples = {
            "lmbench": "Simple syscall: 1.2 microseconds\nSimple syscall: 1.3 microseconds\n",
            "libcbench": "b_malloc_sparse (0)\ntime: 1.0, virt: 0, res: 0\nb_malloc_sparse (0)\ntime: 2.0, virt: 0, res: 0\n",
            "unixbench": "Unixbench DHRY2 test(lps): 1\nUnixbench DHRY2 test(lps): 2\n",
            "iperf": "====== iperf BASIC_UDP end: fail ======\n====== iperf BASIC_UDP end: success ======\n",
            "netperf": "====== netperf UDP_STREAM end: fail ======\n====== netperf UDP_STREAM end: success ======\n",
            "cyclictest": "====== cyclictest NO_STRESS_P1 end: fail ======\n====== cyclictest NO_STRESS_P1 end: success ======\n",
        }
        for name, text in examples.items():
            with self.subTest(name=name):
                self.assertEqual(judge(name, text)[0]["pass"], 0)

    def test_lmbench_full_source_formats_map_each_metric_once(self):
        labels = ['Simple syscall', 'Simple read', 'Simple write', 'Simple stat', 'Simple fstat',
                  'Simple open/close', "Select on 100 fd's", 'Signal handler installation',
                  'Signal handler overhead', 'Protection fault', 'Pipe latency',
                  'Process fork+exit', 'Process fork+execve', 'Process fork+/bin/sh -c']
        text = '\n'.join(f'{label}: 1.2 microseconds' for label in labels) + '\n'
        text += 'File /var/tmp/XXX write bandwidth: 123 KB/sec\nPagefaults on /var/tmp/XXX: 1.2 microseconds\n'
        text += '+ ./lmbench_all lat_mmap -P 1 512k /var/tmp/XXX\n0.524288 12\n'
        text += 'Pipe bandwidth: 123 MB/sec\nfile system latency\n0k 10 123 456\n10k 10 123 456\n'
        for args in ['bw_file_rd -P 1 512k io_only /var/tmp/XXX',
                     'bw_file_rd -P 1 512k open2close /var/tmp/XXX',
                     'bw_mmap_rd -P 1 512k mmap_only /var/tmp/XXX',
                     'bw_mmap_rd -P 1 512k open2close /var/tmp/XXX']:
            text += f'+ ./lmbench_all {args}\n0.524288 123\n'
        text += 'context switch overhead\n"size=32k ovr=0.25\n2 1.2\n4 1.3\n96 2.3\n'
        rows = judge('lmbench', text)
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(row['pass'] == 1 for row in rows))

    def test_invalid_measurement_cannot_be_overridden_by_valid_one(self):
        for value in ['-1', 'NaN', '1.2 milliseconds']:
            rows = judge('lmbench', f'Simple syscall: {value}\nSimple syscall: 1.2 microseconds\n')
            self.assertEqual(rows[0]['pass'], 0)

    def test_libcbench_trailing_error_or_duplicate_time_invalidates_case(self):
        prefix = 'b_malloc_sparse (0)\ntime: 1.0, virt: 1, res: 1\n'
        for tail in ['malloc failed: errno 12\n', 'time: 2.0, virt: 1, res: 1\n']:
            with self.subTest(tail=tail):
                self.assertEqual(judge('libcbench', prefix+tail)[0]['pass'], 0)
