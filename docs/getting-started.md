# 第一次参与 FocOS

目标是先看到一次成功启动，再提交一个队友能审完的小改动。第一天不需要接板子，也不需要实现一个完整模块。

## 1. 准备工具

建议先用 Linux；Windows 成员可以用 WSL2。需要 Git、rustup、GNU make、Python 3 和 `qemu-system-riscv64`。

Ubuntu / Debian 的系统工具可这样安装（其他发行版使用对应包管理器）：

```sh
sudo apt-get update
sudo apt-get install -y git make python3 qemu-system-misc
```

Rust 请先按 [官方 rustup 安装说明](https://www.rust-lang.org/tools/install) 安装 rustup。项目固定 Rust 1.99.0，进入仓库后 rustup 会按 `rust-toolchain.toml` 获取工具链、组件和目标。

## 2. 获取代码

在 GitHub 打开 [上游仓库](https://github.com/MindScrath/Focos)，点击 Fork，创建自己的公有 fork。然后运行下面命令，先把 `YOUR_GITHUB_LOGIN` 换成自己的用户名：

```sh
git clone https://github.com/YOUR_GITHUB_LOGIN/Focos.git
cd Focos
git remote add upstream https://github.com/MindScrath/Focos.git
git fetch upstream
git switch -c feat/1-memory-disk upstream/main
make check
```

这里的分支名只是 #1 的例子；认领其他任务时换成对应编号和主题。`origin` 是你的 fork，`upstream` 是团队仓库。

**目前入门文档还在 PR #14 等待合入。** 如果 main 上暂时看不到这些文档，可先阅读 [PR #14 的文件](https://github.com/MindScrath/Focos/pull/14/files)；main 已经能执行 `make check`。

## 3. 判断是否跑通

`make check` 依次做格式检查、clippy 检查、裸机构建和 QEMU 启动验证。成功时会出现 `PASS: BOOT-001`。

| 需要什么 | 命令或路径 |
| --- | --- |
| 只编译 | `make build` |
| 编译并跑启动检查 | `make smoke` |
| 串口与 QEMU 版本记录 | `target/evidence/boot-qemu-rv.log` |
| 内核 ELF | `target/riscv64gc-unknown-none-elf/release/focos-kernel` |

常见问题：找不到 QEMU，先运行 `qemu-system-riscv64 --version` 检查安装；Rust 下载失败，检查网络并重试工具链安装；格式失败，运行 `cargo fmt --all` 后查看 diff；启动超时，贴出上述串口日志和完整命令，找队友一起定位。不要通过删除断言让检查变绿。

当前基线只验证 QEMU 的启动和关机，不能证明真板、UART 驱动或文件系统已经可用。

## 4. 找到要改的位置

| 路径 | 用途 |
| --- | --- |
| `kernel/src/main.rs` | 内核入口 |
| `kernel/src/hal.rs` | 硬件抽象入口 |
| `kernel/src/arch/riscv64/` | 汇编入口、链接脚本和 RISC-V 操作 |
| `specs/BOOT-001.md` | 第一份功能规格，写新 spec 时可参考它的粒度 |
| `scripts/smoke.py` | 启动检查脚本 |
| `tests/` | 外部测例版本与清单，目前不是已运行的测试集 |

## 5. 提交第一个小改动

在 [任务入口](backlog.md) 选择任务，到 Issue 说明你准备先做哪一小步、何时交付，请一名队友做 reviewer。先把预期行为写进 spec，经审阅后补一个会失败的测试，再写实现。

提交前运行相关测试和 `make check`，检查 `git diff`，确认没有日志、镜像或凭据。用 `git add <本次文件>` 暂存后执行：

```sh
git commit -m "feat: add memory disk boundary checks"
git push -u origin HEAD
```

在 GitHub 创建 PR：目标选 `MindScrath/Focos:main`，来源选自己的 fork 和分支。写清关联 Issue、spec、做了什么、怎样验证；邀请队友审阅。GitHub 登录或推送认证使用自己的凭据，不要把凭据放进命令示例或 Issue。详细规则见 [协作流程](collaboration.md)。
