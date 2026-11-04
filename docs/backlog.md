# FocOS 模块任务入口

这些链接指向 upstream 的实际 issue，认领人、状态、依赖和证据以 issue 为准。此处只提供导航，不维护第二套状态表。

| 模块任务 | 认领入口 |
| --- | --- |
| HAL-001 确定 BlockDevice 契约并建立可测试内存盘 | [#1](https://github.com/MindScrath/Focos/issues/1) |
| BOARD-001 验证 VisionFive2 启动 UART 和定时器 | [#2](https://github.com/MindScrath/Focos/issues/2) |
| IO-001 打通 JH7110 真实 SD 块读写 | [#3](https://github.com/MindScrath/Focos/issues/3) |
| MM-001 实现帧分配器并验证回收和 OOM | [#4](https://github.com/MindScrath/Focos/issues/4) |
| CORE-001 接通异常 定时调度与用户 ELF | [#5](https://github.com/MindScrath/Focos/issues/5) |
| FS-001 实现 FAT32 读写与同步后重启校验 | [#6](https://github.com/MindScrath/Focos/issues/6) |
| VFS-001 确定路径 FD 错误语义并接入用户文件操作 | [#7](https://github.com/MindScrath/Focos/issues/7) |
| TEST-001 锁定并接入 pre-2024 的 32 个源码测试程序 | [#8](https://github.com/MindScrath/Focos/issues/8) |
| PERF-001 记录存储基线并实现同预算缓存消融 | [#9](https://github.com/MindScrath/Focos/issues/9) |
| DOC-001 维护设计 教学实验与区域赛演示证据 | [#10](https://github.com/MindScrath/Focos/issues/10) |
| ARCH-002 在 2K1000LA 星云板复用同一存储实现 | [#11](https://github.com/MindScrath/Focos/issues/11) |
| COMPAT-001 建立全国赛原环境兼容性和磁盘检索数据闭环 | [#12](https://github.com/MindScrath/Focos/issues/12) |

第一周建议三条并行线：一人处理 BOARD-001 并启动 MM-001，一人处理 HAL-001 并尽早做 IO-001 验证，一人处理 TEST-001 并和另一位成员共审 VFS-001。具体人选通过 issue 认领，不能让同一人包办所有关键路径。

每天异步记录遇到的阻塞，每周演示实际运行结果。每个模块大任务按协作要求拆成小 PR，不能把整套模块攒到一次合入。

[查看所有开放任务](https://github.com/MindScrath/Focos/issues) · [查看里程碑](https://github.com/MindScrath/Focos/milestones)
