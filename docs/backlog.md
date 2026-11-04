# 先选一个小任务

每人同时只认领一个实现任务。先在 Issue 说明想做的第一步，再约一位队友 review；不要把模块名当作一次 PR 的工作量。认领与进度以 Issue 为准，这里不复制状态。

## 现在就能开始

| 任务 | 第一小步 |
| --- | --- |
| [#1 先做一个能在开发机测试的内存盘](https://github.com/MindScrath/Focos/issues/1) | 审块设备接口，准备内存盘测试 |
| [#2 让 VisionFive2 打印启动信息](https://github.com/MindScrath/Focos/issues/2) | 确认固件、串口与启动步骤 |
| [#4 实现物理页分配和回收](https://github.com/MindScrath/Focos/issues/4) | 定义页分配/回收和耗尽行为 |
| [#8 先把一个官方测试编译并判对，再接入全部清单](https://github.com/MindScrath/Focos/issues/8) | 先构建一个官方程序并核对判分 |
| [#10 每周留下可复现演示和答辩材料](https://github.com/MindScrath/Focos/issues/10) | 从干净环境复现当前启动演示 |

第一次接触存储可选 #1；手边有板可选 #2；熟悉 rCore 内存实验可选 #4；想先熟悉测试工具链可选 #8。#10 是持续任务，每个人都参与自己的功能说明。

## 接口确认后逐步推进

| 任务 | 如何安排 |
| --- | --- |
| [#3 让内核真正读写 SD 卡扇区](https://github.com/MindScrath/Focos/issues/3) | 接口依赖 #1，上板依赖 #2；资料调查可先做 |
| [#6 从 FAT32 读文件，再做到写后重启不丢](https://github.com/MindScrath/Focos/issues/6) | 内存盘准备好就能做解析，真盘验收等 #3 |
| [#7 统一路径、文件描述符和文件错误](https://github.com/MindScrath/Focos/issues/7) | 先与 #1 共审接口，再接 #5/#6 |
| [#5 让两个任务轮流运行，再启动用户程序](https://github.com/MindScrath/Focos/issues/5) | 先审异常与 ABI，再接 #2/#4 |
| [#9 测清文件访问开销，再比较普通块缓存](https://github.com/MindScrath/Focos/issues/9) | 先准备测量方法，正式实验等文件功能正确 |

## 全国赛扩展

- [#11 星云板移植](https://github.com/MindScrath/Focos/issues/11)：区域赛后主推。
- [#12 正式环境兼容与磁盘检索](https://github.com/MindScrath/Focos/issues/12)：拆成两条独立验收线。

三人的建议并行路线和周目标见 [推进路线](roadmap.md)，提交方法见 [上手指南](getting-started.md)。
