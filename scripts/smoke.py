#!/usr/bin/env python3
"""启动规格 BOOT-001 的集成验收，不代表 OS 功能或官方测例通过。"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ELF = ROOT / "target/riscv64gc-unknown-none-elf/release/focos-kernel"
LOG = ROOT / "target/evidence/boot-qemu-rv.log"


def main():
    if not ELF.is_file():
        sys.exit("FAIL: kernel ELF missing; run make build")
    command = [
        "qemu-system-riscv64", "-machine", "virt", "-bios", "default",
        "-m", "128M", "-smp", "1", "-nographic", "-no-reboot",
        "-kernel", str(ELF),
    ]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output(
        [command[0], "--version"], text=True
    ).splitlines()[0]
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=20, check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        LOG.write_text(version + "\n" + output)
        sys.exit(f"FAIL: boot timed out; see {LOG}")
    LOG.write_text(version + "\n" + result.stdout)
    if (result.returncode != 0 or
            result.stdout.count("FOCOS_BOOT_OK") != 1 or
            "FOCOS_PANIC" in result.stdout):
        sys.exit(f"FAIL: boot contract violated; see {LOG}")
    print(f"PASS: BOOT-001 ({version}); log: {LOG}")


if __name__ == "__main__":
    main()
