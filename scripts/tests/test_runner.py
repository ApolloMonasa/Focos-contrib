#!/usr/bin/env python3
"""Negative and contract tests for scripts/run_tests.py.

Run from the repository root:
    python -m unittest discover -s scripts/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_tests.py"
INVENTORY = ROOT / "tests" / "inventory.json"
JUDGE_DIR = Path(os.environ.get("TEST001_JUDGE_DIR", ROOT / "third_party" / "oskernel-testsuits-cooperation" / "judge"))
TEAM_DIR = ROOT / "scripts" / "judge_team"

BASIC_OK = """#### OS COMP TEST GROUP START basic-musl ####
========== START test_brk ==========
Before alloc,heap pos: 0
After alloc,heap pos: 64
Alloc again,heap pos: 128
========== END test_brk ==========
========== START test_chdir ==========
chdir ret: 0
  current working dir : /test_chdir/
========== END test_chdir ==========
#### OS COMP TEST GROUP END basic-musl ####
"""

IPERF_OK = """#### OS COMP TEST GROUP START iperf-musl ####
====== iperf BASIC_UDP end: success ======
====== iperf BASIC_TCP end: fail ======
#### OS COMP TEST GROUP END iperf-musl ####
"""


def run(log_text: str, groups: list[str], extra: list[str] | None = None) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = tmp_path / "serial.log"
        log.write_text(log_text, encoding="utf-8")
        out = tmp_path / "out"
        cmd = [
            sys.executable,
            str(RUNNER),
            "--log",
            str(log),
            "--out",
            str(out),
            "--judge-dir",
            str(JUDGE_DIR),
            "--team-judge-dir",
            str(TEAM_DIR),
            "--inventory",
            str(INVENTORY),
            "--libc",
            "musl",
        ]
        for group in groups:
            cmd += ["--group", group]
        if extra:
            cmd += extra
        proc = subprocess.run(cmd, text=True, capture_output=True)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        return proc.returncode, summary


class RunnerContractTests(unittest.TestCase):
    def test_empty_log_marks_all_not_run(self):
        code, summary = run("", ["basic", "iperf"])
        self.assertEqual(code, 1)
        self.assertEqual([g["status"] for g in summary["groups"]], ["NOT_RUN", "NOT_RUN"])
        self.assertEqual(summary["totals"]["not_run"], 32 + 6)

    def test_missing_group_is_not_run_not_pass(self):
        code, summary = run(BASIC_OK, ["basic", "unixbench"])
        basic, unixbench = summary["groups"]
        self.assertEqual(basic["status"], "PARTIAL")
        self.assertEqual(unixbench["status"], "NOT_RUN")
        self.assertEqual(unixbench["subcases"][0]["status"], "NOT_RUN")
        self.assertEqual(code, 1)

    def test_start_without_end_is_timeout(self):
        text = "#### OS COMP TEST GROUP START lmbench-musl ####\nnull: 0.1\n"
        code, summary = run(text, ["lmbench"])
        self.assertEqual(summary["groups"][0]["status"], "TIMEOUT")
        self.assertEqual(code, 1)

    def test_unknown_subcase_is_reported(self):
        text = BASIC_OK.replace(
            "#### OS COMP TEST GROUP END basic-musl ####",
            "========== START test_unknown ==========\nx\n========== END test_unknown ==========\n"
            "#### OS COMP TEST GROUP END basic-musl ####",
        )
        _, summary = run(text, ["basic"])
        self.assertEqual(summary["groups"][0]["unexpected_in_log"], ["test_unknown"])

    def test_judge_crash_is_judge_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "judge_basic.py"
            bad.write_text("raise SystemExit(3)\n", encoding="utf-8")
            with tempfile.TemporaryDirectory() as out_tmp:
                log = Path(out_tmp) / "serial.log"
                log.write_text(BASIC_OK, encoding="utf-8")
                out = Path(out_tmp) / "out"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        "--log",
                        str(log),
                        "--out",
                        str(out),
                        "--judge-dir",
                        tmp,
                        "--team-judge-dir",
                        str(TEAM_DIR),
                        "--inventory",
                        str(INVENTORY),
                        "--libc",
                        "musl",
                        "--group",
                        "basic",
                    ],
                    text=True,
                    capture_output=True,
                )
                summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary["groups"][0]["status"], "JUDGE_ERROR")
                self.assertEqual(summary["groups"][0]["subcases"][0]["status"], "JUDGE_ERROR")
                self.assertEqual(proc.returncode, 1)

    def test_partial_group_is_not_pass(self):
        text = BASIC_OK.replace(
            "========== START test_brk ==========",
            "========== START test_brk ==========",
        ).replace("After alloc,heap pos: 64", "After alloc,heap pos: 65")
        _, summary = run(text, ["basic"])
        self.assertEqual(summary["groups"][0]["status"], "PARTIAL")
        self.assertEqual(summary["groups"][0]["subcases"][0]["status"], "PARTIAL")

    def test_iperf_team_judge(self):
        _, summary = run(IPERF_OK, ["iperf"])
        group = summary["groups"][0]
        self.assertEqual(group["status"], "PARTIAL")
        self.assertEqual(group["subcases"][0]["status"], "PASS")
        self.assertEqual(group["subcases"][1]["status"], "FAIL")

    def test_capture_timeout_without_output_is_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            cmd = f'"{sys.executable}" -c "import time; time.sleep(3)"'
            proc = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--capture-cmd",
                    cmd,
                    "--timeout",
                    "1",
                    "--out",
                    str(out),
                    "--judge-dir",
                    str(JUDGE_DIR),
                    "--team-judge-dir",
                    str(TEAM_DIR),
                    "--inventory",
                    str(INVENTORY),
                    "--libc",
                    "musl",
                    "--group",
                    "basic",
                ],
                text=True,
                capture_output=True,
            )
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["meta"]["capture_truncated"])
            self.assertEqual(summary["groups"][0]["status"], "TIMEOUT")
            self.assertEqual(summary["groups"][0]["subcases"][0]["status"], "TIMEOUT")
            self.assertEqual(proc.returncode, 1)

    def test_iozone_performance_mode(self):
        text = """#### OS COMP TEST GROUP START iozone-musl ####
iozone throughput write/read measurements
\tChildren see throughput for  4 initial writers \t=   13545.93 kB/sec
\tMax throughput per process \t\t\t=    3524.04 kB/sec
#### OS COMP TEST GROUP END iozone-musl ####
"""
        _, summary = run(text, ["iozone"])
        group = summary["groups"][0]
        self.assertEqual(group["subcases"][0]["status"], "PASS")



class RunnerSafetyTests(unittest.TestCase):
    def invoke(self, data=b"", extra=(), capture=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        log = root / 'input.log'
        log.write_bytes(data)
        out = root / 'out'
        cmd = [sys.executable, '-B', str(RUNNER), '--out', str(out),
               '--libc', 'musl', '--group', 'iperf', '--inventory', str(INVENTORY),
               '--team-judge-dir', str(TEAM_DIR)]
        cmd += ['--capture-cmd', capture] if capture else ['--log', str(log)]
        proc = subprocess.run(cmd + list(extra), capture_output=True, timeout=8)
        payload = json.loads((out / 'summary.json').read_text()) if (out / 'summary.json').exists() else None
        return proc, payload, out

    @staticmethod
    def complete():
        names = ['BASIC_UDP', 'BASIC_TCP', 'PARALLEL_UDP', 'PARALLEL_TCP', 'REVERSE_UDP', 'REVERSE_TCP']
        return ('#### OS COMP TEST GROUP START iperf-musl ####\n' +
                ''.join(f'====== iperf {n} end: success ======\n' for n in names) +
                '#### OS COMP TEST GROUP END iperf-musl ####\n').encode()

    def test_raw_bytes_are_preserved(self):
        raw = b'\xff\x00\r\n' + self.complete()
        proc, data, out = self.invoke(raw)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((out / 'serial.log').read_bytes(), raw)
        self.assertNotIn('score', data['totals'])
        self.assertIn('log_sha256', data['meta'])

    def test_environment_failure_invalidates_success(self):
        proc, data, _ = self.invoke(b'REF_FAIL network-dummy\n' + self.complete())
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(data['groups'][0]['status'], 'ENV_ERROR')

    def test_duplicate_nested_and_mismatched_sections_are_errors(self):
        valid = self.complete()
        malformed = [valid + valid,
                     valid.replace(b'END iperf-musl', b'END iperf-glibc'),
                     valid.replace(b'====== iperf BASIC_UDP', b'#### OS COMP TEST GROUP START lua-musl ####\n====== iperf BASIC_UDP')]
        for raw in malformed:
            with self.subTest(raw=raw):
                proc, data, _ = self.invoke(raw)
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(data['groups'][0]['status'], 'LOG_ERROR')

    def test_nonzero_capture_exit_overrides_complete_results(self):
        import shlex
        source = f'import sys;sys.stdout.buffer.write({self.complete()!r});sys.exit(7)'
        proc, data, _ = self.invoke(capture=shlex.join([sys.executable, '-c', source]))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(data['groups'][0]['status'], 'CAPTURE_ERROR')

    def test_malformed_judge_rows_return_judge_error(self):
        rows = [[{'name': 'BASIC_UDP', 'all': 0, 'pass': 0}],
                [{'name': 'BASIC_UDP', 'all': 1, 'pass': 2}],
                [{'name': 'BASIC_UDP', 'all': True, 'pass': 1}],
                [{'name': 'BASIC_UDP', 'all': 1, 'pass': 1, 'score': float('nan')}],
                [{'name': 'BASIC_UDP', 'all': 'oops', 'pass': 1}],
                [{'name': 'BASIC_UDP', 'all': 1, 'pass': 1}] * 2]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'judge_iperf.py'
            for items in rows:
                with self.subTest(items=items):
                    fake.write_text('import json\nprint(' + repr(json.dumps(items)) + ')\n')
                    proc, data, _ = self.invoke(self.complete(), ['--team-judge-dir', tmp])
                    self.assertEqual(proc.returncode, 1)
                    self.assertEqual(data['groups'][0]['status'], 'JUDGE_ERROR')

    def test_strict_reference_requires_complete_protocol(self):
        proc, data, _ = self.invoke(self.complete(), ['--reference', 'strict'])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(data['groups'][0]['status'], 'ENV_ERROR')

    def reference(self, rc=0):
        return (b'REF_PROTOCOL 1\nREF_MODULES_OK\nREF_MOUNT_OK\nREF_PREPARED musl\n'
                b'REF RUN musl iperf_testcode.sh\n' + self.complete() +
                f'REF RESULT musl iperf_testcode.sh {rc}\nREF_ALL_DONE\n'.encode())

    def test_valid_reference_protocol_and_test_exit_are_distinct(self):
        proc, data, _ = self.invoke(self.reference(), ['--reference', 'strict'])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(data['meta']['environment']['status'], 'VALID')
        proc, data, _ = self.invoke(self.reference(3), ['--reference', 'strict'])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(data['groups'][0]['status'], 'FAIL')
        self.assertEqual(data['totals']['pass'], 0)
        self.assertEqual(data['meta']['environment']['status'], 'VALID')

    def test_reference_order_missing_and_duplicate_markers_are_errors(self):
        good = self.reference()
        bad = [good.replace(b'REF_MODULES_OK\n', b''),
               good.replace(b'REF_ALL_DONE', b'REF_ALL_DONE\nREF_ALL_DONE'),
               good.replace(b'REF_PREPARED musl\n', b'REF_PREPARED musl\nREF_PREPARED musl\n'),
               good.replace(b'REF RESULT musl', b'REF RESULT glibc'),
               good.replace(b'REF RESULT musl iperf_testcode.sh 0\n', b''),
               good.replace(b'REF_MOUNT_OK', b'REF_MOUNT_OK\nREF_FAIL configuration')]
        for raw in bad:
            with self.subTest(raw=raw):
                proc, data, _ = self.invoke(raw, ['--reference', 'strict'])
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(data['groups'][0]['status'], 'ENV_ERROR')

    def test_legacy_reference_cannot_be_accepted_as_new_environment(self):
        raw = b'REF_MODULES_OK\nREF_MOUNT_OK\n' + self.complete() + b'REF_ALL_DONE\n'
        proc, data, _ = self.invoke(raw)
        self.assertEqual(data['groups'][0]['status'], 'ENV_ERROR')
        proc, data, _ = self.invoke(raw, ['--reference', 'legacy'])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(data['groups'][0]['status'], 'PASS')
        self.assertFalse(data['meta']['execution_valid'])

    def test_capture_timeout_terminates_descendants_even_after_success(self):
        import shlex
        import time
        import os
        with tempfile.TemporaryDirectory() as tmp:
            child_pid = Path(tmp)/'child.pid'
            source = ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                      f"open({str(child_pid)!r},'w').write(str(p.pid));"
                      f"sys.stdout.buffer.write({self.complete()!r});sys.stdout.flush();time.sleep(30)")
            started = time.monotonic()
            proc, data, _ = self.invoke(capture=shlex.join([sys.executable, '-c', source]), extra=['--timeout', '1'])
            self.assertLess(time.monotonic()-started, 4)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(data['groups'][0]['status'], 'TIMEOUT')
            self.assertEqual(data['totals']['pass'], 0)
            stat = Path('/proc')/child_pid.read_text()/'stat'
            if stat.exists():
                self.assertEqual(stat.read_text().split()[2], 'Z')


if __name__ == '__main__':
    unittest.main()
