//! TEST-002：共享块接口检查，使用调用方提供的独立镜像模型。
//!
//! 适配：用独立镜像初始化专用测试盘，把相同镜像复制到单独的 `model`，
//! 调用 [`run_block_contract`]，支持同步的非空盘再调用
//! [`check_injected_failures`]。每个后端保存自己的返回结果。
//! 模型和盘面不得共用存储，也不得通过被测设备读取来建立模型。
//! 失败后停止；重跑前重新装载镜像和模型。
//!
//! 这是有限输入与顺序的观察测试，不能证明读取没有副作用。尤其最后
//! 一次读取返回后再损坏介质的设备，不能用更多同源读取完整判定。
//! DMA/超时/中断、介质全范围寻址、重启与持久化按 DRV-001 另验。

use super::{BLOCK_SIZE, BlockDevice, BlockError};

/// 二次方读回检查仅用于极小的专用盘；8 块对应 4 KiB 模型，由调用方持有。
pub const MAX_TEST_BLOCKS: usize = 8;

/// 本次实际后端应返回的同步能力，必须由适配者明确指定。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FlushExpectation {
    Supported,
    Unsupported,
}

/// 定位到后端、观察场景与块号的失败结果。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContractError {
    pub backend: &'static str,
    pub scenario: &'static str,
    pub block: Option<u64>,
    pub kind: FailureKind,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureKind {
    FixtureTooLarge {
        limit: usize,
        actual: usize,
    },
    BlockCount {
        expected: u64,
        actual: u64,
    },
    /// 故障注入检查要求非空盘。
    EmptyFixture,
    ReturnValue {
        expected: Result<(), BlockError>,
        actual: Result<(), BlockError>,
    },
    DataMismatch {
        byte: usize,
        expected: u8,
        actual: u8,
    },
}

/// 检查已知数据、错误边界、逐块写后读及实际同步返回值。
///
/// # 参数
/// - `backend`：失败结果中使用的后端名称，如 `"memory"` 或 `"virtio"`。
/// - `device`：独占使用的专用测试盘。检查会覆盖其中全部有效块。
/// - `model`：独立已知的整盘期望内容，长度就是预期块数，范围为
///   `0..=MAX_TEST_BLOCKS`；空切片用于检查空盘。模型须与设备存储分离，
///   初值来自测试镜像，不能通过被测设备读取生成。
/// - `flush`：后端约定的同步返回值。选择
///   [`FlushExpectation::Unsupported`] 时仍会调用并检查 `flush`。
///
/// # 返回与状态
/// 全部场景符合期望时返回 `Ok(())`；首次容量、返回值或数据不符时返回
/// [`ContractError`]，其中包含后端名、场景、相关块号和具体差异。
/// 测试盘超出 [`MAX_TEST_BLOCKS`] 时，在访问设备前返回夹具错误。
/// 每次成功写入后按请求更新 `model`，再立即比对整盘；失败时不回滚。
/// 失败后的模型不代表实际盘面，重新运行前须重新装载镜像和模型。
///
/// # 验证边界
/// 检查盘面上的观察数据，不证明 DMA 安全、参数拒绝前无硬件访问，
/// 也不提升 HAL-001 中内存盘专有的参数错误缓冲区不变保证。
///
/// # 示例
/// ```
/// use focos_kernel::block::{BLOCK_SIZE, MemoryDisk};
/// use focos_kernel::block::contract::{
///     FlushExpectation, check_injected_failures, run_block_contract,
/// };
///
/// // 从同一份已知镜像分别准备模型与盘面，不从被测接口读取模型。
/// let mut model = [[0x33; BLOCK_SIZE]; 3];
/// let mut storage = model;
/// {
///     let mut device = MemoryDisk::new(storage.as_flattened_mut()).unwrap();
///     run_block_contract("memory", &mut device, &mut model,
///                        FlushExpectation::Supported).unwrap();
///     check_injected_failures("memory", &mut device, &mut model).unwrap();
/// }
/// assert_eq!(storage, model);
/// ```
pub fn run_block_contract(
    backend: &'static str,
    device: &mut dyn BlockDevice,
    model: &mut [[u8; BLOCK_SIZE]],
    flush: FlushExpectation,
) -> Result<(), ContractError> {
    let mut runner = Runner {
        backend,
        device,
        model,
    };
    runner.validate()?;
    runner.verify("known_data")?;
    runner.parameters()?;
    // 先取反使第一次写入可观察；再写固定的索引低/高位图样。
    // 后两次的实际值对唯一标识 0..512 的位置，不能把这个性质归给
    // 与模型异或的掩码：初始内容可能恰好抵消掩码而制造写入值碰撞。
    for round in 0..3 {
        for block in 0..runner.model.len() {
            let input = core::array::from_fn(|byte| match round {
                0 => !runner.model[block][byte],
                1 => byte as u8,
                _ => (byte >> 8) as u8,
            });
            let actual = runner.device.write_block(block as u64, &input);
            runner.expect("write", Some(block as u64), Ok(()), actual)?;
            runner.model[block] = input;
            runner.verify("write_readback")?;
        }
    }
    let expected = match flush {
        FlushExpectation::Supported => Ok(()),
        FlushExpectation::Unsupported => Err(BlockError::Unsupported),
    };
    let actual = runner.device.flush();
    runner.expect("flush", None, expected, actual)?;
    runner.verify("flush_readback")
}

/// 用转发包装层验证读/写/同步错误可见，清除故障后可再次正常调用。
///
/// # 参数
/// - `backend`：写入失败结果的后端名称。
/// - `device`：支持同步的非空专用测试盘；恢复检查会改写第 0 块。
/// - `model`：设备当前内容的独立期望模型，来源、容量上限和存储分离
///   要求同 [`run_block_contract`]。可接续使用该函数成功更新后的模型。
///
/// # 返回与状态
/// 包装层的三种故障均返回 [`BlockError::Io`]，且清除故障后的读、写、
/// 同步与整盘读回符合期望时返回 `Ok(())`。容量、返回值或数据不符时
/// 返回 [`ContractError`]；空模型返回 [`FailureKind::EmptyFixture`]。
/// 恢复写入成功后更新模型的第 0 块；失败时不回滚，重跑前须重建盘面与模型。
///
/// # 验证边界
/// 注入期间读、写和同步请求均由包装层直接拒绝，不会到达真实设备。
/// 此结果只验证错误可见和清除后的调用恢复，不能作为驱动真实故障恢复、
/// 部分传输或断电持久化的验收证据。用法见 [`run_block_contract`] 的示例。
pub fn check_injected_failures(
    backend: &'static str,
    device: &mut dyn BlockDevice,
    model: &mut [[u8; BLOCK_SIZE]],
) -> Result<(), ContractError> {
    let mut faulty = FaultyDevice {
        inner: device,
        fail: false,
    };
    let mut runner = Runner {
        backend,
        device: &mut faulty,
        model,
    };
    runner.validate()?;
    if runner.model.is_empty() {
        return Err(runner.error("fixture", None, FailureKind::EmptyFixture));
    }
    runner.verify("known_data")?;
    // 暂时结束 Runner 的借用以切换故障；模型保持为独立的原内容。
    faulty.fail = true;
    let mut buffer = [0x77; BLOCK_SIZE];
    let input = model[0].map(|byte| !byte);
    let read = faulty.read_block(0, &mut buffer);
    let write = faulty.write_block(0, &input);
    let flush = faulty.flush();
    let runner = Runner {
        backend,
        device: &mut faulty,
        model,
    };
    for (scenario, actual) in [
        ("injected_read", read),
        ("injected_write", write),
        ("injected_flush", flush),
    ] {
        runner.expect(scenario, Some(0), Err(BlockError::Io), actual)?;
    }
    faulty.fail = false;
    let mut runner = Runner {
        backend,
        device: &mut faulty,
        model,
    };
    runner.verify("fault_cleared")?;
    let actual = runner.device.write_block(0, &input);
    runner.expect("recovered_write", Some(0), Ok(()), actual)?;
    runner.model[0] = input;
    runner.verify("recovered_write_readback")?;
    let actual = runner.device.flush();
    runner.expect("recovered_flush", None, Ok(()), actual)?;
    runner.verify("recovered_flush_readback")
}

/// 持有本轮检查所需的后端标签、独占设备与独立期望模型。
struct Runner<'a> {
    backend: &'static str,
    device: &'a mut dyn BlockDevice,
    model: &'a mut [[u8; BLOCK_SIZE]],
}

impl Runner<'_> {
    /// 把当前后端名、`scenario` 和失败细节组合为定位结果。
    /// `block` 表示相关块号；`None` 用于容量或整设备操作。
    fn error(
        &self,
        scenario: &'static str,
        block: Option<u64>,
        kind: FailureKind,
    ) -> ContractError {
        ContractError {
            backend: self.backend,
            scenario,
            block,
            kind,
        }
    }

    /// 比较操作的完整返回值，包括成功与具体错误类型。
    /// 相同返回 `Ok(())`，否则用场景和块号报告 [`FailureKind::ReturnValue`]。
    fn expect(
        &self,
        scenario: &'static str,
        block: Option<u64>,
        expected: Result<(), BlockError>,
        actual: Result<(), BlockError>,
    ) -> Result<(), ContractError> {
        if actual == expected {
            Ok(())
        } else {
            Err(self.error(
                scenario,
                block,
                FailureKind::ReturnValue { expected, actual },
            ))
        }
    }

    /// 校验模型容量上限，并比较设备自报容量与模型块数。
    /// 先拒绝超限夹具，再调用 `block_count`；本方法不执行块读写，允许空盘。
    /// 失败分别报告 [`FailureKind::FixtureTooLarge`] 或 [`FailureKind::BlockCount`]。
    fn validate(&self) -> Result<(), ContractError> {
        let count = self.model.len();
        if count > MAX_TEST_BLOCKS {
            return Err(self.error(
                "fixture",
                None,
                FailureKind::FixtureTooLarge {
                    limit: MAX_TEST_BLOCKS,
                    actual: count,
                },
            ));
        }
        let actual = self.device.block_count();
        if actual != count as u64 {
            return Err(self.error(
                "capacity",
                None,
                FailureKind::BlockCount {
                    expected: count as u64,
                    actual,
                },
            ));
        }
        Ok(())
    }

    /// 逐块读取并与当前模型比较，用 `scenario` 标记观察发生的阶段。
    /// 每块分别以期望字节异或 `0xff`、`0xa5` 预填后读取，检查漏填或只取反
    /// 缓冲区等错误；模型始终只读，不用设备读回值重建期望。
    /// 读取失败立即报告返回值差异，内容不符则报告首个差异的块号和字节位置。
    /// 两种预填是有限测试输入，不能据此证明读取没有介质副作用。
    fn verify(&mut self, scenario: &'static str) -> Result<(), ContractError> {
        for (block, expected) in self.model.iter().enumerate() {
            for mask in [0xff, 0xa5] {
                let mut buffer = expected.map(|byte| byte ^ mask);
                let actual = self.device.read_block(block as u64, &mut buffer);
                self.expect(scenario, Some(block as u64), Ok(()), actual)?;
                for byte in 0..BLOCK_SIZE {
                    if buffer[byte] != expected[byte] {
                        return Err(self.error(
                            scenario,
                            Some(block as u64),
                            FailureKind::DataMismatch {
                                byte,
                                expected: expected[byte],
                                actual: buffer[byte],
                            },
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    /// 检查非法长度、越界块号及长度错误的优先级，首次返回值不符即停止。
    /// 前置：[`Self::validate`] 已通过，使 `N + 1` 的计算有界。
    /// 将 0、511、513 字节与每个有效块及 `N`、`N + 1`、`u64::MAX` 交叉读写；
    /// 再以完整块长度探测三个越界块。空盘也执行相同的越界组合。
    /// 只核对返回值，不更新模型，也不检查参数错误后的缓冲区或介质是否改变。
    fn parameters(&mut self) -> Result<(), ContractError> {
        let count = self.model.len() as u64;
        let invalid = [count, count + 1, u64::MAX];
        for block in (0..count).chain(invalid) {
            for length in [0, BLOCK_SIZE - 1, BLOCK_SIZE + 1] {
                let mut buffer = [0x77; BLOCK_SIZE + 1];
                let actual = self.device.read_block(block, &mut buffer[..length]);
                self.expect(
                    "invalid_length_read",
                    Some(block),
                    Err(BlockError::InvalidBufferLength),
                    actual,
                )?;
                let actual = self.device.write_block(block, &buffer[..length]);
                self.expect(
                    "invalid_length_write",
                    Some(block),
                    Err(BlockError::InvalidBufferLength),
                    actual,
                )?;
            }
        }
        for block in invalid {
            let mut buffer = [0x77; BLOCK_SIZE];
            let actual = self.device.read_block(block, &mut buffer);
            self.expect(
                "out_of_bounds_read",
                Some(block),
                Err(BlockError::OutOfBounds),
                actual,
            )?;
            let actual = self.device.write_block(block, &buffer);
            self.expect(
                "out_of_bounds_write",
                Some(block),
                Err(BlockError::OutOfBounds),
                actual,
            )?;
        }
        Ok(())
    }
}

/// 通过 `fail` 开关直接返回接口错误，否则转发到独占借用的后端。
/// 只注入返回值，不模拟 DMA、部分写入或断电；容量查询始终转发。
struct FaultyDevice<'a> {
    inner: &'a mut dyn BlockDevice,
    fail: bool,
}

impl BlockDevice for FaultyDevice<'_> {
    /// 返回底层设备的块数；故障开关不影响容量查询。
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    /// 开启故障时直接返回 [`BlockError::Io`]，不访问后端或改动 `buffer`；
    /// 否则把块号和缓冲区原样转发，保留后端的返回值与缓冲区语义。
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if self.fail {
            Err(BlockError::Io)
        } else {
            self.inner.read_block(block, buffer)
        }
    }
    /// 开启故障时直接返回 [`BlockError::Io`]，不向后端提交写入；
    /// 否则转发指定块与输入数据，原样返回后端结果。
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if self.fail {
            Err(BlockError::Io)
        } else {
            self.inner.write_block(block, buffer)
        }
    }
    /// 开启故障时直接返回 [`BlockError::Io`]，不调用后端同步；
    /// 否则转发同步请求，保留后端的成功、错误或不支持结果。
    fn flush(&mut self) -> Result<(), BlockError> {
        if self.fail {
            Err(BlockError::Io)
        } else {
            self.inner.flush()
        }
    }
}

#[cfg(test)]
mod tests;
