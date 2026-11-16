# PR29 后续审核修复（2026-11-17）

针对 f297244 的四条 unresolved，均为实现边界遗漏。保留验证链分层，统一 manifest 校验和 lmbench 尝试记录；证据解包局部改写为隔离构建、校验后发布。

| 问题 | 修复与同类检查 |
| --- | --- |
| strict manifest 合法 JSON 但对象类型不合法 | 采集/回放共用完整结构校验，嵌套对象、选择列表、版本和 SHA256 不合法则退出 2；任何 QEMU 探测/执行与判分前停止。解析和哈希取同一字节快照。 |
| lmbench 无 trace 表头缺结果后重试可通过 | 每次表头先预留失败；trace 后首个表头合为一次，已出现数据后再遇表头算新尝试；缺失和重复不能被后续成功覆盖。 |
| Lua 原始未知名称漏记 | 扫描 Lua/busybox testcase 行，独立记录 unexpected_in_log，不依赖官方 judge。评论说官方 Lua 不返回未知项不准确：它已有 extras；本轮补全原始日志诊断。 |
| 解包复用目录可跟随符号链接 | 相同归档字节先验证再在私有目录解包；38 项校验完整才发布。拒绝目标/祖先/树内链接，已有不同内容不覆盖；复用内容一致时仅读取。 |

还补查了缓存下载、报告、受管 current 树与归档别名。报告原子替换不会修改外部硬链接；每次启动前移除旧摘要，runner 退出 1 且没有新摘要会明确失败。执行源码、清单、锁与诊断登记在回放前固定身份，期间变化则失败。

新增负例在旧实现出现 62 个失败断言和 9 个异常；后续检查再暴露 2 个表头边界失败、1 个旧摘要复用失败、2 个输入/输出别名失败。修复后 `make check mm-smoke irq-smoke` 通过 86 项专项、52 Rust、15 原有 Python 及三个内核 QEMU 检查。

历史归档及内部哈希仍匹配。15 份摘要中，13 份完全一致；busybox-glibc 与 lua-glibc（该摘要也含 busybox）仅在 busybox 组新增 `unexpected_in_log` 中的 `busybox sh -c 'sleep 5' & ./busybox kill $!`。状态、逐项判分、extras 与数量都不变。两处精确增补登记在 tests/reference-replay-updates.json，绑定固定归档与样本/组，只允许这个诊断字段；未登记变化或任何状态/分数变化仍使检查失败。报告保留 all_match=false，并以 all_expected_match=true 明示审计后的对照成立；没有把新诊断伪装成历史逐字相同。

对已提交 schema 2 Lua 串口及其既有实际构件做严格回放，glibc/musl 各 9/9。本轮没有新采集参考 Linux，没有重跑 22 次完整测量，也未覆盖旧 Release 或摘要；三个 QEMU 检查验证的是现有内核契约。逐份结果和当前源码哈希见 [validation.json](validation.json)。
