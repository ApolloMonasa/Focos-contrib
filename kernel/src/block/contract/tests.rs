use super::*;
use crate::block::{BLOCK_SIZE, BlockDevice, BlockError, MemoryDisk};

fn image<const N: usize>() -> [[u8; BLOCK_SIZE]; N] {
    core::array::from_fn(|block| {
        core::array::from_fn(|byte| (byte as u8).wrapping_mul(17) ^ block as u8)
    })
}

fn memory_contract<const N: usize>() {
    let mut model = image::<N>();
    let mut storage = model;
    {
        let mut disk = MemoryDisk::new(storage.as_flattened_mut()).unwrap();
        run_block_contract("memory", &mut disk, &mut model, FlushExpectation::Supported).unwrap();
    }
    assert_eq!(storage, model);
}

#[test]
fn memory_adapter_covers_empty_single_and_multiple_blocks() {
    memory_contract::<0>();
    memory_contract::<1>();
    memory_contract::<4>();
    memory_contract::<8>();
}

#[test]
fn injected_errors_are_visible_and_recoverable() {
    let mut model = image::<3>();
    let mut storage = model;
    {
        let mut disk = MemoryDisk::new(storage.as_flattened_mut()).unwrap();
        check_injected_failures("memory-fault", &mut disk, &mut model).unwrap();
    }
    assert_eq!(storage, model);
}

#[derive(Clone, Copy, PartialEq)]
enum Defect {
    None,
    WrongCount,
    CachedReads,
    UnfilledReads,
    ComplementReads,
    DropFirstWrite,
    DropMiddleWrite,
    SpillWrite,
    BlockBeforeLength,
    WriteBlockBeforeLength,
    EmptyBlockBeforeLength,
    AcceptHugeEmpty,
    AcceptHugeWriteEmpty,
    UnsupportedFlush,
    IoFlush,
    ClobberOnFlush,
}

// 用安全 Rust 模拟可观察的错误，不通过违反 &[u8] 借用规则伪造 DMA。
struct TestDisk<const N: usize> {
    storage: [[u8; BLOCK_SIZE]; N],
    defect: Defect,
    writes: usize,
}

impl<const N: usize> TestDisk<N> {
    fn new(defect: Defect) -> Self {
        Self {
            storage: image(),
            defect,
            writes: 0,
        }
    }

    fn validate(&self, block: u64, length: usize) -> Result<usize, BlockError> {
        if self.defect == Defect::AcceptHugeEmpty
            && N == 0
            && block == u64::MAX
            && length == BLOCK_SIZE
        {
            return Ok(0);
        }
        if ((self.defect == Defect::BlockBeforeLength && block != 0)
            || (self.defect == Defect::EmptyBlockBeforeLength && N == 0 && block != 0))
            && length == BLOCK_SIZE - 1
        {
            return Err(BlockError::OutOfBounds);
        }
        if length != BLOCK_SIZE {
            return Err(BlockError::InvalidBufferLength);
        }
        if block >= N as u64 {
            return Err(BlockError::OutOfBounds);
        }
        Ok(block as usize)
    }
}

impl<const N: usize> BlockDevice for TestDisk<N> {
    fn block_count(&self) -> u64 {
        if self.defect == Defect::WrongCount {
            N.saturating_sub(1) as u64
        } else {
            N as u64
        }
    }

    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        let index = self.validate(block, buffer.len())?;
        match self.defect {
            Defect::AcceptHugeEmpty if N == 0 => return Ok(()),
            Defect::CachedReads if self.writes == 0 => buffer.fill(0),
            Defect::UnfilledReads => (),
            Defect::ComplementReads => buffer.iter_mut().for_each(|b| *b = !*b),
            _ => buffer.copy_from_slice(&self.storage[index]),
        }
        Ok(())
    }

    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if self.defect == Defect::WriteBlockBeforeLength
            && block != 0
            && buffer.len() == BLOCK_SIZE + 1
        {
            return Err(BlockError::OutOfBounds);
        }
        if self.defect == Defect::AcceptHugeWriteEmpty
            && N == 0
            && block == u64::MAX
            && buffer.len() == BLOCK_SIZE
        {
            return Ok(());
        }
        let index = self.validate(block, buffer.len())?;
        if self.defect == Defect::AcceptHugeEmpty && N == 0 {
            return Ok(());
        }
        self.writes += 1;
        if (self.defect == Defect::DropFirstWrite && self.writes == 1)
            || (self.defect == Defect::DropMiddleWrite && index == 1)
        {
            return Ok(());
        }
        self.storage[index].copy_from_slice(buffer);
        if self.defect == Defect::SpillWrite && N > 1 {
            self.storage[(index + 1) % N][13] ^= 0xff;
        }
        Ok(())
    }

    fn flush(&mut self) -> Result<(), BlockError> {
        match self.defect {
            Defect::UnsupportedFlush => Err(BlockError::Unsupported),
            Defect::IoFlush => Err(BlockError::Io),
            Defect::ClobberOnFlush if N > 0 => {
                self.storage[0][7] ^= 1;
                Ok(())
            }
            _ => Ok(()),
        }
    }
}

fn rejected<const N: usize>(defect: Defect, scenario: &'static str, kind: FailureKind) {
    let mut disk = TestDisk::<N>::new(defect);
    let mut model = image::<N>();
    let error = run_block_contract("broken", &mut disk, &mut model, FlushExpectation::Supported)
        .unwrap_err();
    assert_eq!(error.backend, "broken");
    assert_eq!(error.scenario, scenario);
    assert_eq!(error.kind, kind);
}

#[test]
fn capacity_mismatch_uses_external_capacity_before_io() {
    rejected::<4>(
        Defect::WrongCount,
        "capacity",
        FailureKind::BlockCount {
            expected: 4,
            actual: 3,
        },
    );
}

#[test]
fn known_image_rejects_cache_only_and_fake_reads() {
    // 每个负例都与同镜像的合规设备对照；错误发生在写入前。
    for defect in [
        Defect::None,
        Defect::CachedReads,
        Defect::UnfilledReads,
        Defect::ComplementReads,
    ] {
        let mut disk = TestDisk::<3>::new(defect);
        let mut model = image::<3>();
        let result =
            run_block_contract("known", &mut disk, &mut model, FlushExpectation::Supported);
        if defect == Defect::None {
            result.unwrap();
        } else {
            let error = result.unwrap_err();
            assert_eq!(error.scenario, "known_data");
            assert!(matches!(error.kind, FailureKind::DataMismatch { .. }));
            assert_eq!(disk.writes, 0);
        }
    }
}

#[test]
fn dropped_writes_are_observed_before_any_later_write() {
    for (defect, block) in [(Defect::DropFirstWrite, 0), (Defect::DropMiddleWrite, 1)] {
        let mut disk = TestDisk::<4>::new(defect);
        let mut model = image::<4>();
        let error = run_block_contract("drop", &mut disk, &mut model, FlushExpectation::Supported)
            .unwrap_err();
        assert_eq!(error.scenario, "write_readback");
        assert_eq!(error.block, Some(block));
        assert!(matches!(error.kind, FailureKind::DataMismatch { .. }));
        assert_eq!(disk.writes, block as usize + 1);
    }
}

#[test]
fn first_write_cannot_hide_clobbered_unwritten_suffix() {
    let mut disk = TestDisk::<4>::new(Defect::SpillWrite);
    let mut model = image::<4>();
    let error = run_block_contract("spill", &mut disk, &mut model, FlushExpectation::Supported)
        .unwrap_err();
    assert_eq!(error.scenario, "write_readback");
    assert_eq!(error.block, Some(1));
    assert!(matches!(
        error.kind,
        FailureKind::DataMismatch { byte: 13, .. }
    ));
    assert_eq!(disk.writes, 1);
}

#[test]
fn parameter_matrix_covers_nonzero_valid_blocks_and_empty_devices() {
    let wrong = FailureKind::ReturnValue {
        expected: Err(BlockError::InvalidBufferLength),
        actual: Err(BlockError::OutOfBounds),
    };
    rejected::<4>(Defect::BlockBeforeLength, "invalid_length_read", wrong);
    rejected::<4>(
        Defect::WriteBlockBeforeLength,
        "invalid_length_write",
        wrong,
    );
    rejected::<0>(Defect::EmptyBlockBeforeLength, "invalid_length_read", wrong);
    rejected::<0>(
        Defect::AcceptHugeEmpty,
        "out_of_bounds_read",
        FailureKind::ReturnValue {
            expected: Err(BlockError::OutOfBounds),
            actual: Ok(()),
        },
    );
    rejected::<0>(
        Defect::AcceptHugeWriteEmpty,
        "out_of_bounds_write",
        FailureKind::ReturnValue {
            expected: Err(BlockError::OutOfBounds),
            actual: Ok(()),
        },
    );
}

#[test]
fn flush_expectation_is_explicit_and_checks_actual_backend() {
    let mut disk = TestDisk::<3>::new(Defect::UnsupportedFlush);
    let mut model = image::<3>();
    run_block_contract(
        "unsupported",
        &mut disk,
        &mut model,
        FlushExpectation::Unsupported,
    )
    .unwrap();
    rejected::<3>(
        Defect::UnsupportedFlush,
        "flush",
        FailureKind::ReturnValue {
            expected: Ok(()),
            actual: Err(BlockError::Unsupported),
        },
    );
    rejected::<3>(
        Defect::IoFlush,
        "flush",
        FailureKind::ReturnValue {
            expected: Ok(()),
            actual: Err(BlockError::Io),
        },
    );
    let mut disk = TestDisk::<3>::new(Defect::None);
    let mut model = image::<3>();
    let error = run_block_contract(
        "unexpected-success",
        &mut disk,
        &mut model,
        FlushExpectation::Unsupported,
    )
    .unwrap_err();
    assert_eq!(
        error.kind,
        FailureKind::ReturnValue {
            expected: Err(BlockError::Unsupported),
            actual: Ok(())
        }
    );
}

#[test]
fn flush_corruption_is_compared_with_model() {
    let mut disk = TestDisk::<3>::new(Defect::ClobberOnFlush);
    let mut model = image::<3>();
    let error = run_block_contract("flush", &mut disk, &mut model, FlushExpectation::Supported)
        .unwrap_err();
    assert_eq!(error.scenario, "flush_readback");
    assert_eq!(error.block, Some(0));
    assert!(matches!(
        error.kind,
        FailureKind::DataMismatch { byte: 7, .. }
    ));
}

#[test]
fn oversized_fixture_is_rejected_before_io() {
    let mut disk = TestDisk::<9>::new(Defect::None);
    let mut model = image::<9>();
    let error = run_block_contract("large", &mut disk, &mut model, FlushExpectation::Supported)
        .unwrap_err();
    assert_eq!(
        error.kind,
        FailureKind::FixtureTooLarge {
            limit: MAX_TEST_BLOCKS,
            actual: 9
        }
    );
    assert_eq!(disk.writes, 0);
    assert_eq!(disk.storage, image::<9>());
}

#[test]
fn zero_known_sector_still_requires_read_to_fill_buffer() {
    let mut disk = TestDisk::<1>::new(Defect::UnfilledReads);
    disk.storage = [[0; BLOCK_SIZE]];
    let mut model = disk.storage;
    let error =
        run_block_contract("zero", &mut disk, &mut model, FlushExpectation::Supported).unwrap_err();
    assert_eq!(error.scenario, "known_data");
    assert_eq!(
        error.kind,
        FailureKind::DataMismatch {
            byte: 0,
            expected: 0,
            actual: 0xff
        }
    );
}

#[test]
fn injected_check_rejects_empty_or_unsupported_fixtures() {
    let mut empty = TestDisk::<0>::new(Defect::None);
    let error = check_injected_failures("empty-fault", &mut empty, &mut []).unwrap_err();
    assert_eq!(error.kind, FailureKind::EmptyFixture);
    let mut disk = TestDisk::<1>::new(Defect::UnsupportedFlush);
    let mut model = image::<1>();
    let error = check_injected_failures("unsupported-fault", &mut disk, &mut model).unwrap_err();
    assert_eq!(error.scenario, "recovered_flush");
    assert_eq!(
        error.kind,
        FailureKind::ReturnValue {
            expected: Ok(()),
            actual: Err(BlockError::Unsupported)
        }
    );
}
