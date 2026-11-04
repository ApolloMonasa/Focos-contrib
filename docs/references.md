# FocOS 资料来源与规则边界

核对日期：2026-11-04。资料引用与团队实施建议分开；比赛临时通知优先于历史网页或工作区摘要。

## 2026 华东区域赛

工作区已有两份 2026 年 10 月版原始 PDF，保留原路径不覆盖：

- `contest_info/2026年操作系统设计赛华东区域赛章程-1010.pdf`：第 2 页赛程、第 3 页组队、第 4 页来源和 AI 使用要求。SHA256：`ec70009584de03dd1a48a4b7e9ef7d25abdce33c8edcf2280eee8d71f54da16b`。
- `contest_info/2026年操作系统设计赛华东区域赛技术方案-1010.pdf`：第 2–3 页评分，第 3–5 页过程资料与托管平台提交要求。SHA256：`87aa31339caf043bc3d6a740c0aa735a2bd6411cfd6cedb370ef98555d47571d`。

上述原件比工作区 10 月 2 日的 Markdown 摘要更新：提交截止应按 12 月 24 日安排，不能沿用去年一月截止的时间线。区域赛每队 1–3 人，至少一名指导教师；12 月 1 日后仅可减员。具体参赛资格按当届章程办理。

技术方案要求保留来源/基础版本和独立增量说明，在指定 GitLab 提交，并按比赛要求公开代码与文档。2026-11-04 经仓库负责人明确授权，GitHub upstream `MindScrath/Focos` 已改为公有，使用公有 fork 和 PR 协作。这不代表已经完成赛方 GitLab 提交、参赛登记或来源审查；队员账号仍待确认。

代码开源许可须根据第三方组件和团队选择确定；资料遵循赛方规定。任何借鉴都记录具体来源/版本与使用范围。AI 使用记录需出现在相关过程记录及设计/答辩材料中。团队报名敏感资料不进入作品发布物。

赛事入口：[官网](https://os.xtnl.org.cn/#/index?TYPE=26OS_K)，[希冀比赛列表](https://course.educg.net/pages/contest/index.jsp?contestCID=0&my=false&stat=3)。

## 测例与判分的一手资料

- [全部测试分支](https://github.com/oscomp/testsuits-for-oskernel/branches)；本次 19 个分支完整 SHA 见仓库锁定清单。
- [pre-2024 FAT32 基线](https://github.com/oscomp/testsuits-for-oskernel/tree/0de5d9b11cd3b5d7f75436460bd826be5e989a5c)。
- [final-2023 构建脚本](https://github.com/oscomp/testsuits-for-oskernel/blob/e8d502ceb8fa36c6564e53777d06f87c1945802a/Makefile)，确认 FAT32 镜像制作。
- [pre-2025 评测要求](https://github.com/oscomp/testsuits-for-oskernel/blob/8b58dd16d26d30f7c74d48d5832d870d3051b703/README.md)，包括 Ext4、双架构产物和串行运行协议。
- [final-2025 存储与中断专项](https://github.com/oscomp/testsuits-for-oskernel/blob/aa84a2b0cf17960fdcd1dec1c0d036cf3827b45b/README.md)。
- [final-2026 CAgent 与 BuildStorm](https://github.com/oscomp/testsuits-for-oskernel/blob/b5ec6ef8497e1818cbdec3b54bb722f036e57972/README.md)。这些是 2026 历史赛题，不能充当 2027 正式规则。
- [官方自动测试框架](https://github.com/oscomp/autotest-for-oskernel/tree/d1bb3a3c4b27274e196a2648518525c1a304e339)。
- [测例解释与 judge](https://github.com/oscomp/oskernel-testsuits-cooperation/tree/44fa7ac65f292580c3974a8252f533c4484ebe6b)。其中 basic 说明指出测试通过 Linux syscall 子集访问内核；它不等于完整 POSIX 验证。

## 借鉴优秀作品的方式

[oscomp 官方获奖列表](https://github.com/oscomp/os-competition-info/blob/main/os-kernel-winners.md)列有 FTL OS、Titanix、Phoenix、Chronix、Starry Mix 等作品。可以据此选择参照项目，但不能据获奖名称推断某项技术就是其获奖原因。

本次读取了 [Starry Mix 的 README](https://github.com/Starry-Mix-THU/starry-mix)：其明确标明基于 starry-next/ArceOS，并提供比赛文档、幻灯片和演示视频。可借鉴其来源披露和交付物组织方式；本次没有导入其内核代码，也没有验证其性能数据。其他获奖作品作为后续有明确问题时的阅读入口，避免一次泛读多套内核耽误自己的闭环。

每次参考应回答一个具体问题：接口怎样隔离、测试如何复现、缓存怎样保证正确；记录对应文件/版本、可复用思路和 FocOS 自己的实现差异。从零实现不意味着忽略规范和已有经验，也不应把复制的实现算作独立增量。

## 硬件与算法

- [StarFive JH7110 官方简介](https://doc-en.rvspace.org/JH7110/Product_Brief/JH7110_DS/feature_pb.html)：RV64GC、SDIO/eMMC 控制器等；详细寄存器与板级启动资料另查工作区 Hardware 原件及 rvspace。
- [龙芯官方 2K1000LA 说明](https://loongson.cn/EN/news/show?id=636)：2K1000LA 使用 LoongArch 内核。本队已确认实物是该架构的星云板；避免与旧 2K1000 混淆。
- [RISC-V SBI System Reset 规范](https://github.com/riscv-non-isa/riscv-sbi-doc/blob/master/src/ext-sys-reset.adoc)：BOOT-001 的关机接口来源。
- [HNSW 原始论文](https://arxiv.org/abs/1603.09320)：算法背景。FocOS 的磁盘文件布局、内存限制与系统实验属于待实现方案，不能直接引用论文中的算法成绩作为 FocOS 成绩。
