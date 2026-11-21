# 隔离调用到局部报告的本地实验

2026-11-22，本轮实际执行 30 次全新 QEMU 来宾，覆盖两套目录各 15 个场景。程序来自已锁镜像，内核、固件和成员身份沿用 [全组复查](../benchmark-output/README.md)。本实验仅验证重设计中的小范围链路，不注册 TEST-001 生产命令，不宣称完整 A/B/C 或 FocOS/VF2 验收通过。

## 实际结果

| 场景（两套目录分别运行） | 原生/主机观察 | 本地判断 |
| --- | --- | --- |
| 原 Lua 包装器执行 sort.lua | 包装器退出 0，唯一 success，无残留用户进程 | 局部 PASS |
| 原 Lua 包装器执行不存在的文件 | 包装器仍退出 0，输出 fail | FAIL |
| libc-test 原运行器执行 argv | 输出 Pass!，运行器退出 1 | 局部 PASS |
| libc-test 原运行器执行不存在的程序 | 输出 FAIL，运行器仍退出 1 | FAIL |
| 合成父进程退出 0、后台 sleep 存活 | 剩余用户进程被记录，随后销毁来宾 | BLOCKED |
| 合成 sleep 子进程挂起 | 主机 5 秒超时、终止并回收 QEMU 进程组 | BLOCKED，保留 TIMEOUT |
| 在 Lua 失败前输出伪造 PILOT 控制文字 | 文字被编码保留在 stdout，未变成控制帧 | BLOCKED，不能变 PASS |
| iozone 原入口 | musl 缺加载器，shell 返回 127；glibc 输出自动测试表 | 只保留观察 |
| iozone 显式增加软浮点 musl 加载器别名 | musl 得到输出、退出 0；glibc 作为对照 | 只保留观察，不是原环境通过 |
| lmbench lat_syscall 原校准设置、-N 1 | 早一轮双目录超时；最终复跑在相同 25 秒限时内完成，差异原因未确定 | 保留两次观察，不能声称原设置必然超时 |
| 同一 lmbench 调用显式设 ENOUGH=10000/TIMING_O=0/LOOP_O=0 | 双目录得到 microseconds 输出 | 只保留观察，不是原参数通过或性能结论 |
| netperf 回环环境 | glibc 地址解析失败；musl 缺加载器；包装器都可退出 0 | 只保留失败观察，不隐藏环境缺口 |
| netperf 尝试增加 dummy 网络接口 | 固定内核不支持该操作，显式前置返回 90，未执行 netperf | 只保留前置失败；没有证明地址解析失败根因 |
| UnixBench pipe 2 秒 | 直接得到 COUNT；musl 明确增加加载器别名 | 只保留原始计数，不按 lps 宣称速率 |
| UnixBench whetstone-double 1 | 直接得到 COUNT 的 MWIPS 和 TIME | 只保留观察，确认不能照抄包装器 MFLOPS |

共有 4 个局部 PASS、4 个 FAIL、6 个 BLOCKED、16 个 OBSERVATION。所有报告的 **acceptance 始终 BLOCKED**：本实验未交付正式可信运行包。30 次 QEMU 进程组均已确认回收，成功退出与超时退出分别记录；每个实验下一次启动使用全新 initramfs 和来宾。

## 数据怎样流动

`pilot.py` 先校验镜像成员及内核/固件，建立独占新目录，复制输入并核对私有副本；为每个调用构造独立 initramfs，保存实际命令、配置及哈希后启动 QEMU。固定 `init.sh` 将子调用 stdout/stderr 写到不同文件，wait 后记录 shell 状态，再扫描残留用户进程/僵尸、分别十六进制编码输出，最后关机。主机保留原始串口与 QEMU stderr、超时或超限原因，确认进程组回收，再由受限解析函数生成局部报告。

只有选定的 Lua sort.lua 和 libc-test argv 的精确输出有成功规则；其他格式不自动泛化。组脚本退出 0、原生运行器退出 1、主机退出值分别保存。输出中的伪造控制文字作为数据保留，不参与调度协议解析。

`earlier-lmbench.json` 另外保留早一轮两个超时实例，含原计划、串口片段及状态；与最终轮的来宾 initramfs 哈希和时限相同。早一轮 runner 版本与当前配方不同，索引保留其身份，但不宣称当前配方逐字节复现旧主机实现，也不能据此确定时序差异根因。后续成功不覆盖这些失败。

`index.json` 锁定当前配方、init 和样本文件。`samples.json` 保存各次真实计划、报告、从 PILOT BEGIN 开始的串口片段原字节、在完整串口中的偏移、完整串口哈希和 QEMU stderr。计划中的本机路径仅表示本次实际 argv，不是别人机器上的复现路径。局部判断是配方生成的观察；独立测试另外手写期望，不从这些判断自动生成答案。

## 明确限制

- stdout/stderr 目前在调用结束后才导出。超时只能保留已收到的串口，不能恢复来宾临时文件，不得把缺失输出写成空输出。正式方案必须实时保存，见[执行重设计](../../../../docs/reference/test001-execution-redesign.md)。
- 退出值是 shell wait 观察，不是完整 waitpid 信号结构；不能把 137 推断成唯一信号。正式监督器需补原生状态。
- /proc 扫描只是辅助诊断，不证明扫描期间绝无新进程。隔离保障来自整个来宾销毁；本实验不支持同一来宾继续运行下一个独立调用。
- 固定最小 initramfs、root、无网卡、无原盘挂载、BusyBox shell、/suite 工作目录和指定库路径均是实验条件。没有补足所有比赛环境；网络接口实验失败如实保留。
- 主机有 1 MiB 输出阈值和每例时限；采用定期检查，可能超过阈值后才停止，不满足生产级严格容量上限。输入复制/发布也不是完整 TEST-001 的对抗性所有权和原子发布实现。
- 同机可能同时运行其他实验，QEMU 默认时钟未固定；所有数值只用于格式核对。

最初的调试运行将 `/proc/PID/exe` 的悬空链接误当作用户进程，把内核线程也报为残留。已改为检查目标存在或僵尸状态；最终从新目录重跑全部 30 例。samples.json 只收录最终配方对应的 30 个真实样本；早一轮 lmbench 的两个超时另存，不与最终轮计数合并。其他旧调试目录留在本地，不覆盖或冒充最终证据。

## 复现

先按全组复查的 extract.py 提取锁定镜像；以提取结果的 root（下面包含 musl/glibc）为第一个参数，再给内核、固件和全新输出目录：

```bash
python3 -B tests/test001/fixtures/isolated-pilot/pilot.py \
  EXTRACTED_ROOT LINUX_IMAGE OPENSBI_FIRMWARE NEW_OUTPUT
python3 -B -m unittest discover -s scripts -p 'test_test001_isolated_pilot.py'
```

可用重复的 `--case lua-pass --case libctest-pass` 只跑指定场景，输出会明确少于完整 30 例。必须使用固定输入身份；需 Python 3.11+、QEMU RISC-V，来源和版本见已有全组证据。重跑的 PID/时间/吞吐可能不同，不要求串口哈希相同。已有输出目录拒绝覆盖。
