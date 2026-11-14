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
/// `model` 初始内容必须是独立已知的整盘镜像；成功写入后会更新它，
/// 以便每次写入立即比对全部块。失败后模型不代表实际盘面，请重新装载。
/// 检查盘面上的观察数据，不证明 DMA 安全、参数拒绝前无硬件访问，
/// 也不提升 HAL-001 中内存盘专有的参数错误缓冲区不变保证。
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
    // 每轮每个字节都不同于模型现值；初次丢写也无法靠预置图样蒙混。
    for mask in [0xff, 0xa5] {
        for block in 0..runner.model.len() {
            let input = runner.model[block].map(|byte| byte ^ mask);
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
/// 需支持同步的非空专用盘；`model` 与主检查入口的约定相同。
/// 注入请求不会到达真实设备，不能据此声称驱动实际故障恢复已验收。
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

struct Runner<'a> {
    backend: &'static str,
    device: &'a mut dyn BlockDevice,
    model: &'a mut [[u8; BLOCK_SIZE]],
}

impl Runner<'_> {
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

    // 所有期望仅来自模型；双预填针对漏填和固定缓冲区变换，不代表
    // 两遍扫描能排除读取副作用。扫描次数是固定的测试输入，不是证明。
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

// 注入的是接口返回值，不模拟 DMA、部分写入或断电。
struct FaultyDevice<'a> {
    inner: &'a mut dyn BlockDevice,
    fail: bool,
}

impl BlockDevice for FaultyDevice<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if self.fail {
            Err(BlockError::Io)
        } else {
            self.inner.read_block(block, buffer)
        }
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if self.fail {
            Err(BlockError::Io)
        } else {
            self.inner.write_block(block, buffer)
        }
    }
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
