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


if __name__ == "__main__":
    unittest.main()
