# BOOT-001：在 QEMU 启动并关机

状态：已实现，有启动检查。范围：RISC-V `virt` 单 hart；不含真板驱动。

## 要做到什么

OpenSBI 进入内核后，输出一次 `FOCOS_BOOT_OK`，正常关机。

## 行为约定

- Rust 1.99.0，`no_std`，目标 `riscv64gc-unknown-none-elf`。
- S 模式入口位于 `0x80200000`；先建 64 KiB 栈、清零 BSS，再进入 Rust。
- `arch/` 封装汇编和 SBI，内核经 HAL 输出和关机；SBI 输出不算自写 UART。
- panic 输出 `FOCOS_PANIC`，请求失败关机，不继续报告成功。
- 关机不可用时停机等待，由检查脚本判超时失败。

## 怎样验收

| 场景 | 操作 | 预期 |
| --- | --- | --- |
| 缺少内核 | 构建前执行 `python3 scripts/smoke.py` | 因 ELF 缺失失败 |
| 正常启动 | `make check` | 格式、clippy、构建通过；输出 `PASS: BOOT-001` |
| 启动异常 | smoke 检查退出状态、超时和标记 | 缺标记、重复标记、panic 或异常退出均失败 |

日志：`target/evidence/boot-qemu-rv.log`，含 QEMU 版本。首次失败与通过记录见 [AI 辅助记录](../docs/ai-usage.md)。
