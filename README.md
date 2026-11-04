# FocOS

FocOS（File-system Optimized Core OS，读作 Focus）是 Rust `no_std` 裸机内核。真实硬件目标为 VisionFive2 / JH7110 和龙芯 2K1000LA，基础文件系统为 FAT32。

当前仅实现 QEMU RISC-V virt 单 hart 启动、SBI 早期控制台与关机；尚未实现或验证板上、内存管理、任务、用户态和文件系统。

安装 Rust/rustup、GNU make、Python 3 和 qemu-system-riscv64 后运行 `make check`。工具链固定为 Rust 1.99.0，日志保存在 target/evidence/boot-qemu-rv.log。本地 QEMU 10.2.2 已通过。

启动契约与验收见 [BOOT-001](specs/BOOT-001.md)。后续功能采用成员私有 fork、spec 先行、TDD 和 PR 合入。

AI 辅助：此初始化由 Codex 根据团队要求生成；先运行失败的冒烟检查，再实现并通过本地格式、clippy、构建、QEMU 验收。真实板卡仍需团队验证。
