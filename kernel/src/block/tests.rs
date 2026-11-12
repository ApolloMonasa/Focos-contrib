use super::contract::{
    check_buffer_length_validation, check_empty_device, check_first_last_write_preserves_neighbors,
    check_flush_failure_retry, check_flush_ok, check_flush_unsupported,
    check_io_failures_recoverable, check_known_data, check_out_of_bounds_rejected,
    check_read_write_roundtrip,
};
use super::{BLOCK_SIZE, BlockDevice, BlockError, MemoryDisk, contract};

#[test]
fn memory_contract_read_write_roundtrip() {
    let original: [u8; BLOCK_SIZE] = core::array::from_fn(|i| (i ^ (i >> 8)) as u8);
    let mut storage = original;
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    // 破坏性写入前先确认设备读回镜像已有内容（已知数据读取）。
    check_known_data(&mut disk, 0, &original).unwrap();
    check_read_write_roundtrip(&mut disk).unwrap();
}

#[test]
fn memory_contract_first_last_write_preserves_neighbors() {
    let mut storage = [0x33; BLOCK_SIZE * 3];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    assert_eq!(disk.block_count(), 3);
    check_first_last_write_preserves_neighbors(&mut disk).unwrap();
}

#[test]
fn memory_contract_out_of_bounds_rejected() {
    let mut storage = [0x33; BLOCK_SIZE * 2];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    // 两块测试盘上契约探针覆盖全部块，等价于原先的全盘直查。
    check_out_of_bounds_rejected(&mut disk).unwrap();
}

#[test]
fn memory_contract_buffer_length_validation() {
    let mut storage = [0x33; BLOCK_SIZE];
    {
        let mut disk = MemoryDisk::new(&mut storage).unwrap();
        check_buffer_length_validation(&mut disk).unwrap();
    }
    // 底层存储未被参数错误的操作改动（后端侧直查，契约层看不到存储）。
    assert_eq!(storage, [0x33; BLOCK_SIZE]);
}

#[test]
fn rejects_unaligned_capacity_without_modifying_storage() {
    for length in [1, BLOCK_SIZE - 1, BLOCK_SIZE + 1] {
        let mut storage = [0x33; BLOCK_SIZE + 1];
        assert!(matches!(
            MemoryDisk::new(&mut storage[..length]),
            Err(BlockError::InvalidCapacity)
        ));
        assert_eq!(storage, [0x33; BLOCK_SIZE + 1]);
    }
}

#[test]
fn memory_contract_empty_device_has_no_valid_blocks() {
    let mut storage = [];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    check_empty_device(&mut disk).unwrap();
    // 空盘 flush 恒成功是内存盘语义，留在后端侧。
    disk.flush().unwrap();
}

#[test]
fn memory_contract_flush_ok_preserves_data() {
    let mut storage = [0x33; BLOCK_SIZE];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    check_flush_ok(&mut disk).unwrap();
}

#[test]
fn memory_flush_releases_storage_with_written_data() {
    let mut storage = [0x33; BLOCK_SIZE];
    {
        let mut disk = MemoryDisk::new(&mut storage).unwrap();
        disk.write_block(0, &[0x55; BLOCK_SIZE]).unwrap();
        disk.flush().unwrap();
    }
    // 借用归还是内存盘专属判据：释放后写入仍在原缓冲区（HAL-001 容量与借用）。
    assert_eq!(storage, [0x55; BLOCK_SIZE]);
}

#[test]
fn memory_contract_unsupported_sync_reaches_caller() {
    let mut storage = [0; BLOCK_SIZE];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    let device: &mut dyn BlockDevice = &mut disk;
    let mut no_sync = contract::NoSyncDevice::new(device);
    check_flush_unsupported(&mut no_sync).unwrap();
}

#[test]
fn memory_contract_io_failures_reach_caller() {
    let mut storage = [0x33; BLOCK_SIZE];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    let device: &mut dyn BlockDevice = &mut disk;
    let mut faulty = contract::FaultyDevice::new(device);
    check_io_failures_recoverable(&mut faulty).unwrap();
}

#[test]
fn memory_contract_failed_sync_reaches_caller_and_can_be_retried() {
    let mut storage = [0; BLOCK_SIZE];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    let device: &mut dyn BlockDevice = &mut disk;
    let mut faulty = contract::FaultyDevice::new(device);
    check_flush_failure_retry(&mut faulty).unwrap();
}

#[test]
fn memory_contract_run_block_contract_passes_all_scenarios() {
    let mut storage = [0x33; BLOCK_SIZE * 3];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    contract::run_block_contract("memory-disk", &mut disk, 3).unwrap();
}

#[test]
fn memory_contract_run_block_contract_reports_backend_on_count_mismatch() {
    let mut storage = [0x33; BLOCK_SIZE * 3];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    assert_eq!(
        contract::run_block_contract("memory-disk", &mut disk, 4),
        Err(contract::BlockContractError {
            backend: "memory-disk",
            failure: contract::ContractFailure {
                scenario: "block_count",
                kind: contract::FailureKind::WrongBlockCount {
                    expected: 4,
                    actual: 3
                }
            }
        })
    );
}

/// 非合规设备：读路径先查块号再查长度，违反长度错误优先约定。
struct BlockFirstReadDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for BlockFirstReadDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if block >= self.block_count() {
            return Err(BlockError::OutOfBounds);
        }
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.inner.write_block(block, buffer)
    }
}

#[test]
fn memory_contract_length_priority_covers_read_path() {
    let mut storage = [0x33; BLOCK_SIZE];
    let mut disk = BlockFirstReadDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_buffer_length_validation(&mut disk).is_err());
}

/// 非合规设备：写入某块时把相同数据串写到下一块。
struct SpillingWriteDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for SpillingWriteDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.inner.write_block(block, buffer)?;
        let next = block + 1;
        if next < self.block_count() {
            self.inner.write_block(next, buffer)?;
        }
        Ok(())
    }
}

#[test]
fn memory_contract_neighbor_check_covers_adjacent_blocks() {
    let mut storage = [0x33; BLOCK_SIZE * 4];
    let mut disk = SpillingWriteDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_first_last_write_preserves_neighbors(&mut disk).is_err());
}

/// 非合规设备：读只回放会话内最后写入的缓存，未写块返回伪造内容，
/// 从不读取介质原有数据。
struct WriteOnlyCacheDisk<'a> {
    inner: MemoryDisk<'a>,
    cached: Option<(u64, [u8; BLOCK_SIZE])>,
}

impl BlockDevice for WriteOnlyCacheDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if block >= self.block_count() {
            return Err(BlockError::OutOfBounds);
        }
        if buffer.len() != BLOCK_SIZE {
            return Err(BlockError::InvalidBufferLength);
        }
        match self.cached {
            Some((cached_block, data)) if cached_block == block => {
                buffer.copy_from_slice(&data);
            }
            _ => {
                buffer.fill(0xAA);
            }
        }
        Ok(())
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if block >= self.block_count() {
            return Err(BlockError::OutOfBounds);
        }
        if buffer.len() != BLOCK_SIZE {
            return Err(BlockError::InvalidBufferLength);
        }
        let data: [u8; BLOCK_SIZE] = buffer.try_into().unwrap();
        self.cached = Some((block, data));
        Ok(())
    }
    fn flush(&mut self) -> Result<(), BlockError> {
        Ok(())
    }
}

#[test]
fn memory_contract_known_data_rejects_cache_only_reads() {
    // 只回放会话内写入的假后端能骗过写后读检查，
    // 必须在破坏性写入前的已知数据检查处失败。
    let original: [u8; BLOCK_SIZE] = core::array::from_fn(|i| (i ^ (i >> 8)) as u8);
    let mut storage = original;
    let mut disk = WriteOnlyCacheDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
        cached: None,
    };
    check_read_write_roundtrip(&mut disk).unwrap();
    assert!(check_known_data(&mut disk, 0, &original).is_err());
}
