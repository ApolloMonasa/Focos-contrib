# TEST-001：12 组 benchmark 输出复查

这次把复查范围从 LTP 扩大到区域赛清单的全部 12 组。逐组核对包装脚本、实际打印结果的代码和锁定镜像中的程序，并在参考 Linux 上采集输出。发现的问题涉及退出值、指标单位、调用身份和启动条件，不能只补正则表达式。本文是输出依据和缺口清单，**不是已完成的全组解析语法，也不是通过成绩**。

原始片段、文件哈希和实验入口见[证据目录](../../tests/test001/fixtures/benchmark-output/README.md)。LTP 新旧 C/shell 的 18 个受控输出实验另见[前轮复查](test001-output-audit.md)。本轮没有使用旧 judge 的输出定义规则。

## 这次实际检查了什么

- 对照固定比赛源码 `8b58dd16d26d30f7c74d48d5832d870d3051b703` 和镜像 `95973543…e378b8`：24 份组脚本的差异仅是组标记追加 `-musl` / `-glibc`。逐字节差异见 `wrapper-comparison.json`。这不证明镜像里每个 ELF 都由该提交构建。
- 从镜像只读提取程序与所需文件，保留权限，在固定 Linux 内核、显式 OpenSBI、QEMU RISC-V virt、2 CPU / 1 GiB 下运行。使用 initramfs、root、BusyBox shell、回环网络；没有挂载原比赛盘，也没有运行 FocOS 或真板。目录移到 `/suite`，shell 入口和库路径均属实验适配。
- `original`：两套目录分别尝试全部 12 组脚本。LTP 仅装入 access01/getpid01/getppid01/getuid01 四个程序，不代表完整目录、分片或全部调用。失败、超时、缺解释器均保留。
- `supplement`：显式用 shell 执行没有执行权限的 basic/run-all.sh；为软浮点 musl 程序补正确解释器别名；单独执行 libc-test 成功/缺文件两个调用；重跑 netperf/iozone 和四个 LTP 程序。
- `format-probes`：UnixBench 缩短运行参数，保留未经过管道过滤的 COUNT/TIME；lmbench 缩短重复次数。lmbench 多数项目仍超时。
- `timing-probes`：根据 lmbench 原库支持的环境变量，显式设 `ENOUGH=10000 TIMING_O=0 LOOP_O=0`，才取得匿名带宽、mmap、context 和文件系统表格。这些参数改变校准条件，数值只说明格式，不能作性能结论。

不同组在同一来宾内顺序运行，超时后的进程组没有完整回收证明；串口合并了 stdout/stderr，可能有异步输出。片段边界是审计助手打印的标记，不是比赛原协议，更不是可靠的子进程归属证明。原始 COUNT 实验已出现上一进程迟到输出。因此不能把本轮样本直接包装成 C 阶段可信运行包；后续正式采集必须隔离调用并验证后代回收。

## 逐组对照

下表源码路径均相对于上述固定提交；`sources.json` 锁定对应文件，镜像成员另有哈希。每个组的外层包装器是 `scripts/<组>/<组>_testcode.sh`（libc-test 对应 libctest，libc-bench 对应 libcbench）。表中的规则是后续语法表必须满足的条件，不是当前 Lua 解析器已经支持。

| 组 | 实际生产者与输出特征 | 本轮观察及规格约束 |
| --- | --- | --- |
| basic | `basic/user/src/oscomp/*.c`、`basic/user/include/stdio.h` 和 run-all.sh。32 个程序各自打印 START/END、返回值、路径、PID 或成功/错误文字；不存在统一的 Pass!。fork 等有多进程输出，execve 的成功路径会换程序，源码注释掉自身 END | 原包装器遇到 run-all.sh 权限错误仍打印组 END、退出 0。显式 shell 后 glibc 执行并出现成功、断言/挂载失败等；musl 目录的 basic ELF 实际要求 glibc 加载器。逐程序规定正向证据，不能要求所有程序有同样 END，也不能从组 END 推断完成 |
| busybox | `scripts/busybox/busybox_testcode.sh` 和命令表：`testcase busybox <整条命令> success/fail`；命令内部空格、引号、重复均有意义。`false` 是退出状态特殊分支 | 双目录得到真实命令输出和结果行；普通 stdout 可含任意文本。调用身份绑定命令表位置与原命令，不能取一个 token、去重或搜索任意 success。原有共享文件生命周期要求仍适用 |
| lua | `scripts/lua/test.sh` 与九份 Lua 输入：`testcase lua <名称> success/fail` | 双目录原脚本得到九条 success；已有缺文件/return -1 实验继续保留。组脚本退出、Lua 进程退出、脚本断言结果各不相同，不能扩大 success 的含义 |
| libctest | `libc-test/src/common/runtest.c`：`========== START <wrap> <name> ==========`、诊断、`Pass!` 或带 status/signal/timed out 的 FAIL、END。run-static.sh/run-dynamic.sh 决定调用身份；wrap 可为空 | 双目录单独执行 argv 输出 Pass!，运行器返回 **1**；缺文件也返回 **1**。源码结尾固定 return 1。组脚本仍返回 0。不能把 1 一律当测试失败，也不能因为 1 是已知行为就忽略 FAIL；须同时核对身份、唯一结果、结束和执行异常 |
| libcbench | `libc-bench/main.c`：27 个 `函数 (参数)` 标签后跟 `time: 秒.九位小数, virt: 整数, res: 整数, dirty: 整数`。同函数不同参数重复出现 | 双目录取得完整格式。time 来自 CLOCK_REALTIME 差值；内存来自 smaps 中筛选的匿名/heap 映射，单位 kB，不能解释为整个进程 RSS。main 忽略 run_bench 返回状态；退出 0 不能弥补缺标签/缺指标，读不到 smaps 得到的 0 也不是可靠内存测量 |
| cyclictest | `rt-tests-2.7/src/cyclictest/cyclictest.c` print_stat；四场景 begin/end 内 `T:线程 (tid) P:优先级 I:间隔 C:次数 Min:… Act:… Avg:… Max:…`；hackbench 输出来自同目录相邻项目 | 双目录观测单线程/八线程、无压力/有压力及清理文字。原参数 `-D 1s` 限时，线程间隔不同，C 不必相等；不能统一要求 C=1000。原参数默认微秒，`-N` 会改纳秒，须绑定 argv。kill 成功仅说明信号发送成功，不证明所有工作进程已回收；清理错误阻断压力场景 |
| iperf | `iperf/src/iperf_locale.c`、`iperf_api.c`；TCP/UDP、正反向/并发共六场景；Interval/Transfer/Bitrate，UDP 还有 Jitter/Lost/Total；sender/receiver 和 SUM 是不同维度 | 双目录有真实回环结果。区间行不是最终汇总，SUM 不能和单流相加；单位随量级变化，需绑定流 ID、方向、角色和场景。客户端 end success 不能代替服务器启动/退出与清理证据 |
| netperf | `netperf/netperf-2.7.0.tar.gz` 内 `src/nettest_omni.c` 的 MIGRATED 输出及 nettest_bsd.c/netlib.c；UDP_STREAM/TCP_STREAM 两类吞吐表，UDP_RR/TCP_RR/TCP_CRR 三类事务表 | musl 补加载器后取得五类表格；glibc 两轮均发生地址解析错误，尚缺成功表格。UDP_STREAM 两行发送/接收，包含 Okay/Errors；stream 单位 `10^6bits/sec`，RR 单位 `Trans./sec`，不能一律取最后数字当带宽。服务端后台启动与 kill 不构成完成证明 |
| iozone | `iozone/iozone.c`；原脚本八条命令包含自动测试表和四进程测试。`Output is in kBytes/sec`、列标题、文件/记录大小，以及 Children/Parent throughput、Min/Max/Avg 等不同统计 | glibc 原脚本、musl 补加载器后均取得原表格。区分 auto 表格与并发统计，保留模式、块大小、文件大小、进程数、父/子统计维度；不能将所有吞吐行压成同一值。参数能改变单位与列集合，需按具体命令冻结；完成文字不替代每条命令状态 |
| lmbench | `lmbench_src/src/lib_timing.c` 和各 lat_*/bw_*。既有带标签微秒行，又有无标签两列、context 表、文件系统表；默认计时输出多在 stderr | 原脚本超时，不能声称完整通过。显式调整校准后的格式样本：bandwidth 两列为 MB 与 MB/s（MB=1,000,000）；mmap 两列为 MB 与微秒；context 的 `"size=… ovr=…` 后为进程数与微秒；lat_fs 为大小、创建次数、每秒创建/删除速率，缺计时可写 -1。不能仅按列数识别含义；musl lat_fs 还出现 mkdir 错误与 -1，退出却为 0；原始脚本的完整成功样本仍缺 |
| unixbench | `UnixBench/src/*.c` 原始 COUNT/TIME，经组脚本 grep/awk 变成 `Unixbench 名称 test(标签): 数值`；context 管道还只取最后 COUNT | glibc 原脚本和双目录缩短参数实验覆盖多种格式；musl 原入口缺解释器，shell 项 multi.sh 执行失败。**Whetstone 原始 COUNT 单位 MWIPS，被脚本标为 MFLOPS；pipe 等 COUNT 是时段累计 iter，脚本未除时长却标 lps；looper 也未转换就标 lpm。** 原标签必须原样保存，未经计算核对不得作为规范单位或成绩 |
| ltp | 新旧 C/shell 输出库，API 不同导致位置前缀、颜色、汇总和退出位不同；详见前轮复查 | glibc 四程序实测既有 TPASS，也有 access01 因缺 nobody 用户而 TBROK；getpid01 一次调用产生多条断言。musl 四程序因缺匹配的硬浮点加载器无法运行，不能用软浮点 libc 冒充。仍缺全部调用到 API/依赖的绑定，不开放全组判分 |

## 必须修改的设计边界

1. **分开三层退出值。** 主机采集器、来宾调度脚本、被测程序/运行器分别记录。主机与调度协议仍要求正常完成；子调用按固定生产者定义成功条件。libc-test 的 1 只是一项原生事实，不能直接变成 PASS 或 FAIL。LTP 的严格判据仍按专属约定执行。
2. **目录标签不证明 ABI。** PROFILE 的 libc 槽位须绑定实际 ELF、解释器、浮点 ABI 和共享库。当前镜像 musl/basic 的文件与 glibc/basic 对应文件相同；不能声称两套独立 libc 测试已经完成。新增解释器别名、显式 shell、共享数据准备均登记适配，失败不隐藏。
3. **标签不证明指标含义。** 保存 printed_label / raw_value / 原单位；只有来源、公式、参数和维度已核实才发布规范指标。UnixBench 单位冲突没有解决前，只展示原始观察并阻断该指标验收；不得静默改名、除以名义时长或混入已有性能比较。
4. **原包装器不足以证明调用完整。** 大部分包装器以 echo 结束；管道、后台任务和子进程的失败可能丢失。必须先设计可审阅的调用边界、状态捕获和清理协议，再实现正式 runner；不能靠新增 RESERVED 关键词兜底。Lua 只拒绝已登记的结构，不承诺识别任意跨组普通文本。
5. **未知仍是未知。** 本轮实测范围与完整语法支持分开。一个正例不能穷尽条件分支；未取得正例/错误例、单位未核实或依赖未闭合的组保持未冻结。后续每组须补独立语法表与错误矩阵，再实现解析器。

## 对既有工作的影响

已有 A0 镜像/调用目录/ELF/启动审计和 Lua 字节样本仍保留；本轮没有推翻其固定文件事实。#56 继续暂停合并，等独立规格 PR 人工审阅后，只同步 Lua 相关的输出拒绝和回归用例。其他 benchmark、调用调度及性能语义不塞入 #56。

本轮新发现的 libc-test 退出、UnixBench 单位和调用边界属于设计问题，先在规格中明确，不对旧 parser 做局部补丁。下一阶段分别完成未闭合的原环境样本、调用状态协议、逐组语法与负例；完整 A0/A1、可信运行包及全套验收都仍不能宣称完成。
