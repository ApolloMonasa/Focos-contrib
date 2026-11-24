"""构建 FocOS，挂载固定比赛镜像并记录 QEMU 原始串口。"""
import argparse
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
ELF = ROOT / "target/riscv64gc-unknown-none-elf/release/focos-kernel"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


@contextmanager
def child(cmd, **kwargs):
    proc = subprocess.Popen(cmd, cwd=ROOT, start_new_session=True,
                            stdin=subprocess.DEVNULL, **kwargs)
    try:
        yield proc
    finally:
        proc.poll()
        if group_exists(proc.pid):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                until = time.monotonic() + 5
                while group_exists(proc.pid) and time.monotonic() < until:
                    proc.poll()
                    time.sleep(0.05)
                if group_exists(proc.pid):
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("清理失败，子进程仍未退出") from exc
        if group_exists(proc.pid):
            raise RuntimeError("清理失败，进程组仍存在")


def run_serial(cmd, out, timeout, screen):
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return seconds

    with (out / "serial.log").open("xb", buffering=0) as log, (out / "qemu.stderr").open("xb") as err:
        with child(cmd, stdout=subprocess.PIPE, stderr=err) as proc:
            with proc.stdout, selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while selector.select(remaining()):
                    data = os.read(proc.stdout.fileno(), 65536)
                    if not data:
                        return proc.wait(timeout=remaining())
                    log.write(data)
                    screen.write(data)
                    screen.flush()
                raise subprocess.TimeoutExpired(cmd, timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    image = args.image.resolve()
    if args.timeout <= 0 or not image.is_file():
        parser.error("需要有效的镜像文件和正整数超时秒数")
    out = args.out.resolve()
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("输出目录已存在")
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1

    record = {"image": str(image), "timeout_seconds": args.timeout, "build": ["make", "build"]}
    code = 1
    try:
        record["image_sha256"] = digest(image)
        if record["image_sha256"] != IMAGE_SHA:
            raise ValueError("镜像 SHA-256 不匹配")
        with (out / "build.log").open("xb") as log:
            with child(record["build"], stdout=log, stderr=subprocess.STDOUT) as proc:
                if proc.wait():
                    raise RuntimeError("构建失败，见 build.log")
        record["kernel_sha256"] = digest(ELF)
        record["revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        firmware = Path("/usr/share/qemu/opensbi-riscv64-generic-fw_dynamic.bin")
        record["firmware_sha256"] = digest(firmware)
        cmd = ["qemu-system-riscv64", "-machine", "virt", "-kernel", str(ELF),
               "-m", "1G", "-smp", "1", "-display", "none", "-monitor", "none",
               "-serial", "stdio", "-bios", str(firmware), "-no-reboot",
               "-drive", "file=" + str(image).replace(",", ",,") + ",if=none,format=raw,id=x0,snapshot=on",
               "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
               "-device", "virtio-net-device,netdev=net", "-netdev", "user,id=net", "-rtc", "base=utc"]
        record["command"] = cmd
        record["qemu_version"] = subprocess.check_output([cmd[0], "--version"], text=True).splitlines()[0]
        rc = run_serial(cmd, out, args.timeout, sys.stdout.buffer)
        record["qemu_exit"] = rc
        record["reason"] = f"QEMU 进程退出 {rc}"
        code = int(rc != 0)
    except ValueError as exc:
        code, record["reason"] = 2, str(exc)
    except subprocess.TimeoutExpired as exc:
        code, record["reason"] = 124, str(exc)
    except KeyboardInterrupt:
        code, record["reason"] = 130, "用户中断"
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        record["reason"] = str(exc)
    print(record["reason"], file=sys.stderr)
    record["exit"] = code
    try:
        (out / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
