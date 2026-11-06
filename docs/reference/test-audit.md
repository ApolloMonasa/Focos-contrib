# 选哪年的测例，为什么

2026-11-06 复核官方仓库的 19 个可见分支，**未发现 `pre-2026` 分支**。这不表示不存在其他发布渠道。固定提交见 [sources.lock.json](../../tests/sources.lock.json)；本次只审源码与发布信息，未运行测例。

## 选择结果

| 来源 | 本项目用途 |
| --- | --- |
| `pre-2025` | 区域赛主验收：11 组非 LTP，musl/glibc 两套，先 RISC-V |
| `on-site-final-2025` | 区域赛 vim 编辑与 gcc 编译运行；另加团队 make 验收 |
| `final-2025` | copy_file_range、splice、interrupts 的后续专项，不替代初赛必达 |
| `final-2026` | CAgent 与 BuildStorm；更适合作为完成 2025 目标后的扩展 |
| `pre-2024` | 32 个 FAT32 basic 程序，用于起步和定位，旧 P0 不再是交付上限 |
| `final-2023` | FAT32 文件负载参考；不混入 2025 成绩 |

其余分支：`main/master/testscripts/pre-2023` 是早期基础参考；`final/final-comp/libc-test` 是旧综合测试；`pre-2024-riscv64/aarch64/x86_64` 不能仅按名字认定平台；`final-2024-rv/la` 是分架构 Ext4 测试；`2025_multiarch` 供构建差异核对；`on-site-final-2023/2024` 留作历史参考。不要把 main 当成最新赛题。

## 11 组带来哪些内核能力

| 测试组 | 重点依赖 |
| --- | --- |
| basic、busybox、lua | 文件、目录、进程、shell、时间 |
| libctest、libcbench | libc 语义、动态加载、TLS、mmap 与线程等，逐子项核对 |
| iozone、unixbench、lmbench | 文件 I/O、进程/管道、映射、信号和计时 |
| iperf、netperf | TCP/UDP、loopback、并发连接、阻塞与退出 |
| cyclictest | 线程、定时、调度/亲和性接口、压力子进程与信号 |

组名和脚本固定在 [regional-plan.json](../../tests/regional-plan.json)。**全部**指脚本展开后的子用例全部满足判据；不是只运行 11 个脚本或只选 musl。性能程序应完成操作、生成有效结果，不要求追平 Linux 性能或宣称历史满分。

## 镜像与工具：已知道什么，尚缺什么

- [2025 源码与说明](https://github.com/oscomp/testsuits-for-oskernel/tree/8b58dd16d26d30f7c74d48d5832d870d3051b703)：原环境 Ext4，构建同时生成 musl/glibc，README 要求 RV/LA 两架构；本队区域赛仅认领 RISC-V，LoongArch 后续补。
- [2025 初赛发布镜像](https://github.com/oscomp/testsuits-for-oskernel/releases/tag/pre-20250615)：可作候选；其发布日期与源码 Makefile 的 `20250624` 容器标签不同。#8 必须核对脚本/二进制对应关系，不能直接视为同版本。
- [2025 现场说明](https://github.com/oscomp/testsuits-for-oskernel/blob/9ea291d8535774680e88f4e6bf725d7ba156e752/README.md)包含 vim 编辑保存、gcc 编译运行；**make 不是其中的独立题**。
- [现场 RV 镜像](https://github.com/oscomp/testsuits-for-oskernel/releases/tag/on-site-final-2025-rv64-fs)可作工具来源；[soft-info](https://github.com/oscomp/testsuits-for-oskernel/blob/9ea291d8535774680e88f4e6bf725d7ba156e752/soft-info.txt)列 GCC 14.2.0、Vim 9.1 等，但有 LoongArch 输出，RISC-V 二进制版本仍需从实际镜像核实。
- 当前只取得 GitHub 发布元数据中的压缩包 digest，未下载校验、挂载镜像或冻结 make 版本。候选 URL/digest 与待办项保存在计划 JSON，不能当作已验收锁文件。

## runner 必须处理的坑

1. `pre-2025/scripts/test_all.sh` 有旧入口，不能直接当完整清单。以组脚本及其实际子程序展开；libctest 包含 static/dynamic 两轮。
2. iperf/netperf 使用 `127.0.0.1`。先跑真实回环协议，不要求凭空增加物理网卡验收，也不能伪造返回。
3. 旧 basic runner 漏 `sleep` 并误用 `yield.sh`；`exit.c` 依赖 fork/wait。起步清单保留这些历史记录。
4. mount 测试请求 vfat，并涉及设备/挂载点夹具。准备真实设备与目录；argv/打包适配留补丁，不吞错误。
5. 原 judge、容器、镜像版本要对应。失败、超时、缺失输出都保留；文件操作检查真实副作用。

[2026 决赛](https://github.com/oscomp/testsuits-for-oskernel/blob/b5ec6ef8497e1818cbdec3b54bb722f036e57972/README.md)的 BuildStorm 包含 glibc、Rust/cargo 复杂构建与多核条件，不能当作 vim/gcc 的等价替代。2026 参考任务和下一届正式要求分别记录。
