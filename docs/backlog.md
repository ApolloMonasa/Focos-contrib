# 从当前主线选下一步

核对日期：2026-11-19。在 Issue 认领、约 reviewer，再按 [六步 SOP](collaboration.md) 先审 SPEC、写失败测试、实现。负责人、阻塞和实时进度以 Issue 为准；本页只保留下一步入口。

## 当前优先推进

| 任务 | 已到哪里 | 下一份交付 |
| --- | --- | --- |
| [#4 内存与用户映射](https://github.com/MindScrath/Focos/issues/4) | 页分配、真实页接入及板级迁移已合入；MM-003 规格已合入 | 审阅 [#43 内核分页实现](https://github.com/MindScrath/Focos/pull/43) 的当前证据；该 PR 尚未合并，用户映射与 mmap 另拆专项 |
| [#8 测试接入](https://github.com/MindScrath/Focos/issues/8) | 第三轮规格 #49、首批 A0 审计 #50–#53 已合入；完整 A0 未完成 | 继续核对解释器/applet 的实际提供方式、真实输出语法及前置/清理条件，再按 A1–A3 交付解析与外部日志分析 |
| [#35 本核临界区](https://github.com/MindScrath/Focos/issues/35) | #42 已合入状态恢复、自检与屏障约定；真实中断验收未完成 | 与 #2/#5 先审 QEMU 异常/定时中断 SPEC，再证明待处理中断在临界区退出后按原状态触发 |

这三项可以并行；#4/#35 若修改同一启动或异常入口，先约定接口和合入顺序。#8 的 host 审计与解析可独立推进，FocOS 运行验收等待相关内核能力。

## 已完成，继续复用

- [#1 块接口与内存盘](https://github.com/MindScrath/Focos/issues/1)：PR #26；MM-001 页分配、MM-002 QEMU 真实页接入见 PR #28/#31。
- [#33 QEMU 板级迁移](https://github.com/MindScrath/Focos/issues/33)：PR #39 已合入并关闭任务，后续内存工作回到 #4。
- [#34 共享块设备检查](https://github.com/MindScrath/Focos/issues/34)：PR #41 已合入并关闭任务，复用 [TEST-002](../specs/TEST-002.md) 的独立镜像模型；真实 VirtIO/SD 接入回到 #3。

这些完成项不代表真板内存、真实驱动或断电持久化已经验收。

## 基础模块继续往哪里走

| 主任务 | 下一步入口 |
| --- | --- |
| [#2 VisionFive2 启动、UART 与时钟](https://github.com/MindScrath/Focos/issues/2) | PR #16 仅保留历史上板证据，未合入；先审主线真板启动接入 SPEC，再独立冷启动复现 |
| [#3 SD 与 QEMU 块设备](https://github.com/MindScrath/Focos/issues/3) | 共享检查已就绪；先审 QEMU VirtIO 驱动 SPEC，再读已知扇区；SD 资源调查可并行 |
| [#5 进程、ELF 与管道](https://github.com/MindScrath/Focos/issues/5) | 先接 #35 的异常/时钟联调，再拆上下文切换与用户 ELF 专项 |
| [#6 FAT32](https://github.com/MindScrath/Focos/issues/6) | 内存盘 BPB/簇链正常与损坏测试；真实驱动接入前可先推进格式行为 |
| [#7 VFS 与文件接口](https://github.com/MindScrath/Focos/issues/7) | 路径、FD 生命周期与共享偏移 SPEC，与 #5/#6/#22 共审 |

平台任务先读 [架构与接口交接](architecture.md)：#2/#3 对齐 PLAT-001、DRV-001，#4/#5 对齐 ARCH-001。跨层接口由调用方与实现方共同审阅，认领时说明向谁要资源、向谁交接口。

## 区域赛兼容工作

| 任务 | 下一步入口 |
| --- | --- |
| [#8 完整测例接入](https://github.com/MindScrath/Focos/issues/8) | 按 [TEST-001](../specs/TEST-001.md) 推进 A0→A1→A2→A3，再接 B 构建、C 采集、D 批次；[#47 解析/CLI 门槛](https://github.com/MindScrath/Focos/issues/47)、[#48 BusyBox 清单差异](https://github.com/MindScrath/Focos/issues/48) 随对应阶段闭合 |
| [#22 Ext4](https://github.com/MindScrath/Focos/issues/22) | 冻结实际镜像特性，先审支持范围 SPEC，再做 host 只读测试 |
| [#23 动态加载、线程、信号](https://github.com/MindScrath/Focos/issues/23) | 选一个动态 ELF，明确解释器、入口栈与库依赖；与 #4/#5/#7 共审 |
| [#24 TCP/UDP loopback](https://github.com/MindScrath/Focos/issues/24) | 一对客户端/服务端收发及超时 SPEC，运行验收依赖进程与 ABI |
| [#25 vim/gcc/make](https://github.com/MindScrath/Focos/issues/25) | 冻结工具版本、依赖与终端需求，再拆编辑、编译与增量构建验收 |

区域赛已纳入原 11 组与比赛冻结版本 LTP；调用集合与分母仍待冻结，不沿用旧 427 项或 22 槽作为全量范围。#50–#53 是输入审计，不代表可执行测试配置、参考校准或 FocOS 通过成绩。

基础运行依赖 #2/#4/#5；文件依赖 #3/#6/#7。镜像、运行时与格式调查可提前开始，运行验收等接口接通。#8 的失败结果按能力分派，不由测试负责人包办全部内核实现。

## 实验与全国赛

- [#9 缓存实验](https://github.com/MindScrath/Focos/issues/9)：先审 B0/B1 实验 SPEC，冻结预算与统计位置；正式测量等待 #3/#6/#7。
- [#10 提交与教学材料](https://github.com/MindScrath/Focos/issues/10)：持续独立复现、归档证据并核对许可和赛方提交项。
- [#11 LoongArch 与星云板](https://github.com/MindScrath/Focos/issues/11)：资料可先调查；实现先验 QEMU 架构，再验真板资源与设备。
- [#12 全国赛扩展](https://github.com/MindScrath/Focos/issues/12)：双平台、当届新增兼容测试、2026 参考负载和真实磁盘检索。

交付边界见 [REGION-001](../specs/REGION-001.md)，检查点见 [路线](roadmap.md)。任务完成、被替代或下一步改变时，由 owner 同步本页入口。
