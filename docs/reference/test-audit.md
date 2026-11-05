# FocOS 历史测例选择与功能证据

2026-11-04 审计了 `oscomp/testsuits-for-oskernel` 的全部 19 个可见分支：逐一读取根目录和 README，再深入核对候选分支的运行清单、构建文件与部分测试源码。版本固定在 [sources.lock.json](../../tests/sources.lock.json)。这次完成的是选型和清单建设，尚未编译或在 FocOS 上运行官方测试。

## 分支比较及选择

| 分支 | 已核实内容 | FocOS 的用途 |
| --- | --- | --- |
| `main`、`master`、`testscripts` | 早期 Linux syscall 子集；README 指向 FAT32、QEMU RV64/K210 | 查语义、运行参考；不能仅因叫 main 就当最新赛题 |
| `pre-2023` | FAT32 基本 syscall；附历史镜像 | 区域赛回退/交叉核对；和 2024 重复的测试不重复计算覆盖率 |
| **`pre-2024`** | 明确 FAT32；附 RISC-V/LoongArch 命名镜像；现有 CMake 脚本仍主要针对 RISC-V | **区域赛主基线：先 RISC-V**；LoongArch 镜像须另核验 ELF/ABI，不能假定源码已经完整支持双架构构建 |
| `final`、`final-comp`、`libc-test` | 早期 BusyBox/Lua/lmbench 路径；部分含 libc-test；README 提及 RV64/K210 | 作为应用语义和小型脚本参考；不直接沿用过时硬件环境 |
| **`final-2023`** | BusyBox、Lua、iozone、libc-test、lmbench 等；Makefile 用 `mkfs.vfat -F 32` 构造镜像 | **FAT32 上的第二阶段压力负载候选**；只选择依赖已具备的命令和文件 I/O 子集 |
| `pre-2024-riscv64/aarch64/x86_64` | 内容是 2023 风格的综合测试；分支名覆盖更多架构，README 仍为旧版 | 暂不采用；避免仅凭分支名推断平台支持 |
| `final-2024-rv`、`final-2024-la` | 两套分架构综合测试；README 明确 Ext4；RISC-V 版含 LTP | 全国赛回归与差异定位，不增加到区域赛 FAT32 必过集合 |
| `2025_multiarch`、**`pre-2025`** | 12 组综合测试，RISC-V/LoongArch，musl/glibc，Ext4；pre-2025 构建/运行资料更完整 | **全国赛兼容性主参考**；优先 basic，再 BusyBox/文件 I/O/libc/LTP；镜像与测试版本必须匹配 |
| **`final-2025`** | `/proc/interrupts`、`copy_file_range`、`splice` 三个专项 | 选择性补充：中断可观测性和文件复制路径，很适合后续存储方向；实现正确后再讨论零拷贝收益 |
| **`final-2026`** | glibc 下 CAgent 与 BuildStorm；后者包含 Rust/cargo 复杂构建 | 全国赛能力差距清单：动态链接、线程、mmap、进程/文件语义等；不安排进七周区域赛范围 |
| `on-site-final-2023`、`on-site-final-2024`、`on-site-final-2025` | 历年现场题；2025 明确 QEMU 双架构和两块真板，涉及 git/vim/gcc/rustc 等 | 演练与兼容性远期目标；现场题得分不并入基础测例覆盖率 |

推荐推进链：`pre-2024` 的 FAT32 基础功能 → `final-2023` 中适配的文件负载 → `pre-2025` 双架构原环境 → `final-2025` 存储专项 → 当届正式规则与 `final-2026` 所代表的复杂应用能力。每一步须在已有功能可复现后推进。

`pre-2024` 的 `run-all.sh` 有 **31 个入口，其中包含 `yield.sh`**，但源码目录实际存在 `yield.c` 和 `sleep.c`，不存在该路径的 `yield.sh`，旧脚本漏列 sleep。以源码和配套 `_test.py` 盘点得到 32 个测试程序，另有辅助程序 `test_echo.c`。因此区域赛目录采用这 32 个测试程序，保留旧脚本原始内容及「补 sleep、改用 yield」的 runner 适配记录；被测程序不修改。`pre-2025` 的对应清单已经列出 32 个程序入口。入口数、进程数、语义断言数和得分是不同指标，不能混用。

## 首轮模块到测试映射

以下是实现顺序和待验收目标，不是通过结果。冻结后的具体选择在 [catalog.json](../../tests/catalog.json)，首次运行前核验每个测试的隐含依赖与输出判据。

| 能力 | 优先查看的官方入口 | 额外自测 |
| --- | --- | --- |
| 用户态进入、身份、退出 | `write`、`exit`、`getpid`、`getppid`、`uname` | 用户坏指针、返回寄存器、退出后资源回收 |
| 时间与分配 | `gettimeofday`、`times`、`brk`、`sleep` | 时间单调性、非法 brk、OOM、timer 抢占与 sleep 唤醒 |
| 文件/目录闭环 | `open`、`openat`、`close`、`read`、`write`、`fstat`、`getcwd`、`chdir`、`mkdir_`、`getdents` | 跨簇、空文件、EOF、LFN、错误路径、同步重启校验 |
| FD 共享和删除 | `dup`、`dup2`、`unlink` | 共享偏移、关闭后使用、目录缓存失效 |
| 进程/调度/IPC | `fork`、`clone`、`execve`、`wait`、`waitpid`、`pipe`、`yield` | 两任务公平性、异常隔离、阻塞唤醒、死锁 |
| 映射与挂载 | `mmap`、`munmap`、`mount`、`umount` | 映射边界、共享文件一致性、busy 挂载点、非法设备 |

区域赛以基础身份/时间/文件路径的可执行子集为 P0，进程复制、文件映射等为扩展目标；完整清单始终列出，未运行项目不删除。源码审计发现 `exit.c` 实际先 fork 再 wait，因此其官方测试归入 P1，基本 exit syscall 用团队自测提前验证；不能仅凭文件名把它列为最简单的第一项。当前 P0 候选为 16 个入口。任务调度/中断/内存安全必须有自己的集成用例，不能等待所有历史进程测例通过才验证。

`mount.c` 在 2024/2025 所读版本中请求 `vfat`，默认设备 `/dev/vda2`，并允许 argv 指定设备和挂载点。runner 应准备真正可挂载的设备和目录；如传入不同 argv，要记录为环境适配，不能伪造成功返回。只有在正确完成挂载/卸载语义后才算通过。

## 如何复用并保持证据可信

1. 固定源仓库、分支、完整 SHA、构建容器 digest、工具链和 judge 版本；保存原始源码，必要补丁放独立路径并解释。取镜像时额外记录下载 URL 与 SHA256，分支 SHA 不能替代镜像哈希。
2. 先在参考 Linux/QEMU 环境跑相同产物，确认 ELF 架构、入口、ABI、辅助文件、解释器及判分方式。负面输出应当使 runner 失败，防止解析器误报。
3. 区域赛优先使用历史 FAT32 测试；移植其他分支时只调整打包/启动适配，不修改被检验的语义或降低断言。直接启动静态测试 ELF 可以先替代 shell，但 shell 脚本中的前置动作不能省略。
4. FocOS 逐项串行执行，保存完整 stdout/stderr、退出状态、超时、启动/结束边界；原 judge 与团队补充的副作用校验一起使用。只打印 `success` 或存在首尾标记不足以证明功能正确。
5. 结果分为 PASS、FAIL、TIMEOUT、NOT_RUN、BLOCKED_BY_DEPENDENCY，附具体原因。公布“通过 X / 冻结入口总数 Y”，同时公布断言结果；部分断言通过不自动变成整个程序通过。
6. 一份结果包包含内核 SHA、测试 SHA、judge SHA、ELF/镜像哈希、环境、完整命令、原始日志、机器可读结果、补丁/argv 差异。文件写入类额外保存重挂载或主机检查结果。
7. 对外明确标注「历史官方测试，原环境」或「历史官方测试，FAT32 打包/运行适配」。团队自测和正式平台得分分开；不得将局部通过描述为通过整届国赛。

官方 `autotest-for-oskernel` 是运行和判分框架，可参考其 Docker/QEMU 驱动与 judge，但不能直接假定其 main、镜像、测试源码自动匹配。例如本次读取的 autotest README 镜像引用是 `pre-20250615`，而 `pre-2025` Makefile 的构建镜像标签是 `20250624`。接入 issue 必须先选择一套一致组合，并验证运行命令在该版本下有效。

基础功能证据包括测试矩阵、源码/规格关联、失败路径、原始日志和演示，而非只有一个通过率。官方历史测例能够证明其覆盖的行为；不覆盖完整 FAT32 一致性、所有异常或所有并发语义。
