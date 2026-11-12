//! 跨后端复用的 `BlockDevice` 契约检查，判据沿用 HAL-001。

use super::{BLOCK_SIZE, BlockDevice, BlockError};

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
            .map_err(|error| ContractFailure {
                scenario,
                kind: FailureKind::UnexpectedError {
                    op: "write_block",
                    error,
                },
            })?;
        device
            .read_block(0, &mut buffer)
            .map_err(|error| ContractFailure {
                scenario,
                kind: FailureKind::UnexpectedError {
                    op: "read_block",
                    error,
                },
            })?;
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
