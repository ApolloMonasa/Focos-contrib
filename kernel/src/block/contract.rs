//! 跨后端复用的 `BlockDevice` 契约检查，判据沿用 HAL-001，
//! 供内存盘、QEMU VirtIO 块设备与真板 SD 共用（DRV-001「共享接口验收」）。
//!
//! 后端适配步骤：
//! 1. 实现 [`BlockDevice`]，写清 `flush` 的真实语义与保证范围。
//! 2. 准备专用测试盘/镜像（勿用生产盘或含个人数据的卡）并确定已知容量；
//!    故障场景用本模块包装设备注入，不需要真实损坏的硬件。
//! 3. host 侧逐项调用 `check_*`，保持每个场景独立的失败定位；
//!    上板自检或 CI 汇总改用 [`run_block_contract`] 并传入后端名。
//! 4. 按 `flush` 实际语义选择检查：成功走 [`check_flush_ok`]（汇总入口即此
//!    路径），不支持走 [`check_flush_unsupported`]（通过只说明如实上报），
//!    会失败走 [`check_flush_failure_retry`]；不支持被跳过不算成功。
//! 5. 硬件专项（超时、DMA 生命周期、中断、重启持久化、真实故障）留在各
//!    驱动模块按 DRV-001 验收；共享测试通过不替代真实驱动或真板验收。

use super::{BLOCK_SIZE, BlockDevice, BlockError};

/// 带后端名的契约失败，用于汇总入口的失败定位。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BlockContractError {
    /// 失败的后端名，如 `"memory-disk"`、`"virtio-mmio"`、`"sd-jh7110"`。
    pub backend: &'static str,
    /// 失败场景与细节。
    pub failure: ContractFailure,
}

/// 单项契约检查失败：场景名加失败细节，便于定位后端与失败场景。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContractFailure {
    /// 失败场景的固定名称，如 `read_write_roundtrip`。
    pub scenario: &'static str,
    /// 具体失败原因。
    pub kind: FailureKind,
}

/// 契约检查能观察到的失败种类。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureKind {
    /// 设备报告的容量与测试盘已知容量不符。
    WrongBlockCount { expected: u64, actual: u64 },
    /// 操作返回了错误但不是契约要求的错误。
    WrongError {
        op: &'static str,
        expected: BlockError,
        actual: BlockError,
    },
    /// 契约预期成功的操作返回了错误。
    UnexpectedError { op: &'static str, error: BlockError },
    /// 契约预期失败的操作返回了成功。
    UnexpectedSuccess { op: &'static str },
    /// 读回数据与期望图样在指定字节处不符。
    DataMismatch {
        block: u64,
        byte: usize,
        expected: u8,
        actual: u8,
    },
    /// 参数错误的操作修改了调用者缓冲区。
    BufferMutated,
}

/// 写后读：向第 0 块先后写入两种随偏移变化的图样并整块读回，
/// 逐字节比对，覆盖 HAL-001 的正常读写场景（含字节顺序不变）。
///
/// 前置：设备至少 1 块，且写入专用测试盘。
pub fn check_read_write_roundtrip(device: &mut dyn BlockDevice) -> Result<(), ContractFailure> {
    let scenario = "read_write_roundtrip";
    let first: [u8; BLOCK_SIZE] = core::array::from_fn(|i| (i ^ (i >> 8)) as u8);
    let second: [u8; BLOCK_SIZE] = core::array::from_fn(|i| (i.wrapping_mul(17) >> 1) as u8);
    let mut buffer = [0; BLOCK_SIZE];
    for pattern in [first, second] {
        device
            .write_block(0, &pattern)
            .map_err(unexpected(scenario, "write_block"))?;
        device
            .read_block(0, &mut buffer)
            .map_err(unexpected(scenario, "read_block"))?;
        compare_block(scenario, 0, &buffer, &pattern)?;
    }
    Ok(())
}

/// 长度校验：0、`BLOCK_SIZE-1`、`BLOCK_SIZE+1` 字节的缓冲区被拒绝并返回
/// [`BlockError::InvalidBufferLength`]，且长度错误优先于块号错误；
/// 参数错误的操作不得修改调用者缓冲区。
///
/// 前置：设备至少 1 块。
pub fn check_buffer_length_validation(device: &mut dyn BlockDevice) -> Result<(), ContractFailure> {
    let scenario = "buffer_length_validation";
    let sentinel = 0x77;
    let mut buffer = [sentinel; BLOCK_SIZE + 1];
    for length in [0, BLOCK_SIZE - 1, BLOCK_SIZE + 1] {
        expect_error(
            scenario,
            "read_block",
            BlockError::InvalidBufferLength,
            device.read_block(0, &mut buffer[..length]),
        )?;
        expect_error(
            scenario,
            "write_block",
            BlockError::InvalidBufferLength,
            device.write_block(0, &buffer[..length]),
        )?;
        if buffer != [sentinel; BLOCK_SIZE + 1] {
            return Err(ContractFailure {
                scenario,
                kind: FailureKind::BufferMutated,
            });
        }
    }
    // 巨大块编号配非法长度：长度错误必须优先于块号错误。
    expect_error(
        scenario,
        "write_block",
        BlockError::InvalidBufferLength,
        device.write_block(u64::MAX, &[]),
    )
}

/// 越界校验：第 N 块与 `u64::MAX` 的读写被拒绝并返回
/// [`BlockError::OutOfBounds`]，被拒的读取不得修改调用者缓冲区；
/// 被拒操作前后探针块（首、中、末）内容保持不变。
///
/// 前置：设备至少 1 块，且写入专用测试盘。
pub fn check_out_of_bounds_rejected(device: &mut dyn BlockDevice) -> Result<(), ContractFailure> {
    let scenario = "out_of_bounds_rejected";
    let count = device.block_count();
    if count == 0 {
        return Err(ContractFailure {
            scenario,
            kind: FailureKind::WrongBlockCount {
                expected: 1,
                actual: count,
            },
        });
    }
    let probes = [0, count / 2, count - 1];
    for block in probes {
        let pattern = probe_pattern(block);
        device
            .write_block(block, &pattern)
            .map_err(unexpected(scenario, "write_block"))?;
    }
    let sentinel = 0x77;
    let mut buffer = [sentinel; BLOCK_SIZE];
    for block in [count, u64::MAX] {
        expect_error(
            scenario,
            "read_block",
            BlockError::OutOfBounds,
            device.read_block(block, &mut buffer),
        )?;
        if buffer != [sentinel; BLOCK_SIZE] {
            return Err(ContractFailure {
                scenario,
                kind: FailureKind::BufferMutated,
            });
        }
        expect_error(
            scenario,
            "write_block",
            BlockError::OutOfBounds,
            device.write_block(block, &buffer),
        )?;
    }
    for block in probes {
        let pattern = probe_pattern(block);
        device
            .read_block(block, &mut buffer)
            .map_err(unexpected(scenario, "read_block"))?;
        compare_block(scenario, block, &buffer, &pattern)?;
    }
    Ok(())
}

/// 邻块保持：写入首块与末块后，中间块内容不变（HAL-001 最后一块场景）。
///
/// 前置：设备至少 3 块，且写入专用测试盘。
pub fn check_first_last_write_preserves_neighbors(
    device: &mut dyn BlockDevice,
) -> Result<(), ContractFailure> {
    let scenario = "first_last_write_preserves_neighbors";
    let count = device.block_count();
    if count < 3 {
        return Err(ContractFailure {
            scenario,
            kind: FailureKind::WrongBlockCount {
                expected: 3,
                actual: count,
            },
        });
    }
    let (first, last, neighbor) = (0, count - 1, count / 2);
    let base = [0x33; BLOCK_SIZE];
    device
        .write_block(neighbor, &base)
        .map_err(unexpected(scenario, "write_block"))?;
    device
        .write_block(first, &[0x11; BLOCK_SIZE])
        .map_err(unexpected(scenario, "write_block"))?;
    device
        .write_block(last, &[0x22; BLOCK_SIZE])
        .map_err(unexpected(scenario, "write_block"))?;
    let mut buffer = [0; BLOCK_SIZE];
    for (block, expected) in [
        (first, [0x11; BLOCK_SIZE]),
        (neighbor, base),
        (last, [0x22; BLOCK_SIZE]),
    ] {
        device
            .read_block(block, &mut buffer)
            .map_err(unexpected(scenario, "read_block"))?;
        compare_block(scenario, block, &buffer, &expected)?;
    }
    Ok(())
}

/// 空设备校验：容量为 0 时任何块编号的读写都被拒绝并返回
/// [`BlockError::OutOfBounds`]，读取缓冲区保持不变。
///
/// 前置：后端提供容量为 0 的专用测试设备。
pub fn check_empty_device(device: &mut dyn BlockDevice) -> Result<(), ContractFailure> {
    let scenario = "empty_device";
    let count = device.block_count();
    if count != 0 {
        return Err(ContractFailure {
            scenario,
            kind: FailureKind::WrongBlockCount {
                expected: 0,
                actual: count,
            },
        });
    }
    let sentinel = 0x77;
    let mut buffer = [sentinel; BLOCK_SIZE];
    expect_error(
        scenario,
        "read_block",
        BlockError::OutOfBounds,
        device.read_block(0, &mut buffer),
    )?;
    if buffer != [sentinel; BLOCK_SIZE] {
        return Err(ContractFailure {
            scenario,
            kind: FailureKind::BufferMutated,
        });
    }
    expect_error(
        scenario,
        "write_block",
        BlockError::OutOfBounds,
        device.write_block(0, &buffer),
    )
}

/// 同步成功语义：写入后 `flush` 成功，重读数据一致。
///
/// 通过本检查不代表断电持久化；持久化与重启读回按环境在硬件专项验证。
/// 前置：设备（或其包装）的 `flush` 返回成功；设备至少 1 块。
pub fn check_flush_ok(device: &mut dyn BlockDevice) -> Result<(), ContractFailure> {
    let scenario = "flush_ok";
    device
        .write_block(0, &[0x55; BLOCK_SIZE])
        .map_err(unexpected(scenario, "write_block"))?;
    device.flush().map_err(unexpected(scenario, "flush"))?;
    let mut buffer = [0; BLOCK_SIZE];
    device
        .read_block(0, &mut buffer)
        .map_err(unexpected(scenario, "read_block"))?;
    compare_block(scenario, 0, &buffer, &[0x55; BLOCK_SIZE])
}

/// 同步不支持语义：有未同步写入时请求 `flush` 必须收到
/// [`BlockError::Unsupported`]，不得返回成功。
///
/// 前置：被测设备（或其包装）不提供同步能力；设备至少 1 块。
/// 通过本检查只说明不支持被如实上报，不代表设备支持同步。
pub fn check_flush_unsupported(device: &mut dyn BlockDevice) -> Result<(), ContractFailure> {
    let scenario = "flush_unsupported";
    device
        .write_block(0, &[0x55; BLOCK_SIZE])
        .map_err(unexpected(scenario, "write_block"))?;
    expect_error(scenario, "flush", BlockError::Unsupported, device.flush())
}

/// 同步失败语义：`flush` 失败向调用者返回 [`BlockError::Io`]，不吞错、
/// 不返回成功；清除故障后重试成功且数据仍在。
///
/// 检查自行注入和清除同步故障；前置：设备至少 1 块。
pub fn check_flush_failure_retry(device: &mut FaultyDevice<'_>) -> Result<(), ContractFailure> {
    let scenario = "flush_failure_retry";
    device
        .write_block(0, &[0x55; BLOCK_SIZE])
        .map_err(unexpected(scenario, "write_block"))?;
    device.fail_flush = true;
    expect_error(scenario, "flush", BlockError::Io, device.flush())?;
    device.fail_flush = false;
    device.flush().map_err(unexpected(scenario, "flush"))?;
    let mut buffer = [0; BLOCK_SIZE];
    device
        .read_block(0, &mut buffer)
        .map_err(unexpected(scenario, "read_block"))?;
    compare_block(scenario, 0, &buffer, &[0x55; BLOCK_SIZE])
}

/// 读写失败语义：设备错误以 [`BlockError::Io`] 传给调用者，失败的读取
/// 不修改缓冲区；清除故障后恢复正常读写，原数据未受失败写影响。
///
/// 检查自行注入和清除读写故障；前置：设备至少 1 块。
pub fn check_io_failures_recoverable(device: &mut FaultyDevice<'_>) -> Result<(), ContractFailure> {
    let scenario = "io_failures_recoverable";
    let pattern = probe_pattern(0);
    device
        .write_block(0, &pattern)
        .map_err(unexpected(scenario, "write_block"))?;
    device.fail_read = true;
    device.fail_write = true;
    let sentinel = 0x77;
    let mut buffer = [sentinel; BLOCK_SIZE];
    expect_error(
        scenario,
        "read_block",
        BlockError::Io,
        device.read_block(0, &mut buffer),
    )?;
    if buffer != [sentinel; BLOCK_SIZE] {
        return Err(ContractFailure {
            scenario,
            kind: FailureKind::BufferMutated,
        });
    }
    expect_error(
        scenario,
        "write_block",
        BlockError::Io,
        device.write_block(0, &buffer),
    )?;
    device.fail_read = false;
    device.fail_write = false;
    device
        .read_block(0, &mut buffer)
        .map_err(unexpected(scenario, "read_block"))?;
    compare_block(scenario, 0, &buffer, &pattern)
}

/// 汇总入口：校验已知容量后顺序执行全部契约检查，首个失败带后端名返回。
///
/// 供上板自检或 CI 汇总使用；host 单测建议逐项调用 `check_*` 保留
/// 每个场景的原生失败定位。本入口要求设备 `flush` 成功且容量至少 3 块；
/// 不支持或会失败的同步、以及 0 容量空盘，请逐项调用对应检查。
///
/// # 参数
/// - `backend`：后端名，用于失败定位。
/// - `device`：被测设备。
/// - `expected_blocks`：专用测试盘的已知容量，单位为块。
///
/// # 返回与错误
/// 全部场景通过返回 `Ok(())`；容量不符或任一场景失败返回
/// 带后端名与场景的 [`BlockContractError`]。
pub fn run_block_contract(
    backend: &'static str,
    device: &mut dyn BlockDevice,
    expected_blocks: u64,
) -> Result<(), BlockContractError> {
    let wrap = |failure| BlockContractError { backend, failure };
    let actual = device.block_count();
    if actual != expected_blocks {
        return Err(wrap(ContractFailure {
            scenario: "block_count",
            kind: FailureKind::WrongBlockCount {
                expected: expected_blocks,
                actual,
            },
        }));
    }
    check_read_write_roundtrip(device).map_err(wrap)?;
    check_first_last_write_preserves_neighbors(device).map_err(wrap)?;
    check_out_of_bounds_rejected(device).map_err(wrap)?;
    check_buffer_length_validation(device).map_err(wrap)?;
    check_flush_ok(device).map_err(wrap)?;
    {
        let mut no_sync = NoSyncDevice::new(&mut *device);
        check_flush_unsupported(&mut no_sync).map_err(wrap)?;
    }
    let mut faulty = FaultyDevice::new(&mut *device);
    check_flush_failure_retry(&mut faulty).map_err(wrap)?;
    check_io_failures_recoverable(&mut faulty).map_err(wrap)
}

/// 仅转发读写、刻意不覆盖默认 `flush` 的包装设备，用于验证
/// [`BlockError::Unsupported`] 能到达调用者。
pub struct NoSyncDevice<'a> {
    inner: &'a mut dyn BlockDevice,
}

impl<'a> NoSyncDevice<'a> {
    /// 包装任意块设备；包装后的设备 `flush` 返回 [`BlockError::Unsupported`]。
    pub fn new(inner: &'a mut dyn BlockDevice) -> Self {
        Self { inner }
    }
}

impl BlockDevice for NoSyncDevice<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.inner.write_block(block, buffer)
    }
}

/// 可按开关注入读、写、同步故障的包装设备，用于验证失败分支；
/// 故障只验证接口错误可见性，不模拟真实硬件持久化。
pub struct FaultyDevice<'a> {
    inner: &'a mut dyn BlockDevice,
    /// 令 `read_block` 返回 [`BlockError::Io`]。
    pub fail_read: bool,
    /// 令 `write_block` 返回 [`BlockError::Io`]。
    pub fail_write: bool,
    /// 令 `flush` 返回 [`BlockError::Io`]。
    pub fail_flush: bool,
}

impl<'a> FaultyDevice<'a> {
    /// 包装任意块设备，初始不注入故障，调用方按场景打开开关。
    pub fn new(inner: &'a mut dyn BlockDevice) -> Self {
        Self {
            inner,
            fail_read: false,
            fail_write: false,
            fail_flush: false,
        }
    }
}

impl BlockDevice for FaultyDevice<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if self.fail_read {
            return Err(BlockError::Io);
        }
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if self.fail_write {
            return Err(BlockError::Io);
        }
        self.inner.write_block(block, buffer)
    }
    fn flush(&mut self) -> Result<(), BlockError> {
        if self.fail_flush {
            return Err(BlockError::Io);
        }
        self.inner.flush()
    }
}

/// 生成随块编号变化的探针图样，使跨块串扰可被检出。
fn probe_pattern(block: u64) -> [u8; BLOCK_SIZE] {
    core::array::from_fn(|i| (i as u8).wrapping_add(block as u8))
}

/// 把预期成功操作的错误包装为契约失败。
fn unexpected(scenario: &'static str, op: &'static str) -> impl Fn(BlockError) -> ContractFailure {
    move |error| ContractFailure {
        scenario,
        kind: FailureKind::UnexpectedError { op, error },
    }
}

/// 断言操作以指定错误失败；成功或返回其他错误都算契约失败。
fn expect_error(
    scenario: &'static str,
    op: &'static str,
    expected: BlockError,
    actual: Result<(), BlockError>,
) -> Result<(), ContractFailure> {
    match actual {
        Err(actual) if actual == expected => Ok(()),
        Err(actual) => Err(ContractFailure {
            scenario,
            kind: FailureKind::WrongError {
                op,
                expected,
                actual,
            },
        }),
        Ok(()) => Err(ContractFailure {
            scenario,
            kind: FailureKind::UnexpectedSuccess { op },
        }),
    }
}

/// 逐字节比较读回缓冲区与期望图样，报告首个不符位置。
fn compare_block(
    scenario: &'static str,
    block: u64,
    actual: &[u8],
    expected: &[u8],
) -> Result<(), ContractFailure> {
    for (byte, (actual, expected)) in actual.iter().zip(expected).enumerate() {
        if actual != expected {
            return Err(ContractFailure {
                scenario,
                kind: FailureKind::DataMismatch {
                    block,
                    byte,
                    expected: *expected,
                    actual: *actual,
                },
            });
        }
    }
    Ok(())
}
