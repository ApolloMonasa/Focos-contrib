# TEST-001 A 阶段：冻结清单与日志分析

关联：[Issue #8](https://github.com/MindScrath/Focos/issues/8) · 状态：实现待非作者审阅；B–D 未实现

## 这次做什么

把 2025 初赛原 11 组与比赛冻结 LTP 展开成可追溯的调用清单，并提供 `test001 analyze`：输入外部日志或已校验运行包，按冻结的 CRITERIA 生成 JSON/Markdown 报告。`test001 analyze` 不构建内核、不启动 QEMU，也不产生 FocOS 成绩。

完成后能看到：

- 一份有 6514 个调用的 PROFILE 和 24 个单组单 libc shard；
- 对同一日志分别给出采集、协议、段、逐项判分和验收结论；
- 对同一运行包用 J1/J2 两个判据重判，采集身份和分母不变；
- 15 份历史参考结果（来自 7 份日志）逐项对照旧摘要，已知差异单列。

## 命令

```bash
python3 scripts/test001.py analyze \
  --log tests/test001/fixtures/historical/logs/ref_lua3.log \
  --profile regional-2025-with-ltp-v1 \
  --criteria regional-2025-log-v1 \
  --group lua --libc glibc \
  --out /tmp/test001-lua-report
```

- `--group/--libc` 与 `--shard` 二选一；未知组、未知 shard、缺参数都在开始前退出 2。
- `--run` 消费固定布局的 `capture.json`、`serial.log`、`firmware.bin`；不能再用 `--profile/--group/--libc/--shard` 覆盖运行包选择。
- 普通分析完成报告即退出 0，即使结论是 BLOCKED；`--require-pass` 只在结论不是 PASS 时退出 1。
- 输出目录必须不存在；已存在、祖先含符号链接或写入失败都不覆盖旧结果。`report.json` 最后写入，`report.md` 的哈希记录在其中。

## 冻结了什么

| 项 | 值 |
| --- | --- |
| 测例源码 | `oscomp/testsuits-for-oskernel@8b58dd1`，分支 `pre-2025` |
| 初赛镜像 | `pre-20250615/sdcard-rv.img.xz`；解压 SHA256 `95973543…e378b8` |
| LTP | `20240524`；musl 2820 个、glibc 2840 个顶层调用 |
| 非 LTP | 11 组 × 427 项 × 两套 libc |
| 总调用 | 6514；shard 24 个，每个单组单 libc |
| PROFILE | `regional-2025-with-ltp-v1`，`profile_hash` 见 [profile](../../tests/test001/profiles/regional-2025-with-ltp-v1.json) |
| 判据 | `regional-2025-log-v1`（J1）、`regional-2025-log-v2`（J2，额外要求 LTP 原生汇总） |

LTP 包装脚本只遍历 `ltp/testcases/bin` 顶层普通文件，目录 `datafiles`、`ftrace_stress` 不执行；每个文件无参数、按文件名排序，工作目录为 `/musl` 或 `/glibc`。逐文件的路径、长度、权限和 SHA256 在调用清单里。包装脚本无条件打印 `FAIL LTP CASE`，所以不能按该字样判失败。

## 分析边界

- 来源分 `external_log` 与 `recorded_capture`。外部日志的 subject/platform 固定 `unknown`、capture 为 `NOT_RECORDED`、input_integrity 为 `UNVERIFIED`，即使逐项全 PASS 也不能验收通过。
- 采集状态、完整性、协议、段和判分分字段保存。截断段保留已观察值，但 acceptance 仍 BLOCKED，`accepted_pass=0`。
- 非 LTP 使用冻结的官方/团队结果适配器；适配器崩溃、超时、非法 JSON、重复名称或非有限数值记 JUDGE_ERROR。
- LTP 保存每次尝试、退出码、TPASS/TFAIL/TBROK/TCONF/TWARN 事件和原生汇总；TINFO/TDEBUG 只作信息。跳过、警告、TBROK、非零退出和无 TPASS 都不通过；重复尝试不能被后次成功覆盖。未知名称单列且阻断验收，不进入分母。
- 同一运行包用 J1/J2 重判时，`run_id`、采集清单哈希、PROFILE 哈希、selection 和分母不变；CRITERIA 身份与结论可以不同。J2 的额外 LTP 汇总要求只影响分析，不重跑 QEMU。

## 15 份历史结果对照

`tests/test001/fixtures/historical/` 保存 7 份脱敏串口日志和 15 份旧摘要；原始证据归档 SHA256 和逐文件原始/夹具哈希在 `index.json`。逐项状态全部一致：

| 组/libc | 结果 | 说明 |
| --- | --- | --- |
| basic glibc/musl | 32 项一致 | musl 仍按旧镜像的已知构建问题记失败 |
| lua glibc/musl | 9 项一致 | 参考历史全 PASS，但来源仍是 external_log |
| busybox glibc/musl | 53 项一致 | judge 输出多出 2 个镜像实际执行项，作为 extras 单列并阻断验收 |
| libctest glibc/musl | 220 项一致 | 镜像缺少的项按失败保留 |
| iozone/libcbench | 20/27 项一致 | 性能结果只作参考，不合成总分 |
| lmbench | 24 项一致 | 历史日志在 END 前挂住，段为 TRUNCATED、协议 INCOMPLETE |
| cyclictest/iperf/netperf/unixbench | 4/6/5/27 项一致 | 适配器输出与旧摘要逐项一致 |

busybox 的两个 extras 是这次重放发现的诊断差异，不是把旧摘要改成分数。

## 真实 LTP 样本

`tests/test001/fixtures/ltp/reference-ltp-sample-2026-11-18.log` 是从冻结镜像和 LTP 20240524 跑出的真实参考 Linux 短时样本：107 个 `RUN`、106 个完整结束，主机 120 秒超时后在 `cgroup_fj_proc` 截断。报告得到 30 个 PASS、76 个 FAIL、1 个未完成和 2734 个 MISSING；TPASS/TCONF/ANSI 颜色和旧式 `TAG 1 TPASS :` 格式都被解析。它只用于适配器正反例，不是完整批次，也不是 FocOS 成绩；运行条件的差异记在 `fixtures/ltp/index.json`。

## 还没做什么

- B：构建包、内核/initramfs/基盘冻结与并行构建。
- C：QEMU 真实采集、固件副本、写时复制层和完整参考 Linux LTP 批次。
- D：campaign 预登记、24 槽编排、全套汇总与重判替换约束。
- FocOS/VF2 采集适配器、真板结果和完整的 2025 全部通过结论。

## 改分析实现后

分析器、适配器和实现清单是一体的。改代码后运行：

```bash
python3 scripts/update_test001_manifest.py
```

它会重写 `tests/test001/criteria/v1/implementation.sha256`，并更新两份 CRITERIA 的 `implementation.sha256` 与 `criteria_hash`。`--check` 只核对不写；测试会检查清单是否过期。
