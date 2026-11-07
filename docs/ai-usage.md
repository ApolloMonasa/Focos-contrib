# AI 辅助记录

工具：Codex。AI 起草不等于人工审阅或板上验收；每个 PR 仍须提供实际测试和队友批准。原会话保留在本地，发布摘录先去除个人信息和凭据。

## 已发生的工作

| 日期 | 辅助内容 | 实际验证与边界 |
| --- | --- | --- |
| 11/4 初始化 | 赛规/历史分支审计、启动 SPEC、Rust 内核与 CI | 冒烟先因 ELF 缺失失败，再通过 make check；[初始 CI](https://github.com/MindScrath/Focos/actions/runs/41846818776)。未跑官方测例与性能 |
| 11/4 协作 | 按负责人要求改公有 fork 流程、保护配置和成员指南 | 本地启动回归与 GitHub 配置核对；未确认的成员/功能不计完成 |
| 11/4 审阅修复 | 持久化写作约定；移除入门页会随 PR 合入失效的提示 | 检查事实、链接和 diff；人工需核对 unsafe、ABI 和规格 |
| 11/5 架构与文件树 | 目标分层图、路径用途及维护规则 | 检查渲染、路径与链接；目标图不是实现成绩 |
| 11/5 应用定位 | AI 记忆检索动机、HNSW 与两赛段范围；项目外汇报材料 | 文档/展示检查；未实现 AI 或 HNSW，不把论文结果算本项目成绩 |
| 11/6 目标重塑 | 2025 非 LTP 全套与 vim/gcc/make；重写指南、SPEC、Issue/PR，增加短模板和 SOP | 审官方分支、脚本和发布元数据；检查文档/清单，实际运行记录见 PR #17；未运行历史测试或工具 |
| 11/5–11/6 VisionFive2 | Codex 查阅 U-Boot 启动与 ELF 装载实现，起草 BOARD-001、单 hart 入口、板级构建、模拟检查与上板指南。用户操作串口和 U-Boot，Codex 整理实测记录和固定复现版本 | `make check` 和模拟 `argc=0/2` 的 `make vf2-check` 通过。用户在真板观察到 SBI 启动标记。队友冷启动复现及 ABI、链接布局人工审阅待完成。[PR #16](https://github.com/MindScrath/Focos/pull/16) · [实测记录](evidence/BOARD-001-20261105.txt) |
| 11/8 VisionFive2 审阅修复 | Codex 合并新版文档，将启动提示和结束动作移入 HAL，补齐串口工具安装说明 | `make check` 与 `make vf2-check` 通过。新 ELF 待真板冷启动复验。[PR #16](https://github.com/MindScrath/Focos/pull/16) |

初始化提交：`46222643e3c2c2e25521684b0d30d37ab20abee6`。此次规划未导入第三方内核实现。首次真板启动的实现和证据在 [PR #16](https://github.com/MindScrath/Focos/pull/16)独立审阅，不能以本文代替板上日志。

## 后续怎样记

新增一条即可：AI 做了什么、实际验证了什么、哪些仍需人工确认。来源指向固定版本，证据指向对应提交。无需公开私人账户设置、完整聊天或无关个人资料。
