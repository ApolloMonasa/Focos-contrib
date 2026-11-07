# 来源：需要时再查

## 区域赛规则

工作区 `contest_info/` 的 2026 年 10 月原件优先于旧摘要：

| 原件 | 查什么 | SHA256 |
| --- | --- | --- |
| 2026年操作系统设计赛华东区域赛章程-1010.pdf | 赛程、组队、来源与 AI 要求 | `ec70009584de03dd1a48a4b7e9ef7d25abdce33c8edcf2280eee8d71f54da16b` |
| 2026年操作系统设计赛华东区域赛技术方案-1010.pdf | 评分、交付和 GitLab | `87aa31339caf043bc3d6a740c0aa735a2bd6411cfd6cedb370ef98555d47571d` |

计划按 12/24 提交，12/20 准备候选版。技术方案参考权重：代码 30%、文档 30%、教学 10%、工程规范 10%、演示答辩 20%。新加的 2025 测试目标是团队要求，不是官方最低门槛。

要求公开代码/文档、保留开发与来源记录，并通过指定 GitLab 提交。代码需选开源许可，文档按 CC-BY-SA 4.0；当前仓库尚待补齐许可文件，由 #10 跟进。GitHub 公有仓库不等于赛方提交已完成。

入口：[官网](https://os.xtnl.org.cn/#/index?TYPE=26OS_K)。本次网页正文未能直接读取；最新通知仍需提交前核对。个人报名材料不发布。

## 测评来源

- [2025 初赛](https://github.com/oscomp/testsuits-for-oskernel/tree/8b58dd16d26d30f7c74d48d5832d870d3051b703)：区域赛兼容基线。
- [2025 现场](https://github.com/oscomp/testsuits-for-oskernel/tree/9ea291d8535774680e88f4e6bf725d7ba156e752)：vim/gcc 来源与任务。
- [2026 决赛](https://github.com/oscomp/testsuits-for-oskernel/tree/b5ec6ef8497e1818cbdec3b54bb722f036e57972)：CAgent/BuildStorm 进阶参考。
- [官方运行框架](https://github.com/oscomp/autotest-for-oskernel/tree/d1bb3a3c4b27274e196a2648518525c1a304e339)、[测例与 judge 说明](https://github.com/oscomp/oskernel-testsuits-cooperation/tree/44fa7ac65f292580c3974a8252f533c4484ebe6b)。

完整分支比较、镜像差异与未确认项见 [审计](reference/test-audit.md)。不要把历史适配结果写成当届官方成绩。

## 实现与研究参考

- [JH7110 官方资料](https://doc-en.rvspace.org/JH7110/Product_Brief/JH7110_DS/feature_pb.html)、[2K1000LA](https://loongson.cn/EN/news/show?id=636)：设备资料入口。
- [SBI System Reset](https://github.com/riscv-non-isa/riscv-sbi-doc/blob/master/src/ext-sys-reset.adoc)：启动关机规范。
- [获奖作品索引](https://github.com/oscomp/os-competition-info/blob/main/os-kernel-winners.md)、[Starry Mix](https://github.com/Starry-Mix-THU/starry-mix)：针对具体问题阅读，记录版本、借鉴范围与本队增量。
- [HNSW](https://arxiv.org/abs/1603.09320)、[DiskANN](https://papers.neurips.cc/paper/2019/hash/09853c7fb1d3f8ee67a61b6bf4a7f8e6-Abstract.html)、[MemGPT](https://arxiv.org/abs/2310.08560)：算法与应用动机；论文性能不是 FocOS 数据。

引入第三方代码前核对许可证，在源码和设计/答辩材料注明来源。AI 辅助与人工复核见 [记录](ai-usage.md)。
