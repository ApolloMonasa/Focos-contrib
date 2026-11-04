# FocOS

我们要做一个能在 VisionFive2 上运行、能读写 FAT32 文件的 Rust 操作系统，再用实验说明文件缓存为什么能提速。项目名读作 Focus。

**第一次加入：先按下面步骤跑通，再认领一个小任务。不用先读完所有设计文档。**

## 现在做到哪里了？

已完成：QEMU 中启动一个 RISC-V 裸机内核，打印启动标记并关机；CI 能自动检查这一过程。

接下来：VisionFive2 启动、内存管理、任务与用户程序、SD 驱动、FAT32。它们目前都还没有验收通过。历史官方测例和性能实验也还没有运行结果。

## 第一天：跑起来 → 选任务 → 提第一个 PR

1. 按 [上手指南](docs/getting-started.md) 安装工具、fork 仓库并运行 `make check`。
2. 在 [任务入口](docs/backlog.md) 选一个可以现在开始的任务，在对应 Issue 认领。
3. 找一位队友确认接口和验收方法，再开始实现。提交方法见 [协作流程](docs/collaboration.md)。

跑通后的成功标志：终端出现 `PASS: BOOT-001`，串口日志中有 `FOCOS_BOOT_OK`。

## 做到哪个阶段，再读哪份文档？

| 你现在要做什么 | 阅读入口 |
| --- | --- |
| 配环境、第一次提交 | [上手指南](docs/getting-started.md) |
| 选任务、看依赖 | [任务入口](docs/backlog.md) |
| 写 spec、提交和审阅 PR | [协作流程](docs/collaboration.md) |
| 看本周目标和取舍 | [推进路线](docs/roadmap.md) |
| 接入往届官方测例 | [功能验证](docs/verification.md) |
| 做性能实验、准备图表 | [性能实验](docs/benchmarks.md) |
| 查赛规、外部来源 | [来源说明](docs/references.md) |

## 几条固定约定

- 内核用 Rust `no_std`；首块真板是 VisionFive2 / JH7110（RV64GC），全国赛再扩展龙芯 2K1000LA 星云板（LoongArch64）。
- 基础文件系统是 FAT32。CPU/板卡差异放在 HAL 和 arch，文件系统与 VFS 不写架构条件编译。
- [MindScrath/Focos](https://github.com/MindScrath/Focos) 是公有上游。成员从个人 fork 提 PR，main 必须可构建，合入需要 CI 和队友批准。
- 报名资料、邮箱、令牌不放进代码库或日志。`devdoc/` 只用于本地资料，已被 Git 忽略。

GitHub 开发仓库不能替代赛方要求的 GitLab 提交。第三方代码引入前要确认许可证并记录来源；AI 辅助记录见 [ai-usage.md](docs/ai-usage.md)。
