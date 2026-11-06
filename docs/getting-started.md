# 第一天：运行 → 认领 → 提交

目标：看到 `PASS: BOOT-001`，然后交一个队友能快速审完的小改动。

## 1. 准备环境

需要 Linux、Git、rustup、make、Python 3、QEMU RISC-V。Ubuntu / Debian：

```sh
sudo apt-get update
sudo apt-get install -y git make python3 qemu-system-misc binutils-riscv64-linux-gnu
```

按 [rustup 官方说明](https://www.rust-lang.org/tools/install) 安装 Rust 管理器。仓库的 `rust-toolchain.toml` 固定工具链；首次运行会下载。Fedora 使用对应系统包；WSL2 可用于 QEMU 开发，板卡连接需另配。

## 2. 获取并运行

在 [团队仓库](https://github.com/MindScrath/Focos) 点击 Fork，再将下方用户名和 fork 名改成自己的：

```sh
git clone https://github.com/YOUR_GITHUB_LOGIN/Focos.git
cd Focos
git remote add upstream https://github.com/MindScrath/Focos.git
git fetch upstream
git switch -c feat/1-memory-disk upstream/main
make check
```

`origin` 是个人 fork，`upstream` 是团队仓库。分支名中的 `1` 换成认领的 Issue 编号。

成功标志：`PASS: BOOT-001`。日志：`target/evidence/boot-qemu-rv.log`。只编译用 `make build`，只跑启动流程用 `make smoke`。

| 遇到问题 | 先检查 |
| --- | --- |
| 找不到 QEMU | `qemu-system-riscv64 --version` |
| Rust 下载失败 | 网络与 rustup 安装 |
| 格式不对 | `cargo fmt --all` 后查看 diff |
| 启动超时 | 日志、完整命令与当前 SHA；不要删断言 |

## 3. 交第一份 PR

1. 从 [任务入口](backlog.md) 选一项，在 Issue 认领并约 reviewer。
2. 复制 [SPEC 模板](../specs/TEMPLATE.md)，先约定行为，再写失败测试和实现。示例见 [BOOT-001](../specs/BOOT-001.md)。
3. 运行相关检查和 `make check`，检查 diff；路径变化时更新 README 文件树。
4. `git add <本次文件>` 后提交并推送：

```sh
git commit -m "feat: add memory disk boundary checks"
git push -u origin HEAD
```

PR 目标选 `MindScrath/Focos:main`，按自动出现的模板填写。完整流程只有 [六步](collaboration.md)。当前启动成功不代表文件系统或历史测例已通过。
