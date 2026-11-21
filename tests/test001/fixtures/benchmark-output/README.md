# 12 组输出证据与复现

本目录记录 2026-11-21 的八次参考 Linux 来宾实验。它用于核对输出特征及反例，不是生产 runner、比赛成绩或已冻结的解析语法。结论和缺口见[逐组对照](../../../../docs/reference/test001-benchmark-output-audit.md)。

- `index.json`：原盘/内核/固件身份、QEMU 参数、每次 initramfs 哈希、完整串口哈希、保存片段的原始偏移/长度/哈希，以及审计助手观察到的 shell 状态。argv 中 KERNEL/FIRMWARE 是本机路径占位符，文件身份由哈希决定。
- `samples.json`：保留首次 OUTPUT_AUDIT_BEGIN 到 OUTPUT_AUDIT_DONE 的全部串口字节，使用 hex 防止换行转换；另外保留 QEMU stderr。来宾 stdout/stderr 已合流，不能据此恢复流归属。没有删掉失败、超时或迟到输出。边界是实验助手新增的，不是比赛输出。
- `image-members.json`：只读提取的原始文件大小、权限和哈希。未提取完整 LTP；四个选定 LTP 文件的执行权限在提取时显式设为 0755，其 mode 是实验安装值，不是原盘权限证据。
- `wrapper-comparison.json`：24 份镜像脚本与固定源码的完整差异。
- `sources.json`：核对输出时使用的固定源码文件哈希，包括 nested netperf 压缩包与其中源码；哈希不表示已逐行审计所有代码，也不表示镜像 ELF 的构建来源已经证明。
- `elf-observations.json`：选定实际 ELF 的 readelf 头和程序头；basic 的两套文件相同、加载器与浮点 ABI 的事实可回查。
- 四份 `*-init.sh` 是实际运行入口。`extract.py / pack.py / run.py` 将本机输入路径改为参数，分别只读提取、构造 initramfs、运行两个 libc 目录的参考实验。它们不实现 TEST-001 的安全采集/所有权协议，不可当作 C 阶段入口发布。

## 复现条件

需要 Python 3、debugfs、readelf、QEMU RISC-V 和已取得的锁定镜像。内核来源及 APK/解压后哈希沿用 [Lua 样本索引](../lua-a0/index.json)；固件和镜像必须匹配本目录 index。提取前校验整盘 SHA256；本次实验结束后也校验了原盘，值未改变。实验不把原盘传给 QEMU。

来宾使用 root、LC_ALL=C、TZ=UTC、TERM=dumb、umask 022、BusyBox shell 和 applets；工作目录 `/suite`，LD_LIBRARY_PATH=/suite/lib，/lib 指向 suite/lib。网络设备关闭，原组/补充实验启用 loopback。QEMU 时钟沿用主机默认值；各实验两套目录并行运行，不能比较数值性能。安装文件与 initramfs 内容决定实验条件；不能用目录名证明全部 ELF 使用对应 libc。

在仓库根目录设置三个输入路径和一个尚不存在的输出目录，然后执行：

```bash
AUDIT_FIXTURE=tests/test001/fixtures/benchmark-output
# AUDIT_IMAGE / AUDIT_KERNEL / AUDIT_FIRMWARE：本机已验证输入的绝对路径。
AUDIT_OUT="$PWD/target/benchmark-reproduce"
python3 "$AUDIT_FIXTURE/extract.py" "$AUDIT_IMAGE" "$AUDIT_OUT"
cp "$AUDIT_FIXTURE/original-init.sh" "$AUDIT_OUT/init.sh"
python3 "$AUDIT_FIXTURE/pack.py" "$AUDIT_OUT"
python3 "$AUDIT_FIXTURE/run.py" "$AUDIT_OUT" "$AUDIT_KERNEL" "$AUDIT_FIRMWARE"
```

`run.py` 启动前验证内核/固件哈希，拒绝已有 `*-run.json` 的目录。每个来宾外层限时 1600 秒，组内时限和参数见 init。对 supplement / format-probes / timing-probes，另建新目录，令其 root 指向上述提取目录的 root，复制对应 init，再 pack/run。保持文件内容和权限，生成的 initramfs SHA256 应与 index 对应实验一致；不一致先查输入和配方，不能覆盖既有证据。仅目录名不同不会改变 initramfs 字节。

复现后比较**格式、边界和失败事实**，不能期待 PID、时间、吞吐、调度顺序或完整串口哈希相同。初轮各组在同一来宾顺序执行，进程清理证据不完整；此局限不能被新的成功重跑抹掉。正式调用隔离与回收需在后续 runner 设计中单独验收。

源码可以通过本地 gh 获取固定提交的 tarball，再按 sources.json 校验每个文件；netperf 包须再解包并核对 nested_netperf。镜像包装器与源码只有 libc 后缀差异；其他程序的编译版本对应关系仍需后续建立。

```bash
gh api repos/oscomp/testsuits-for-oskernel/tarball/8b58dd16d26d30f7c74d48d5832d870d3051b703 > SOURCE_ARCHIVE.tar.gz
python3 -B -m unittest discover -s scripts -p 'test_test001_benchmark_samples.py'
```

这六项独立样本检查保护来源、全组尝试范围和关键反例，不启动 benchmark、不验证内核兼容性。特别保留 libc-test 成功/失败都退出 1、组 END/0 掩盖启动失败、UnixBench 单位冲突、lmbench 超时、glibc netperf 地址解析失败与 LTP TBROK；不可把测试检查通过写成 benchmark 通过。
