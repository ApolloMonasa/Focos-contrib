# 结果适配器来源

这些文件是冻结的判分输入，不是内核实现，也不产生 FocOS 成绩。

- 官方判据：`oscomp/oskernel-testsuits-cooperation@44fa7ac65f292580c3974a8252f533c4484ebe6b` 的 `judge/judge_basic.py`、`judge_busybox.py`、`judge_lua.py`、`judge_libctest.py`、`judge_iozone.py`，原样复制。
- 团队判据：旧 TEST-001 分支的 `scripts/judge_team/*.py` 与 `scripts/lmbench_trace.py`，原样复制。
- `inventory.py` 是本仓库新增的小适配层：从分析时生成的 `names.json` 读取冻结项，不依赖仓库外的 inventory。
- 逐文件哈希见上一级 `implementation.sha256`；修改任一文件都必须生成新的 CRITERIA revision。

上游仓库未附 license 文件。这里只用于本队比赛测例的本地判分；如上游作者有异议，应移除对应文件并重新审查 CRITERIA。
