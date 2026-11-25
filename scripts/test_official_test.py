import os
import signal
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

                with open(os.devnull, "wb") as screen, patch("official_test.os.write", side_effect=error):
                    with self.assertRaises(error):
                        run_serial(cmd, root, 3, screen)
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

    def test_return_code_survives_failed_cleanup(self):
        record = {}
        with tempfile.TemporaryDirectory() as tmp, open(os.devnull, "wb") as screen:
            with patch("official_test.reap", side_effect=OSError("cleanup failed")):
                with self.assertRaises(OSError):
                    run_serial([sys.executable, "-c", "raise SystemExit(7)"],
                               Path(tmp), 3, screen, record)
        self.assertEqual(record["qemu_exit"], 7)

    def test_sigkill_reaps_orphan(self):
        # The leader exits only after its helper is ready and ignores TERM.
        cmd = """import os, signal
r, w = os.pipe()
pid = os.fork()
if not pid:
    os.close(r)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(w, b'1')
    signal.pause()
else:
    os.close(w)
    os.read(r, 1)
    print(pid)
"""
        with child([sys.executable, "-c", cmd], stdout=subprocess.PIPE) as proc:
            with proc.stdout:
                helper = int(proc.stdout.readline())
                self.assertEqual(proc.wait(timeout=3), 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(helper, 0)

    def test_stalled_display_times_out(self):
        r, w = os.pipe()
        with os.fdopen(r, "rb"), os.fdopen(w, "wb") as screen, tempfile.TemporaryDirectory() as tmp:
            os.set_blocking(w, False)
            with self.assertRaises(BlockingIOError):
                while True:
                    os.write(w, b"x" * 4096)
            os.set_blocking(w, True)
            with self.assertRaises(subprocess.TimeoutExpired):
                run_serial([sys.executable, "-c", "print('boot')"], Path(tmp), 0.5, screen)
            self.assertTrue(os.get_blocking(w))
            self.assertEqual((Path(tmp) / "serial.log").read_bytes(), b"boot\n")

    def test_second_sigint_during_cleanup(self):
        from official_test import reap

        def interrupt_cleanup(proc):
            os.kill(os.getpid(), signal.SIGINT)
            reap(proc)

        with patch("official_test.reap", side_effect=interrupt_cleanup):
            with self.assertRaises(KeyboardInterrupt):
                with child([sys.executable, "-c", "import time; time.sleep(30)"]) as proc:
                    os.kill(os.getpid(), signal.SIGINT)
        self.assertIsNotNone(proc.returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(proc.pid, 0)

    def test_bad_image_records_utf8_reason_in_ascii_locale(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "image"
            image.write_bytes(b"invalid")
            out = root / "run"
            result = subprocess.run(
                [sys.executable, Path(__file__).with_name("official_test.py"),
                 "--image", image, "--out", out],
                env=os.environ | {"LC_ALL": "C", "PYTHONUTF8": "0"},
                capture_output=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            record = json.loads((out / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["exit"], 2)
            self.assertEqual(record["reason"], "镜像 SHA-256 不匹配")

    def test_closed_stderr_keeps_bad_image_record(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "bad.img"
            image.write_bytes(b"invalid")
            out = root / "out"
            reader, writer = os.pipe()
            os.close(reader)
            with os.fdopen(writer, "wb") as closed_pipe:
                result = subprocess.run(
                    [sys.executable, Path(__file__).with_name("official_test.py"),
                     "--image", image, "--out", out], stderr=closed_pipe)
            self.assertEqual(result.returncode, 2)
            record = json.loads((out / "run.json").read_bytes())
            self.assertEqual(record["exit"], 2)
            self.assertEqual(record["reason"], "镜像 SHA-256 不匹配")

    def test_parameter_errors_with_closed_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "image"
            image.touch()
            for args in ([], ["--image", image, "--out", root, "--timeout", "0"],
                         ["--image", root / "missing", "--out", root],
                         ["--image", image, "--out", root]):
                reader, writer = os.pipe()
                os.close(reader)
                with self.subTest(args=args), os.fdopen(writer, "wb") as sink:
                    result = subprocess.run(
                        [sys.executable, Path(__file__).with_name("official_test.py"), *args],
                        stderr=sink)
                    self.assertEqual(result.returncode, 2)

    @unittest.skipIf(os.geteuid() == 0, "root can read mode-000 files")
    def test_unreadable_image_is_input_error(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "image"
            image.touch(mode=0)
            out = root / "out"
            result = subprocess.run(
                [sys.executable, Path(__file__).with_name("official_test.py"),
                 "--image", image, "--out", out], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads((out / "run.json").read_bytes())["exit"], 2)
            self.assertFalse((out / "build.log").exists())
