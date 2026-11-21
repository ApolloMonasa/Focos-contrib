# TEST-001：测试输出规则复查

本页复查 TEST-001、解析契约、阶段契约、词法设计和 A0/A1 参考页中关于测试脚本输出的约定。结论：Lua 的真实样本已有依据；LTP 的无冒号汇总、结果行前缀、旧式输出和退出语义此前核查不足。以下修正规则，不继承旧 SPEC 或 judge 的格式假设。

## 证据怎样分级

- **比赛镜像真实执行**：已有双 libc Lua/QEMU 样本；本轮扩展实验及其适配/失败边界见[全组复查](test001-benchmark-output-audit.md)。均不是 FocOS 成绩。
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

## 全部组的复查结果

后续按负责人要求扩展到全部 12 组的实际打印代码、镜像程序和参考 Linux 实验，见[全组 benchmark 复查](test001-benchmark-output-audit.md)。该页取代本页初轮仅检查包装脚本的覆盖表；除 LTP 格式外，还发现 libc-test 固定返回 1、UnixBench 标签与实际单位冲突、目录标签与 ELF ABI 不一致，以及包装器掩盖失败等问题。

固定源码 `scripts/test_all.sh` 的 LTP 调用被注释，不能作为本项目完整执行入口。24 份镜像组脚本与固定源码的差异已逐字节核对，只有 libc 组标记后缀；ELF 的逐项源码构建身份仍未建立。

所有组仍须先有真实正例、固定来源与错误例，再实现语法。未知格式不能临时用宽泛关键字扫描补成支持；当前 Lua 的拒绝清单不是任意其他组输出的全集。

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
