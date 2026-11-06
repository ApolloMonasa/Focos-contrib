# 选一个 3–6 小时的第一步

在 Issue 认领、约 reviewer，再从 [SPEC 模板](../specs/TEMPLATE.md) 写行为。负责人和进度只维护在 Issue，下面只作导航。

## 基础链路

| 任务 | 第一份交付 |
| --- | --- |
| [#1 块设备与内存盘](https://github.com/MindScrath/Focos/issues/1) | 越界/错误行为 SPEC 与失败测试 |
| [#2 真板 UART 与时钟](https://github.com/MindScrath/Focos/issues/2) | 独立复现首次启动，标明 SBI 与自有驱动 |
| [#3 SD 与 QEMU 块设备](https://github.com/MindScrath/Focos/issues/3) | 读已知扇区并校验 |
| [#4 内存与用户映射](https://github.com/MindScrath/Focos/issues/4) | 分配、释放、耗尽测试 |
| [#5 进程、ELF 与管道](https://github.com/MindScrath/Focos/issues/5) | 两任务切换和异常隔离规格 |
| [#6 FAT32](https://github.com/MindScrath/Focos/issues/6) | 内存盘 BPB/簇链测试 |
| [#7 VFS 与文件接口](https://github.com/MindScrath/Focos/issues/7) | 路径、FD、共享偏移约定 |

## 区域赛新增的兼容工作

| 任务 | 第一份交付 |
| --- | --- |
| [#8 完整测例接入](https://github.com/MindScrath/Focos/issues/8) | 2025 镜像与 11 组子用例清单 |
| [#22 Ext4](https://github.com/MindScrath/Focos/issues/22) | 镜像特性调查与只读测试 |
| [#23 动态加载、线程、信号](https://github.com/MindScrath/Focos/issues/23) | 一个动态 ELF 的解释器/库依赖 |
| [#24 TCP/UDP loopback](https://github.com/MindScrath/Focos/issues/24) | 一对客户端/服务端收发测试 |
| [#25 vim/gcc/make](https://github.com/MindScrath/Focos/issues/25) | 工具版本、依赖与终端需求 |

基础运行依赖 #2/#4/#5；文件依赖 #1/#3/#6/#7。Ext4 和运行时调查可提前开始，运行验收要等相关能力接通。#8 的失败结果分派给能力 owner，不由测试负责人包办所有内核实现。

## 实验与后续

- [#9 缓存实验](https://github.com/MindScrath/Focos/issues/9)：功能正确后比较 B0/B1。
- [#10 提交与教学材料](https://github.com/MindScrath/Focos/issues/10)：每周独立复现；补许可证和赛方 GitLab 提交项。
- [#11 星云板](https://github.com/MindScrath/Focos/issues/11)、[#12 全国赛扩展](https://github.com/MindScrath/Focos/issues/12)：双平台、LTP、2026 参考负载和真实磁盘检索。

交付边界见 [REGION-001](../specs/REGION-001.md)，检查点见 [路线](roadmap.md)。
