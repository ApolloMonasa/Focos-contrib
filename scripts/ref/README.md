# TEST-001 参考运行与证据回放

这里运行参考 Linux，校准判分与失败处理。结果不计为 FocOS 成绩。构建、runner、团队判据和测试均随本 PR 交付。

## 先验证管线

需要 Linux/POSIX、Python 3.11+、已配置的 `gh`。缓存完整时无需联网；缓存缺失时通过 `gh` 从固定提交/Release 拉取。

```bash
make test-test001
make reference-replay
```

第一条运行清单一致性、构建故障注入、判分和 runner 契约测试。第二条核对历史归档 SHA256、38 个内部文件哈希，再回放 15 份机器结果，输出 `target/evidence/test001/replay/replay.json`。CI 同时执行这些检查；不依赖本地已有镜像、APK 或第三方 judge。

官方 judge 不入库。`scripts/fetch_judges.py` 从 `inventory.json` 的固定提交拉取 5 个文件并校验各自 SHA256；已有缓存被修改时明确失败，不覆盖现场。需要重新下载时使用新的 `--dest`，并把对应 `judge/` 传给 runner 的 `--judge-dir`。

## 取得当前冻结输入

唯一输入来源为 [inventory.json](../../tests/inventory.json) 和 [reference.lock.json](../../tests/reference.lock.json)。压缩镜像 SHA256 为 `2b59aee4d26681b2d78a8faa615ac216f723a8ea157544e7867bbc8c77aac087`，解压后为 `95973543db6b84a9a5e70f30da466ce292867aff5b689fb14c88dc9406e378b8`。

```bash
mkdir -p target/ref-inputs
gh release download pre-20250615 -R oscomp/testsuits-for-oskernel \
  -p sdcard-rv.img.xz -D target/ref-inputs
xz -dk target/ref-inputs/sdcard-rv.img.xz
gh release download test-001-pr29-validation-2026-11-16 \
  -R ApolloMonasa/Focos-contrib -p linux-lts-6.18.63-r0.apk -D target/ref-inputs
python3 -m venv target/ref-env
target/ref-env/bin/python -m pip install ext4==1.4.1
```

APK 原始来源为 Alpine v3.23，SHA256 为 `47034305e6ddde9717652ce1da25d29b51ae88b997ab7c69b0beeee00ccc9369`。Release 保留相同字节，避免滚动包站撤下旧包后无法取回；包来源及 aports 提交见 lock。历史 6.18.60 APK 的旧 URL 已返回 404，其哈希保留在 `historical_kernel`，不把新内核结果替代旧测量。

## 构建与实际执行

使用支持 RISC-V virt 的 QEMU；本轮使用 QEMU 10.2.2 / Alpine 6.18.63。下例在同一启动中执行双 libc 的 Lua：

```bash
target/ref-env/bin/python -B scripts/ref/build_initramfs.py \
  --image target/ref-inputs/sdcard-rv.img \
  --kernel-apk target/ref-inputs/linux-lts-6.18.63-r0.apk \
  --out target/ref/lua.cpio.gz --kernel-out target/ref/Image \
  --modules-dir target/ref/modules --libcs glibc musl \
  --scripts lua_testcode.sh > target/ref-build.log

python3 -B scripts/run_tests.py \
  --capture-cmd "$(head -n 1 target/ref-build.log)" \
  --out target/ref/results/glibc --libc glibc --group lua --timeout 180 \
  --reference strict --reference-manifest target/ref/lua.cpio.manifest.json \
  --reference-artifacts target/ref/Image target/ref/lua.cpio.gz

python3 -B scripts/run_tests.py \
  --log target/ref/results/glibc/serial.log \
  --out target/ref/results/musl --libc musl --group lua \
  --reference strict --reference-manifest target/ref/lua.cpio.manifest.json \
  --reference-artifacts target/ref/Image target/ref/lua.cpio.gz
```

构建器第一行输出经过 shell 引号保护的完整 QEMU 命令，第二行输出构件哈希。`-snapshot` 保留原始镜像；不同组建议分别启动，避免前一组的挂载或文件操作影响后一组。内核/initramfs/manifest 必须保留，严格回放需要核对实际构件。

默认检查镜像、APK、release；缺失或重复模块、输出覆盖输入、非法脚本名均在构建阶段失败。只提取当前 release 的必需模块；`netperf_testcode.sh` 自动启用 dummy 网卡，iperf/netperf 预先配置 loopback。显式 `--network-dummy` 可用于其他实验。

`--allow-unpinned-inputs` 或指定外部 `--busybox` 只能生成标记为未冻结的实验构件；严格判分拒绝这些输入。为排查而放宽构建不等于验收通过。

cpio 的顺序、权限、属主和时间固定；gzip 固定 `mtime=0` 且不记录文件名。相同输入、选择、实现和压缩工具链在不同目录/时间得到相同产物与 manifest。manifest 记录 zlib 版本、输入和实现哈希、选择及输出哈希；内嵌构建 ID 把实际串口与该次构建绑定。跨 zlib 版本不承诺压缩字节相同。

## 失败怎样区分

| 标记/状态 | 含义 |
| --- | --- |
| `REF_PROTOCOL 1`、`REF_BUILD_ID <sha256>` | 新协议和本次构建身份 |
| `REF_MODULES_OK`、`REF_MOUNT_OK` | 必需模块加载和 ext4 挂载成功 |
| `REF_PREPARED <libc>` | 对应目录、链接、配置、权限、脚本存在及 chroot 预检成功 |
| `REF RUN <libc> <script>` | 开始一次脚本执行；组输出须位于对应尝试内 |
| `REF RESULT <libc> <script> <rc>` | 脚本实际退出码；非零不能被成功输出掩盖 |
| `REF_ALL_DONE` | 全部循环结束；不表示测试通过 |
| `REF_FAIL <stage>` / `ENV_ERROR` | 必需前置动作失败，或协议/构件身份不一致；停止后续测试，结果无效 |
| `CAPTURE_ERROR` | 捕获进程非零退出，即使组标记完整也不能通过 |
| `LOG_ERROR` / `TIMEOUT` | 组标记重复、嵌套、错配 / 缺 END 或捕获超时 |
| `JUDGE_ERROR` | 判分器来源、执行或输出不合法；不降级到其他判据 |

原始 `serial.log` 逐字节保留，包括 CRLF 和非 UTF-8 字节；仅解析视图进行解码。环境/捕获错误会清零有效通过计数，原观察值保留于 `observed`。捕获超时会杀死整个子进程组，避免 QEMU 后代继续持有串口管道。

参考标记自动启用严格检查；严格模式还必须提供 manifest 和两份构件。旧日志使用 `--reference legacy`，输出环境 `LEGACY_UNVERIFIED`、`execution_valid: false`，即使子项全部通过也退出 1。历史回放比较逐项结果与数量，明确移除原先没有物理意义的跨单位总分。

## 团队判据与边界

- 六个团队判据拒绝重复/冲突结果。指标名从同一冻结清单读取，避免独立维护多份名称。
- lmbench 按冻结源码的完整标签、单位和有效正数识别。lat_mmap 与四个读带宽指标的原始输出没有标签；新 harness 对 lmbench 使用 `sh -x` 记录命令上下文，脚本内容不修改。缺少上下文时不按数字出现顺序猜测归属。
- libc-bench 需要标签和完整 time 行；中间/尾随错误、重复 time、NaN/Inf 不构成成功。
- 性能组的“通过”只表示产生约定的有效结果，不表示达到 Linux 性能，更不能跨不同单位求和。
- symlink、chmod、hosts、loopback/dummy 是参考环境适配，必须随结果披露；不能声称原版镜像无需适配即可兼容。

历史结果见 [2026-11-08 测量](../../docs/reference/reference-run.md)，新验证结果及限制见 [2026-11-16 摘要](../../tests/results/reference-2026-11-16/summary.md)。这些均非 FocOS 或 VF2 成绩。
