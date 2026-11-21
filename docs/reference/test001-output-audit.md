# TEST-001：测试输出规则复查

本页复查 TEST-001、解析契约、阶段契约、词法设计和 A0/A1 参考页中关于测试脚本输出的约定。结论：Lua 的真实样本已有依据；LTP 的无冒号汇总、结果行前缀、旧式输出和退出语义此前核查不足。以下修正规则，不继承旧 SPEC 或 judge 的格式假设。

## 证据怎样分级

- **比赛镜像真实执行**：已有双 libc Lua/QEMU 样本，可证明对应环境的原包装器输出；仍不是 FocOS 成绩。
- **固定源码 + 输出函数实验**：本轮核对比赛仓库 `8b58dd16d26d30f7c74d48d5832d870d3051b703` 的全部 12 个组包装脚本，以及 LTP 新旧 C/shell 输出库。运行了 18 个输出/退出实验；原生测试程序、内核行为、依赖闭合没有执行。
- **设计规则或合成反例**：例如遇到跨组结构阻断、用 NBSP 破坏结果、重复失败后成功，是我们的判据/故障注入，不是比赛脚本必然生成的输出。

[实验索引](../../tests/test001/fixtures/output-audit/index.json)逐文件锁定本轮源码哈希；[输出](../../tests/test001/fixtures/output-audit/samples.json)以十六进制保存 stdout/stderr 原字节和进程退出状态。`probe`、`probe.c:7` 和消息里的引用由实验指定，不是比赛用例名或测例成绩。

## 已纠正的规则

| 原说法或缺口 | 固定来源与实际观察 | 修正 |
| --- | --- | --- |
| 用 `failed: 999` 示例代表原生汇总 | [新 C 库](https://github.com/oscomp/testsuits-for-oskernel/blob/8b58dd16d26d30f7c74d48d5832d870d3051b703/ltp-full-20240524/lib/tst_test.c#L862-L900)、[新 shell 库](https://github.com/oscomp/testsuits-for-oskernel/blob/8b58dd16d26d30f7c74d48d5832d870d3051b703/ltp-full-20240524/testcases/lib/tst_test.sh#L39-L98)输出 `Summary:` 和无冒号的 passed/failed/broken/skipped/warnings 计数 | 原生正例采用无冒号格式；冒号形式仅作为历史合成反例保留，不能当作输出来源 |
| 只把行首 TPASS 等列为保留标记 | 新 C 库输出 `文件:行号: TPASS: 消息`；[旧 C 库](https://github.com/oscomp/testsuits-for-oskernel/blob/8b58dd16d26d30f7c74d48d5832d870d3051b703/ltp-full-20240524/lib/tst_res.c#L282-L382)输出 `测试名 序号 TPASS : 消息`；新旧 shell 为 `测试名 序号 TPASS: 消息` | 后续 Lua 修复拟补登记这些外层结果字段；消息内的 TPASS/testcase 不递归识别。只是识别不支持的结构，不判 LTP 成绩 |
| 旧式 LTP 必有 Total Tests / Passed / Failed / Not Supported | 旧 C `tst_exit`、旧 shell `tst_exit` 没有统一汇总；本轮实验只有逐条结果，没有 Total Tests | 撤回“旧式统一汇总”断言。此历史标签未建立比赛来源；继续作为明确列出的兼容拒绝标记，不据此开放判分 |
| 容易把 FAIL LTP CASE 当失败判词 | [镜像脚本](../../tests/test001/ltp-audit-inputs/musl-ltp_testcode.sh)在每次子程序后无条件打印它；退出 0 和 1 的替身均实测出现，组脚本最后退出 0 | 它是包装器结束/状态行，数字才是 shell 观察的状态；整组退出 0 不证明子项成功。信号含义不能只从整数补猜 |
| 用退出 0 推断没有跳过 | 新旧 shell 实测 TPASS+TCONF 同时出现但退出 0；全 TCONF 退出 32 | 原生事件、计数和退出值分开保留。团队判据“有跳过则不通过”是主动更严格的要求，不是 LTP 的原生退出定义 |
| 默认所有 LTP 库有同一退出位/计数语义 | 注入五类计数的新 C 退出函数实验为 7；旧 C 同时记录五类标志为 39。旧 C 还支持 NOPASS/DISCARD，并可压缩/隐藏结果 | 按实际调用的库/API/输出模式冻结映射，不能用标志头文件代替格式和退出逻辑审计，也不能把日志行数直接当作原生断言数 |
| 把裸 TINFO/TDEBUG 示例当所有库通用格式 | 新 C 有带位置的 TDEBUG，受调试开关控制；旧 C 不支持 TDEBUG，新旧 shell 的结果接口也不接受它 | 示例显式标 synthetic；各 API 的信息规则分别登记，不将消息中的结果词当事件 |
| 将 Lua 的五种颜色序列视为全套输出支持 | [颜色定义](https://github.com/oscomp/testsuits-for-oskernel/blob/8b58dd16d26d30f7c74d48d5832d870d3051b703/ltp-full-20240524/include/tst_ansi_color.h)及实验包含 `ESC[1;32m`、`1;35m`、`1;34m`，而 Lua 只允许五种已登记序列 | 不扩大全局去色规则。当前 Lua 对额外颜色报错；实现 LTP 前必须单独冻结颜色开关、完整序列和样本 |
| 源码组标记和镜像组标记混为一谈 | 固定源码包装脚本通常只有 `lua`/`ltp`；已锁镜像脚本和 Lua 实测使用 `lua-musl`/`ltp-musl` 等 | 当前接口以镜像语法为准；源码差异不得静默正常化，也不能从请求参数补造缺失 libc |

## 全部组的输出依据与边界

以下固定源码入口均位于同一提交的 [scripts](https://github.com/oscomp/testsuits-for-oskernel/tree/8b58dd16d26d30f7c74d48d5832d870d3051b703/scripts)。表中“源码”只表示读过实际输出语句，不表示运行过对应 benchmark 或完成镜像版本核对。

| 组 | 已核对的输出来源 | 可确认与尚不能确认的部分 |
| --- | --- | --- |
| Lua | `lua/lua_testcode.sh`、`lua/test.sh`；锁定镜像脚本、九份输入及真实样本 | 九条 success、缺文件 fail、包装器退出与 Lua 进程退出不同均有实测。success 不证明脚本内所有逻辑断言成立 |
| BusyBox | `busybox/busybox_testcode.sh`、镜像原样脚本/命令表 | 源码打印 `testcase busybox 命令 success/fail`，false 是特殊例外；命令可能含空格且有重复。实际组输出/新调用身份仍未闭合，不能套用 Lua 的单 token NAME |
| LTP | `ltp/ltp_testcode.sh`、镜像脚本、新旧 C/shell 输出库 | 本页列出已核对的形式和函数实验；没有完整比赛 LTP 用例运行或镜像 ELF 到源码 API 的逐项绑定，尚不能开放 LTP 判分 |
| basic | `basic/basic_testcode.sh` 调用 `basic/run-all.sh` | 包装层只确认组边界，不能从包装器推断每个内核测试的 Pass! 含义；逐程序语法/真实样本待审 |
| libc-test | `libctest/libctest_testcode.sh` 调用 run-static.sh/run-dynamic.sh | 组边界已核对；每个测试和运行器输出、缺项及退出映射未闭合，不从旧 judge 键复制格式 |
| libc-bench | `libcbench/libcbench_testcode.sh` 执行 libc-bench | 包装层只有组边界；指标名称、单位、成功条件仍需逐项核对程序和实际样本 |
| cyclictest | `cyclictest/cyclictest_testcode.sh` | begin/end 包含场景名与 success/fail；kill hackbench 失败明确写 `fail, ignore STRESS result`。具体指标/环境和清理实测未完成，不能只看 end success |
| iperf | `iperf/iperf_testcode.sh` | begin/end 和客户端退出状态，不足以证明服务端启动及流量指标有效；逐场景真实样本待审 |
| netperf | `netperf/netperf_testcode.sh` | begin/end 包装返回状态；服务端、指标表格和场景语法待实际运行核对 |
| iozone | `iozone/iozone_testcode.sh` | 输出测量类别并执行多组参数；表格数值/单位来自程序，不能仅凭脚本标签宣称完整语法 |
| lmbench | `lmbench/lmbench_testcode.sh` | latency/bandwidth 等类别和一个 lmdd label；具体匿名数字的上下文、单位仍待样本，不把旧解析器当定义 |
| unixbench | `unixbench/unixbench_testcode.sh` | 管道提取 COUNT 并打印 `Unixbench 名称 test(单位): 数值`，lps/MFLOPS/KBps/lpm 由脚本区分；管道末端成功不证明原程序完成，原始 COUNT 和失败样本仍需核对 |

额外检查了源码 `scripts/test_all.sh`：LTP 调用在那里被注释，不能把它当作本项目包含 LTP 的完整执行入口；本项目后续采集需要显式冻结自己的调度清单。源码脚本和已锁镜像脚本不同，表中没有替未审计的其他组建立镜像身份等价关系。

所有组都受“先有该组真实正例、固定来源与错误例，再实现语法”的门槛约束。未知格式不能临时用宽泛关键字扫描补成支持；指标/旧式汇总没有来源时撤回确定性描述，列为未支持。当前 Lua 的拒绝清单也不是任意其他组输出的全集，不承诺从所有普通文本推断来源。

## 实验怎样复现

下载索引 `sources` 的每个原样文件（本地 gh 授权）：

```bash
gh api 'repos/oscomp/testsuits-for-oskernel/contents/PATH?ref=8b58dd16d26d30f7c74d48d5832d870d3051b703' --jq .content | base64 -d > SOURCE_ROOT/PATH
python3 -B tests/test001/fixtures/output-audit/probe.py SOURCE_ROOT NEW_OUT
python3 -B -m unittest discover -s scripts -p 'test_test001*.py'
```

PATH 替换为索引中的相对路径，先创建父目录；配方会核对所有源码及两份镜像包装器的哈希，输出目录必须不存在。需要本机 cc、pthread、/bin/sh、/bin/bash。索引记录本次编译器、shell 和 Python；产物应逐字节比对 samples.json 的两个输出流及退出值，不能只看配方退出 0。

- C 实验从原文件按函数边界提取 `print_result/do_exit`、`tst_print/tst_exit`，正文不改；编译时提供明确的计数状态、类型名称映射、清理/提示替身，颜色函数使用原文件。它证明受控输入下的格式和退出函数行为，不验证计数累加、信号、多进程、清理或完整 API。TDEBUG 直接调用输出函数，不证明默认配置会显示调试信息。
- shell 实验 source 原库和原依赖，只调用信息/成功/跳过与退出接口；不调用完整 tst_run，也没有运行需设备、挂载或网络的测例。
- 包装器实验使用锁定镜像脚本，子程序替身只返回 0/1。用于验证包装文案和退出传递，不计为实际 LTP 调用。
- 本 PR 的测试仅用手写期望检查输出实验的原字节和退出状态，不引入解析器。后续 #56 再使用这些字节构造 Lua 异常输入，检查错误归属且不虚构 Lua 调用；这种组合不代表实际比赛日志已混入这些行。

## 防止再沿用未经核查的示例

新增、修改输出规则时，必须同时登记：固定生产者、源码位置、样本类别、执行环境、原字节、字段边界和独立期望。SPEC 示例标明来自实际输出还是合成反例；旧 judge/旧 SPEC 只提供待查线索。输出函数实验不能代替该组真实调用正例，仍未核实的规则不得宣称冻结或继续扩大实现。
