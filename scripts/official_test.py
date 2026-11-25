"""构建 FocOS，挂载固定比赛镜像并记录 QEMU 原始串口。"""
import argparse
import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SHA = "95973543db6b84a9a5e70f30da466ce292867aff5b689fb14c88dc9406e378b8"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def copy_image(image, dest):
    try:
        source = image.open("rb")
    except OSError as exc:
        raise ValueError(f"无法读取镜像 {image}：{exc}") from exc
    with source, dest.open("xb") as target:
        while True:
            try:
                data = source.read(1024 * 1024)
            except OSError as exc:
                raise ValueError(f"无法读取镜像 {image}：{exc}") from exc
            if not data:
                break
            if data.count(0) == len(data):
                target.seek(len(data), os.SEEK_CUR)
            else:
                target.write(data)
        target.truncate()
    dest.chmod(0o400)


class Terminated(Exception):
    pass


def interrupt(signum, frame):
    if signum == signal.SIGTERM:
        raise Terminated("收到 SIGTERM")
    raise KeyboardInterrupt


def group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


@contextmanager
def child(cmd, **kwargs):
    # Linux reparents orphaned grandchildren to this runner, so waitpid can
    # release their process table entries even if the container init does not.
    pr_set_child_subreaper = 36
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(pr_set_child_subreaper, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    pending_signal = None
    running = False

    def handle(signum, frame):
        nonlocal pending_signal
        if running:
            interrupt(signum, frame)
        pending_signal = signum

    previous = {sig: signal.signal(sig, handle) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, start_new_session=True,
                                stdin=subprocess.DEVNULL, **kwargs)
        try:
            running = True
            if pending_signal:
                interrupt(pending_signal, None)
            yield proc
        finally:
            running = False
            original = sys.exc_info()[1]
            try:
                reap(proc)
            except (RuntimeError, OSError) as exc:
                if original is None:
                    raise
                reason = "用户中断" if isinstance(original, KeyboardInterrupt) else str(original)
                raise RuntimeError(f"{reason}；清理失败：{exc}") from exc
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if pending_signal:
        interrupt(pending_signal, None)


def reap(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        proc.poll()
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        until = time.monotonic() + 5
        while time.monotonic() < until:
            # Let Popen collect its own leader and retain the exit status.
            if proc.poll() is not None:
                try:
                    while os.waitpid(-proc.pid, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
            if not group_exists(proc.pid):
                return
            time.sleep(0.05)
    raise RuntimeError("清理失败，进程组仍存在")


@contextmanager
def display_writer(screen, remaining):
    fd = screen.fileno()
    was_blocking = os.get_blocking(fd)
    # poll also accepts terminals, regular files and /dev/null.
    with selectors.PollSelector() as writable:
        writable.register(fd, selectors.EVENT_WRITE)

        def write(data):
            pending = memoryview(data)
            while pending:
                if writable.select(remaining()):
                    try:
                        count = os.write(fd, pending)
                    except BlockingIOError:
                        continue
                    if not count:
                        raise OSError("显示输出写入停滞")
                    pending = pending[count:]

        try:
            os.set_blocking(fd, False)
            yield write
        finally:
            os.set_blocking(fd, was_blocking)


def run_serial(cmd, out, timeout, screen, record=None):
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return seconds

    with (out / "serial.log").open("xb", buffering=0) as log, (out / "qemu.stderr").open("xb") as err:
        with child(cmd, stdout=subprocess.PIPE, stderr=err) as proc:
            with proc.stdout, selectors.DefaultSelector() as selector, display_writer(screen, remaining) as display:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while selector.select(remaining()):
                    data = os.read(proc.stdout.fileno(), 65536)
                    if not data:
                        rc = proc.wait(timeout=remaining())
                        if record is not None:
                            record["qemu_exit"] = rc
                        return rc
                    pending = memoryview(data)
                    while pending:
                        written = log.write(pending)
                        if not written:
                            raise OSError("串口日志写入停滞")
                        pending = pending[written:]
                    display(data)
                raise subprocess.TimeoutExpired(cmd, timeout)


def report(reason):
    # stderr is optional; a buffered retry at interpreter shutdown can
    # otherwise replace the runner's exit code when the reader has gone.
    try:
        os.write(2, (str(reason) + "\n").encode("utf-8", "backslashreplace"))
    except OSError:
        pass


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        report(f"{self.format_usage().rstrip()}\n{self.prog}: error: {message}")
        self.exit(2)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    try:
        image = args.image.resolve(strict=True)
    except (RuntimeError, OSError) as exc:
        parser.error(f"无法解析镜像路径：{exc}")
    if args.timeout <= 0 or not image.is_file():
        parser.error("需要有效的镜像文件和正整数超时秒数")
    out = args.out.resolve()
    try:
        out.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("输出目录已存在")
    except OSError as exc:
        report(exc)
        return 1

    record = {"image": str(image), "timeout_seconds": args.timeout, "build": ["make", "build"]}
    code = 1
    try:
        private_image = out / "sdcard-rv.img"
        copy_image(image, private_image)
        record["image_sha256"] = digest(private_image)
        if record["image_sha256"] != IMAGE_SHA:
            raise ValueError("镜像 SHA-256 不匹配")
        record["revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        record["dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT))
        with (out / "build.log").open("xb") as log:
            target_dir = out / "cargo-target"
            kernel = target_dir / "riscv64gc-unknown-none-elf/release/focos-kernel"
            compiler = os.environ.get("RUSTC") or os.environ.get("CARGO_BUILD_RUSTC") or "rustc"
            record["build_env"] = {"CARGO_TARGET_DIR": str(target_dir), "RUSTC": compiler}
            for key, value in os.environ.items():
                if key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS") or (
                    key.startswith("CARGO_TARGET_") and key.endswith("_RUSTFLAGS")
                ):
                    record["build_env"][key] = value
            build_env = os.environ | record["build_env"]
            for tool, executable in (("rustc", compiler), ("cargo", "cargo")):
                record[f"{tool}_version"] = subprocess.check_output(
                    [executable, "--version", "--verbose"], cwd=ROOT, env=build_env,
                    text=True).strip()
            with child(record["build"], env=build_env,
                       stdout=log, stderr=subprocess.STDOUT) as proc:
                if proc.wait():
                    raise RuntimeError("构建失败，见 build.log")
        record["kernel_sha256"] = digest(kernel)
        firmware = Path("/usr/share/qemu/opensbi-riscv64-generic-fw_dynamic.bin")
        record["firmware_sha256"] = digest(firmware)
        cmd = ["qemu-system-riscv64", "-machine", "virt", "-kernel", str(kernel),
               "-m", "1G", "-smp", "1", "-display", "none", "-monitor", "none",
               "-serial", "stdio", "-bios", str(firmware), "-no-reboot",
               "-drive", "file=" + str(private_image).replace(",", ",,") + ",if=none,format=raw,id=x0,snapshot=on",
               "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
               "-device", "virtio-net-device,netdev=net", "-netdev", "user,id=net", "-rtc", "base=utc"]
        record["command"] = cmd
        record["qemu_version"] = subprocess.check_output([cmd[0], "--version"], text=True).splitlines()[0]
        rc = run_serial(cmd, out, args.timeout, sys.stdout.buffer, record)
        record["reason"] = f"QEMU 进程退出 {rc}"
        code = int(rc != 0)
    except ValueError as exc:
        code, record["reason"] = 2, str(exc)
    except subprocess.TimeoutExpired as exc:
        code, record["reason"] = 124, str(exc)
    except Terminated as exc:
        code, record["reason"] = 143, str(exc)
    except KeyboardInterrupt:
        code, record["reason"] = 130, "用户中断"
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        record["reason"] = str(exc)
        if "qemu_exit" in record:
            record["reason"] = f"QEMU 进程退出 {record['qemu_exit']}；清理失败：{exc}"
    record["exit"] = code
    try:
        (out / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        report(exc)
        return 1
    report(record["reason"])
    return code


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    sys.exit(main())
