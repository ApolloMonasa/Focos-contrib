# 怎样证明功能可用

**当前没有历史测例通过成绩。** 启动检查和真板首次输出分别有证据，不能替代下面的验收。

## 本次选哪套

区域赛主线为 `pre-2025` 的全部非 LTP 组，RISC-V 的 musl/glibc 都纳入。`pre-2024` 32 个 basic 程序只用于早期定位，不再是区域赛交付上限。

| 内容 | 入口 |
| --- | --- |
| 交付范围与平台 | [REGION-001](../specs/REGION-001.md) |
| 11 组、来源与待办状态 | [regional-plan.json](../tests/regional-plan.json) |
| 历史分支固定提交 | [sources.lock.json](../tests/sources.lock.json) |
| 起步用 basic 清单 | [catalog.json](../tests/catalog.json) |
| 版本选择、工具来源和已知坑 | [测例审计](reference/test-audit.md) |

## 接入顺序

1. **冻结输入。** #8 锁定镜像 SHA256、容器 digest、测试/judge SHA，展开所有子用例。当前只有组清单，尚无可执行的全套 runner。
2. **先验证判分。** 同一产物在参考 Linux/QEMU 运行；失败、超时、缺失输出必须判失败。核对辅助文件、动态加载器和脚本前置动作。
3. **接入 FocOS。** 按原脚本串行运行；musl/glibc 分开记。先 basic/BusyBox，再各组，最后从干净镜像全量回归。
4. **接工具演示。** 2025 原版 vim/gcc，加已锁定来源的 make；编辑、构建、执行并检查磁盘副作用。版本输出只是加载检查。

11 组 × 2 libc = 22 个组环境组合；不能拿它代替子用例总数。只有全部纳入项通过才完成目标，启动脚本跑到结尾不算全通过。

## 每次留下什么

保存内核/测试/judge SHA、镜像与 ELF 哈希、环境、完整命令、原始日志、退出状态和结果表。状态用 PASS / FAIL / TIMEOUT / NOT_RUN / BLOCKED_BY_DEPENDENCY，LTP 单独标明范围外。

QEMU、VF2、LoongArch 分开报告；原版 Ext4 镜像与适配镜像分开报告。改 argv、打包或脚本须留补丁，不能改被测语义。写入和工具构建同时核对文件内容与新产物。

额外自测覆盖坏指针、OOM、异常隔离、簇链环、跨簇写、失败同步及资源释放。官方测试只证明它实际覆盖的行为。
