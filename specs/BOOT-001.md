# BOOT 001 裸机工程起点

状态：本次实现范围；2026-11-04。关联任务：仓库初始化。

FocOS 使用 Rust `no_std` / `no_main`。本规格只验收 QEMU RISC-V `virt` 单 hart 的启动链；VisionFive2 是首个真实硬件目标，其启动、UART 和存储驱动另行验收。

- 固定 Rust 1.99.0，目标 `riscv64gc-unknown-none-elf`，无第三方 Rust 依赖。
- 由 QEMU 自带 OpenSBI 进入 S 模式，内核链接地址为 `0x80200000`。
- 汇编入口建立 64 KiB 栈并清零 BSS，再进入 Rust；当前仅支持 `-smp 1`。
- 内核经 HAL 调用 SBI 早期控制台输出 `FOCOS_BOOT_OK`；该标记只代表启动成功。
- 通过 SBI System Reset 扩展请求正常关机。不能关机时停机等待，由测试超时判失败。
- 所有寄存器、汇编和 SBI 调用在 `arch/`，对内核提供窄 HAL 接口；不将 SBI 控制台称为已实现 UART 驱动。
- panic 输出 `FOCOS_PANIC` 后请求失败关机；不得继续输出成功标记。

验收：先运行 `python3 scripts/smoke.py` 确认缺少内核时失败，再实现；`make check` 必须通过格式、裸机 clippy、构建与 QEMU 冒烟。脚本检查 ELF 存在、QEMU 正常退出、成功标记恰好一次且无 panic；超时、缺少文件、标记缺失均失败。实际 QEMU 版本随日志保存。

本规格不承诺内存管理、中断、用户态、文件系统、官方测例通过或板上启动。后续每项功能须有独立 spec、失败测试和 PR。
