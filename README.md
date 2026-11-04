# FocOS

FocOS（File-system Optimized Core OS，读作 Focus）是面向操作系统设计赛的 Rust `no_std` 裸机内核。首个真实硬件平台为 VisionFive2 / JH7110（RV64GC），第二平台为龙芯 2K1000LA 星云板（LoongArch64）。基础文件系统固定为 FAT32。

研究主线：在受限内存下识别随机小 I/O 的存储瓶颈，通过可消融的缓存和写回策略改进文件访问，再用相同实现的跨平台实验与磁盘向量检索验证收益。

## 当前可运行内容

2026-11-04 的初始化版本仅实现 QEMU RISC-V `virt` 单 hart 启动、SBI 早期控制台和关机。它已经通过本地构建与冒烟检查。尚未验证 VisionFive2 / 龙芯上板，也尚未实现内存管理、任务、用户态、UART 驱动、块设备或 FAT32；官方功能测例当前无通过记录。

安装 Rust/rustup、GNU make、Python 3 和 `qemu-system-riscv64` 后，在仓库根目录运行：

```sh
make check
```

工具链固定为 Rust 1.99.0，目标由 `rust-toolchain.toml` 指定。`make build` 生成 `target/riscv64gc-unknown-none-elf/release/focos-kernel`，`make smoke` 将版本及串口日志保存到 `target/evidence/boot-qemu-rv.log`。本地验收 QEMU 为 10.2.2；CI 和比赛环境须分别记录版本，不能将本地环境当作往届官方环境。

## 项目入口

- [阶段目标与模块依赖](docs/roadmap.md)
- [12 个模块任务与认领入口](docs/backlog.md)
- [协作要求与认领流程](docs/collaboration.md)
- [评测分支选择和功能证据](docs/verification.md)
- [性能实验与图表要求](docs/benchmarks.md)
- [资料来源及规则边界](docs/references.md)
- [AI 辅助开发记录](docs/ai-usage.md)
- [启动规格 BOOT 001](specs/BOOT-001.md)

`kernel/` 存内核，`specs/` 存已审阅的契约，`scripts/` 存复现工具，`tests/` 存测试清单，`docs/` 存团队设计与证据说明。大型镜像和日志不进入 Git；`devdoc/` 为本地研究材料，默认忽略。

公有 upstream 为 [MindScrath/Focos](https://github.com/MindScrath/Focos)。日常开发采用成员公有 fork、短分支和 PR；`main` 已启用 CI 必过、至少一名非作者批准和禁止强推等保护。成员的账号、模块 owner 与 review 轮值待确认后绑定。团队报名个人资料不得进入此仓库。

区域赛赛方另有 GitLab 提交和来源披露要求，详见资料说明；此 GitHub 仓库不替代赛方提交仓库。代码许可证须在引入第三方代码前确定并核对兼容性，参赛提交按当届规则办理。
