"""检查器自身的负向测试，防止启动标记或失败输出被算成专项验收通过。"""
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import smoke


SUCCESS = "OpenSBI v1.3.1\nFOCOS_MM_OK\nFOCOS_IRQ_OK\nFOCOS_BOOT_OK\n"


class ResultTests(unittest.TestCase):
    """验证各合同的成功标记、顺序和退出状态；固件版本不作为通过条件。"""

    def test_accepts_complete_results(self):
        """启动、内存和中断合同都能从完整输出中取到自己的标记。"""
        for contract in smoke.CONTRACTS:
            with self.subTest(contract=contract):
                smoke.validate_result(SUCCESS, 0, contract)

    def test_boot_only_is_not_memory_or_irq_success(self):
        """旧内核仍能通过启动检查，但不能通过新增专项检查。"""
        output = "OpenSBI v1.3.1\nFOCOS_BOOT_OK\n"
        smoke.validate_result(output, 0, "boot")
        for contract in ("mm", "irq"):
            with self.subTest(contract=contract):
                with self.assertRaisesRegex(ValueError, "FOCOS_"):
                    smoke.validate_result(output, 0, contract)

    def test_rejects_missing_duplicate_or_embedded_markers(self):
        """缺失、重复或嵌在其他文字中的标记都不是有效成功记录。"""
        for contract, (_, markers) in smoke.CONTRACTS.items():
            for marker in markers:
                for replacement in ("", marker + "\n" + marker, "prefix " + marker):
                    with self.subTest(contract=contract, marker=marker,
                                      replacement=replacement):
                        with self.assertRaises(ValueError):
                            smoke.validate_result(
                                SUCCESS.replace(marker, replacement), 0, contract,
                            )

    def test_rejects_reversed_success_order(self):
        """专项自检尚未完成时不能提前报告启动成功。"""
        for contract in ("mm", "irq"):
            with self.subTest(contract=contract):
                _, markers = smoke.CONTRACTS[contract]
                reversed_output = (
                    "OpenSBI v1.3.1\n" + "\n".join(reversed(markers)) + "\n"
                )
                with self.assertRaisesRegex(ValueError, "out of order"):
                    smoke.validate_result(reversed_output, 0, contract)

    def test_rejects_panic_even_with_success_markers(self):
        """日志中出现 panic 时，即使已有成功标记也必须失败。"""
        with self.assertRaisesRegex(ValueError, "FOCOS_PANIC"):
            smoke.validate_result(SUCCESS + "FOCOS_PANIC\n", 0, "mm")

    def test_rejects_abnormal_exit(self):
        """正常标记不能掩盖非零退出状态或信号终止。"""
        for status in (1, 2, -9):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "status"):
                    smoke.validate_result(SUCCESS, status, "mm")

    def test_accepts_different_firmware_versions(self):
        """固件版本只写入日志，不代替内核布局、读写或中断状态验收。"""
        for banner in ("", "OpenSBI v1.3.1\n", "OpenSBI v1.6\n"):
            with self.subTest(banner=banner):
                smoke.validate_result(
                    banner + "FOCOS_MM_OK\nFOCOS_IRQ_OK\nFOCOS_BOOT_OK\n",
                    0,
                    "mm",
                )


class RunnerTests(unittest.TestCase):
    """验证检查入口对超时和不同 QEMU 版本的处理，不启动真实虚拟机。"""

    def setUp(self):
        """提供临时 ELF 占位文件，使各用例进入运行与判定路径。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        elf = pathlib.Path(directory.name) / "kernel"
        elf.write_bytes(b"test placeholder")
        self.elf = patch.object(smoke, "ELF", elf)
        self.elf.start()
        self.addCleanup(self.elf.stop)

    def test_timeout_fails_even_after_both_markers(self):
        """输出成功标记后仍不退出，也必须记录为超时失败。"""
        error = subprocess.TimeoutExpired("qemu", 20, output=SUCCESS.encode())
        with patch.object(smoke.subprocess, "check_output", return_value=
                          "QEMU emulator version 8.2.2\n"), \
                patch.object(smoke.subprocess, "run", side_effect=error), \
                patch.object(smoke, "write_log") as write_log:
            with self.assertRaisesRegex(SystemExit, "timed out"):
                smoke.run_check("mm")
            self.assertEqual(write_log.call_args.args[-2:], (SUCCESS, "TIMEOUT"))

    def test_newer_qemu_runs_and_is_logged(self):
        """新版 QEMU 仍执行行为验收，并把实际版本交给日志记录。"""
        output = "OpenSBI v1.6\nFOCOS_MM_OK\nFOCOS_BOOT_OK\n"
        completed = subprocess.CompletedProcess("qemu", 0, stdout=output)
        with patch.object(smoke.subprocess, "check_output", return_value=
                          "QEMU emulator version 9.0.0\n"), \
                patch.object(smoke.subprocess, "run", return_value=completed) as run, \
                patch.object(smoke, "write_log") as write_log, \
                patch("builtins.print") as report:
            smoke.run_check("mm")
            run.assert_called_once()
            self.assertEqual(write_log.call_args.args[1], "QEMU emulator version 9.0.0")
            self.assertEqual(write_log.call_args.args[-2:], (output, "PASS"))
            report.assert_called_once_with(
                "PASS: MM-002 (QEMU emulator version 9.0.0); "
                f"log: {smoke.MM_LOG}"
            )

    def test_irq_contract_uses_its_own_log(self):
        """中断专项通过时报告 ARCH-001，并写入独立日志路径。"""
        output = "OpenSBI v1.6\nFOCOS_IRQ_OK\nFOCOS_BOOT_OK\n"
        completed = subprocess.CompletedProcess("qemu", 0, stdout=output)
        with patch.object(smoke.subprocess, "check_output", return_value=
                          "QEMU emulator version 9.0.0\n"), \
                patch.object(smoke.subprocess, "run", return_value=completed), \
                patch.object(smoke, "write_log") as write_log, \
                patch("builtins.print") as report:
            smoke.run_check("irq")
            self.assertEqual(write_log.call_args.args[0], smoke.IRQ_LOG)
            self.assertEqual(write_log.call_args.args[-2:], (output, "PASS"))
            report.assert_called_once_with(
                "PASS: ARCH-001 (QEMU emulator version 9.0.0); "
                f"log: {smoke.IRQ_LOG}"
            )


if __name__ == "__main__":
    unittest.main()
