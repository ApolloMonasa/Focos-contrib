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
- 入口先把当前实现和配置复制到私有快照，再从快照执行；报告中的实现哈希对应实际执行的代码，不依赖调用前导入的内存副本。
- 输出目录必须不存在；已存在、祖先含符号链接或写入失败都不覆盖旧结果。创建时从 `/` 开始逐级 `openat(O_NOFOLLOW)` 打开并持有目录句柄，发布阶段不再按路径重新打开。适配器输出按流式读取，超过显式字节限额即终止整个进程组；目录 fsync 失败会撤销已可见的 `report.json`。`report.json` 最后原子发布，`report.md` 的哈希记录在其中。

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

LTP 包装脚本只遍历 `ltp/testcases/bin` 顶层普通文件，目录 `datafiles`、`ftrace_stress` 不执行；每个文件无参数、按文件名排序，工作目录为 `/musl` 或 `/glibc`。逐文件的路径、长度、权限和 SHA256 在调用清单里。包装脚本无条件打印 `FAIL LTP CASE`，所以不能按该字样判失败。busybox 镜像脚本中的 `echo "bbbbbbb" >> test.txt` 计划执行两次，PROFILE 的 `planned_occurrences` 记为 2；这是计划重复，不是意外重试。

## 运行包契约

`--run` 只接受完整采集清单，而不是一张自报 `VERIFIED` 的最小 JSON：

- `run_id`、PROFILE identity、selection、subject/platform、capture 状态/退出码、serial 与 firmware 文件哈希/长度；`scripts` 必须逐项列出 selected shard 的脚本退出码，缺失或非 0 阻断；
- `build.manifest_sha256`、`kernel_sha256`、`initramfs_sha256`、`base_img_sha256`；其中 `base_img_sha256` 必须等于 PROFILE `inputs.image.uncompressed_sha256`；
- `qemu.path/sha256/version`、实际 `argv`、`started_at/finished_at`、`limits`；
- argv 严格解析：`argv[0]` 必须逐字等于 `qemu.path`；只允许冻结启动参数（`-machine virt`、`-m`、`-smp`、`-nographic`、`-no-reboot`、`-bios`、`-kernel`、`-initrd`、`-append`、`-drive`、`-device`），并必须含 `-machine/-m/-smp/-bios/-kernel/-initrd/-append/-drive/-device`；拒绝重复参数、`-blockdev` 和任何未登记参数。`-kernel/-initrd/-bios` 必须逐字等于对应只读角色路径；`-append` 固定为 `console=ttyS0 rdinit=/init`；唯一 `-drive` 必须 `if=none`、指向 `cow_layer.path`、format 与 COW 一致、不是基盘且不能 `readonly=on`；唯一 `-device` 必须是 `virtio-blk-device,drive=<COW id>`；
- `firmware.source/version/original_path`、`cow_layer.path/format` 的初始哈希/长度/创建结果及基盘绑定；
- `read_only_inputs` 必须恰好包含 kernel/initramfs/base_img/firmware/qemu 五个角色及各自前后 SHA256；
- `capture_implementation.files` 的完整相对路径和逐文件 SHA256。

字段缺失、角色重复/缺失、前后哈希与 build/firmware/qemu 身份不符，或 `input_integrity=VERIFIED` 但任一 before/after 不同，均在分析前退出 2。`CHANGED/UNVERIFIED` 的完整包仍可分析，但 acceptance 阻断。A 阶段只校验这份契约并分析；实际产出由 C 阶段采集器负责。

## 分析边界

- PROFILE 的 `judge_sha256` 只证明采集时使用的 judge 来源；CRITERIA 的 adapter 可独立版本化，`check_profile()` 不要求两者哈希相等，因此旧运行包的 `profile_hash` 不变也能按修复后的判据重判。

- 来源分 `external_log` 与 `recorded_capture`。外部日志的 subject/platform 固定 `unknown`、capture 为 `NOT_RECORDED`、input_integrity 为 `UNVERIFIED`，即使逐项全 PASS 也不能验收通过。
- 采集状态、完整性、协议、段和判分分字段保存。截断段保留已观察值，但 acceptance 仍 BLOCKED，`accepted_pass=0`。
- 非 LTP 使用冻结的官方/团队结果适配器；对 basic/libctest/lua/busybox/iozone/unixbench/iperf/netperf/cyclictest/libcbench/lmbench 段，分析器另有原始观测守卫，从段文本独立核对重复和未知名称，适配器只保留最后一次成功也不能绕过。Lua/BusyBox 的 `testcase` 行按官方判据的非锚定 `success|fail` 语法解析，尾随诊断文本仍可识别；同一名称的 fail+success 记为重复/失败，含 `testcase` 但无法唯一解析结果的行记 `MALFORMED_OBSERVATION` 并阻断。basic/libctest 逐用例校验 START/END 配对和名称一致；嵌套 START、END 无 START、END 名称错配、缺 END 都保留已见观察值并记 `MALFORMED_OBSERVATION` 阻断。官方判据能识别的非规范边界（START/END 带尾随文本、缺 `=` 等）也先报错；libctest 的 `Pass!` 必须位于当前 START/END 内，END 后的 `Pass!` 记 case 外结果，adapter 报 PASS 但没有尝试内成功证据时记 `NO_IN_ATTEMPT_PASS` 并阻断。适配器崩溃、超时、非法 JSON、重复名称或非有限数值记 JUDGE_ERROR。libcbench/lmbench 的冻结 adapter 仍只输出 PASS/FAIL；分析器从原始段文本提取 `group.metrics`，保留 name/label、数值、单位和行号，并在 report.md/JSON 中展示。
- LTP 保存每次尝试、退出码、TPASS/TFAIL/TBROK/TCONF/TWARN 事件和原生汇总；TINFO/TDEBUG 只作信息。跳过、警告、TBROK、非零退出和无 TPASS 都不通过；重复尝试不能被后次成功覆盖。已有原生汇总与事件冲突时 J1/J2 都阻断；J2 另外要求现代汇总的 passed/failed/broken/skipped/warnings 五个字段完整，缺字段记 `summary_incomplete` 并阻断。调用顺序必须匹配冻结列表，否则记 `LTP_ORDER_MISMATCH`。组内 invocation 外的结果事件保留段内/全局行号并记入 `native.unattributed`；组 END 之外的 LTP 结果事件保留全局行号并进入协议错误；组外的 `testcase ... success/fail`、basic/libctest 边界、`Pass!`、team 标记和性能结果行也保留行号并作为非 LTP 协议错误阻断。二者都阻断验收。未知名称单列且阻断验收，不进入分母。
- 同一运行包用 J1/J2 重判时，`run_id`、采集清单哈希、PROFILE 哈希、selection 和分母不变；CRITERIA 身份与结论可以不同。J2 的额外 LTP 汇总要求只影响分析，不重跑 QEMU。

## 15 份历史结果对照

`tests/test001/fixtures/historical/` 保存 7 份脱敏串口日志和 15 份旧摘要；原始证据归档 SHA256 和逐文件原始/夹具哈希在 `index.json`。逐项状态全部一致：

| 组/libc | 结果 | 说明 |
| --- | --- | --- |
| basic glibc/musl | 32 项一致 | musl 仍按旧镜像的已知构建问题记失败 |
| lua glibc/musl | 9 项一致 | 参考历史全 PASS，但来源仍是 external_log |
| busybox glibc/musl | 53 项一致（当前口径） | 计划重复 `bbbbbbb` 允许两次成功；当前 extras/阻断行为正确，但镜像/历史命令表是 55 条，审计未闭合，见 [#48](https://github.com/MindScrath/Focos/issues/48) |
| libctest glibc/musl | 220 项一致 | 镜像缺少的项按失败保留 |
| iozone/libcbench | 20/27 项一致 | 性能结果只作参考，不合成总分 |
| lmbench | 24 项一致 | 历史日志在 END 前挂住，段为 TRUNCATED、协议 INCOMPLETE |
| cyclictest/iperf/netperf/unixbench | 4/6/5/27 项一致 | 适配器输出与旧摘要逐项一致 |

busybox 的两个 extras 是这次重放发现的诊断差异，不是把旧摘要改成分数。

**BusyBox 清单审计未闭合（2026-11-18）。** 冻结镜像 `/musl|glibc/busybox_cmd.txt` 为 55 行、54 个唯一命令（SHA256 `e970e5ec…d623`），包含 `printf "abc\n"`、`sh -c 'sleep 5' & ./busybox kill $!` 和两次 `bbbbbbb`，不含 `kill 10`；镜像脚本哈希与 PROFILE 记录一致。15 份历史日志的 BusyBox 段也是 55 条并逐项与镜像对应，因此不是历史日志版本不同。当前 PROFILE 的 53 条与官方 judge 基线一致，却含 `kill 10`、缺上述两项；所以当前 extras/阻断是正确的，但调用清单需要修订。修订会同时影响 profile/CRITERIA 身份、shard 范围和历史对照，单独改 PROFILE 还会让官方 judge 继续输出 `kill 10` extra；具体决策见 Issue #48。

## 真实 LTP 样本

`tests/test001/fixtures/ltp/reference-ltp-sample-2026-11-18.log` 是从冻结镜像和 LTP 20240524 跑出的真实参考 Linux 短时样本：107 个 `RUN`、106 个完整结束，主机 120 秒超时后在 `cgroup_fj_proc` 截断。报告得到 30 个 PASS、76 个 FAIL，其余 2734 项为 MISSING（其中 1 项是 `cgroup_fj_proc` 的 INCOMPLETE_ATTEMPT，另 2733 项为 NO_RESULT；30+76+2734=2840）；TPASS/TCONF/ANSI 颜色和旧式 `TAG 1 TPASS :` 格式都被解析。它只用于适配器正反例，不是完整批次，也不是 FocOS 成绩；运行条件的差异记在 `fixtures/ltp/index.json`。

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
