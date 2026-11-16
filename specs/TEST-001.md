# TEST-001：串口日志驱动的测试执行与判分

关联：[Issue #8](https://github.com/MindScrath/Focos/issues/8) · 状态：修订待审

## 要做到什么

冻结 2025 初赛非 LTP 的 11 组、427 项输入，把串口日志转换成可追溯的逐项结果。参考 Linux 用于校准判分，不计为 FocOS 成绩。构建、runner、团队判据和验证入口随本 PR 一起交付。

## 行为约定

- **输入与来源**：源码、镜像和官方 judge 以 `tests/inventory.json` 为准；参考环境与历史证据以 `tests/reference.lock.json` 为准。记录输入、原始日志、判据及执行代码的 SHA256，官方 judge 必须匹配冻结文件。原始日志逐字节保留。
- **构建**：默认校验镜像和 APK；固定 cpio 元数据、gzip 时间戳和文件名。相同输入、参数及压缩工具链，在不同时间和目录构建得到相同内核/initramfs 哈希。显式允许未冻结输入时，manifest 必须标记；不能作为冻结环境的证据。
- **环境准备**：所有必需的模块、挂载、目录、链接、权限、配置、网络及 chroot 预检失败，输出 `REF_FAIL <stage>` 并停止，不执行后续组。测试脚本非零退出单独记录，不冒充环境失败或成功。
- **组切分**：严格配对 `#### OS COMP TEST GROUP START/END <group>-<libc> ####`；不带后缀时用必填的 `--libc`。重复、嵌套、错配标记是 `LOG_ERROR`，禁止选择一段覆盖其他段；EOF 前未结束是 `TIMEOUT`。
- **判分**：固定官方或团队 Python judge 用 stdin 接收组段，stdout 返回 JSON 列表；不设 fallback。非法字段、重复名字、非有限数值、非法计数、崩溃、超时均为 `JUDGE_ERROR`。官方 `total` 兼容为 `all`；iozone 按有效正 `result` 判完成。
- **判据**：团队性能解析器只接受约定格式、单位和有效数值。错误消息中的关键词、数字、耗时或部分输出不构成成功。重复/冲突结果不能用后一次成功覆盖失败。
- **状态**：`PASS` 仅限清单内每项 `pass == all > 0`；部分断言通过为 `PARTIAL`，无通过为 `FAIL`。缺组为 `NOT_RUN`，缺项为 `MISSING`（0 分）。另列 `TIMEOUT`、`JUDGE_ERROR`、`LOG_ERROR`、`ENV_ERROR`、`CAPTURE_ERROR`；这些均不能通过。环境错误优先于其他状态。
- **参考协议**：新 init 输出版本、准备、运行及退出码标记；严格模式检查顺序、完整性和所选组，环境错误使结果失效。旧日志只能显式使用 legacy 模式，标记环境未经新协议验证；不据此声称新构建通过。详见[参考运行说明](../scripts/ref/README.md)。
- **输出**：`summary.json`、`summary.md`、原始 `serial.log`、`sections/<group>-<libc>.log`；额外 judge 名称与未知用例另列，不进入分母。每项保留 `name/status/all/pass/score`，不同单位的分数不汇总。
- **退出码**：0 = 所选组全部通过且运行有效；1 = 非通过或运行无效；2 = 参数、文件或清单错误。捕获命令非零退出不得报告通过；超时终止整个子进程组。

## 怎样验收

| 场景 | 入口 | 应看到什么 |
| --- | --- | --- |
| 环境故障注入 | `make test-test001` | 每个必需步骤失败均停止；无后续执行/完成标记 |
| 重复构建与缺模块 | 同上 | 改时间/输出名哈希不变；缺必需模块、错误 release 构建失败 |
| 判分负例 | 同上 | 错误文本、错单位、NaN/Inf、非法计数、重复结果均不通过 |
| 执行与日志负例 | 同上 | 非零退出、超时、错 libc、嵌套/重复组、环境错误均不通过；原始字节不变 |
| 清单一致性 | `python3 scripts/sync_test_plan.py --check` | 计划、生成表格与冻结清单一致；FocOS 执行状态不冒填 |
| 历史日志回放 | `make reference-replay` | 校验归档及内部哈希，回放 15 份结果；差异必须显式列出，禁止改旧证据迎合新判据 |
| 工程回归 | `make check mm-smoke irq-smoke` | 新检查和现有内核验收均通过 |

输入和判据细节见 [test-inventory](../docs/reference/test-inventory.md)，历史测量见 [reference-run](../docs/reference/reference-run.md)。FocOS 用户态和真板执行仍依赖 #2/#4/#5/#7/#22/#23/#24。本改动不涉及 HAL/ABI/unsafe/持久化语义；普通 PR 需一名非作者批准，CI 必过集合变化另需一名成员确认。
