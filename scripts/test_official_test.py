import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unittest.mock import patch

from official_test import child, run_serial


class SerialTests(unittest.TestCase):
    def test_raw_bytes_and_separate_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd = [sys.executable, "-c", "import os; os.write(1,b'\\xff\\r\\n'); os.write(2,b'error')"]
            with open(os.devnull, "wb") as screen:
                self.assertEqual(run_serial(cmd, root, 3, screen), 0)
            self.assertEqual((root / "serial.log").read_bytes(), b"\xff\r\n")
            self.assertEqual((root / "qemu.stderr").read_bytes(), b"error")

    def test_silent_child_times_out(self):
        with tempfile.TemporaryDirectory() as tmp, open(os.devnull, "wb") as screen:
            with self.assertRaises(subprocess.TimeoutExpired):
                run_serial([sys.executable, "-c", "import time; time.sleep(30)"],
                           Path(tmp), 0.2, screen)

    def test_interrupted_or_failed_output_reaps_vm(self):
        for error in (KeyboardInterrupt, OSError):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pidfile = root / "pid"
                cmd = [sys.executable, "-c",
                       "import os,sys,time; from pathlib import Path; "
                       "Path(sys.argv[1]).write_text(str(os.getpid())); "
                       "print('boot', flush=True); time.sleep(30)", str(pidfile)]

                class FailingOutput:
                    def write(self, data):
                        raise error()

                with self.assertRaises(error):
                    run_serial(cmd, root, 3, FailingOutput())
                self.assertEqual((root / "serial.log").read_bytes(), b"boot\n")
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)

    def test_short_disk_write_is_reported(self):
        # Linux can accept a prefix without raising until the next write.
        with tempfile.TemporaryDirectory() as tmp:
            probe = """
import os, resource, signal, sys
from pathlib import Path
from official_test import run_serial
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (8, 8))
with open(os.devnull, 'wb') as screen:
    try:
        run_serial([sys.executable, '-c', 'print("0123456789")'],
                   Path(sys.argv[1]), 3, screen)
    except OSError as error:
        import errno
        if error.errno == errno.EFBIG:
            sys.exit(0)
        raise
sys.exit(1)
"""
            result = subprocess.run([sys.executable, "-B", "-c", probe, tmp],
                                    cwd=Path(__file__).resolve().parent)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((Path(tmp) / "serial.log").read_bytes(), b"01234567")

    def test_cleanup_failure_keeps_both_reasons(self):
        for original, text in [(KeyboardInterrupt(), "用户中断"),
                               (subprocess.TimeoutExpired("qemu", 1), "timed out")]:
            with self.subTest(original=original), patch("official_test.reap",
                                                       side_effect=OSError("cleanup error")):
                with self.assertRaises(RuntimeError) as raised:
                    with child([sys.executable, "-c", "pass"]) as proc:
                        proc.wait()
                        raise original
                self.assertIn(text, str(raised.exception))
                self.assertIn("cleanup error", str(raised.exception))
