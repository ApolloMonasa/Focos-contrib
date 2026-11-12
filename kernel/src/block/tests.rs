use super::contract::{check_buffer_length_validation, check_read_write_roundtrip};
use super::{BLOCK_SIZE, BlockDevice, BlockError, MemoryDisk};

#[test]
fn memory_contract_read_write_roundtrip() {
    let original: [u8; BLOCK_SIZE] = core::array::from_fn(|i| (i ^ (i >> 8)) as u8);
    let mut storage = original;
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    // 构造保留已有内容是内存盘专属判据（HAL-001 容量与借用场景），留在后端侧。
    let mut buffer = [0; BLOCK_SIZE];
    disk.read_block(0, &mut buffer).unwrap();
    assert_eq!(buffer, original);
    check_read_write_roundtrip(&mut disk).unwrap();
}

#[test]
fn writes_first_and_last_blocks_without_changing_neighbors() {
    let mut storage = [0x33; BLOCK_SIZE * 3];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    let device: &mut dyn BlockDevice = &mut disk;
    assert_eq!(device.block_count(), 3);
    device.write_block(0, &[0x11; BLOCK_SIZE]).unwrap();
    device.write_block(2, &[0x22; BLOCK_SIZE]).unwrap();
    let mut buffer = [0; BLOCK_SIZE];
    for (block, expected) in [(0, 0x11), (1, 0x33), (2, 0x22)] {
        device.read_block(block, &mut buffer).unwrap();
        assert_eq!(buffer, [expected; BLOCK_SIZE]);
    }
}

#[test]
fn rejects_out_of_bounds_and_huge_indices_without_changes() {
    let mut storage = [0x33; BLOCK_SIZE * 2];
    {
        let mut disk = MemoryDisk::new(&mut storage).unwrap();
        for block in [2, u64::MAX] {
            let mut buffer = [0x77; BLOCK_SIZE];
            assert_eq!(
                disk.read_block(block, &mut buffer),
                Err(BlockError::OutOfBounds)
            );
            assert_eq!(buffer, [0x77; BLOCK_SIZE]);
            assert_eq!(
                disk.write_block(block, &[0x99; BLOCK_SIZE]),
                Err(BlockError::OutOfBounds)
            );
        }
    }
    assert_eq!(storage, [0x33; BLOCK_SIZE * 2]);
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
fn empty_disk_has_no_valid_blocks() {
    let mut storage = [];
    let mut disk = MemoryDisk::new(&mut storage).unwrap();
    assert_eq!(disk.block_count(), 0);
    let mut buffer = [0x77; BLOCK_SIZE];
    assert_eq!(
        disk.read_block(0, &mut buffer),
        Err(BlockError::OutOfBounds)
    );
    assert_eq!(buffer, [0x77; BLOCK_SIZE]);
    assert_eq!(disk.write_block(0, &buffer), Err(BlockError::OutOfBounds));
    assert_eq!(disk.flush(), Ok(()));
}

#[test]
fn memory_flush_preserves_data_and_releasing_disk_returns_storage() {
    let mut storage = [0x33; BLOCK_SIZE];
    {
        let mut disk = MemoryDisk::new(&mut storage).unwrap();
        disk.write_block(0, &[0x55; BLOCK_SIZE]).unwrap();
        disk.flush().unwrap();
        let mut buffer = [0; BLOCK_SIZE];
        disk.read_block(0, &mut buffer).unwrap();
        assert_eq!(buffer, [0x55; BLOCK_SIZE]);
    }
    assert_eq!(storage, [0x55; BLOCK_SIZE]);
}

/// 仅提供读写，刻意不覆盖默认 flush。
struct NoSyncDisk<'a>(MemoryDisk<'a>);

impl BlockDevice for NoSyncDisk<'_> {
    fn block_count(&self) -> u64 {
        self.0.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        self.0.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.0.write_block(block, buffer)
    }
}

#[test]
fn unsupported_sync_reaches_caller() {
    let mut storage = [0; BLOCK_SIZE];
    let mut disk = NoSyncDisk(MemoryDisk::new(&mut storage).unwrap());
    let device: &mut dyn BlockDevice = &mut disk;
    device.write_block(0, &[0x55; BLOCK_SIZE]).unwrap();
    assert_eq!(device.flush(), Err(BlockError::Unsupported));
}

/// 故障只用于验证接口调用者能观察到错误，不模拟真实硬件持久化。
struct FaultDisk<'a> {
    inner: MemoryDisk<'a>,
    fail_read: bool,
    fail_write: bool,
    fail_sync: bool,
}

impl BlockDevice for FaultDisk<'_> {
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
        if self.fail_sync {
            return Err(BlockError::Io);
        }
        self.inner.flush()
    }
}

#[test]
fn read_and_write_failures_reach_caller() {
    let mut storage = [0x33; BLOCK_SIZE];
    let mut disk = FaultDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
        fail_read: true,
        fail_write: true,
        fail_sync: false,
    };
    let device: &mut dyn BlockDevice = &mut disk;
    let mut buffer = [0x77; BLOCK_SIZE];
    assert_eq!(device.read_block(0, &mut buffer), Err(BlockError::Io));
    assert_eq!(buffer, [0x77; BLOCK_SIZE]);
    assert_eq!(
        device.write_block(0, &[0x55; BLOCK_SIZE]),
        Err(BlockError::Io)
    );
    disk.fail_read = false;
    disk.read_block(0, &mut buffer).unwrap();
    assert_eq!(buffer, [0x33; BLOCK_SIZE]);
}

#[test]
fn failed_sync_reaches_caller_and_can_be_retried() {
    let mut storage = [0; BLOCK_SIZE];
    let mut disk = FaultDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
        fail_read: false,
        fail_write: false,
        fail_sync: true,
    };
    let device: &mut dyn BlockDevice = &mut disk;
    device.write_block(0, &[0x55; BLOCK_SIZE]).unwrap();
    assert_eq!(device.flush(), Err(BlockError::Io));
    disk.fail_sync = false;
    let device: &mut dyn BlockDevice = &mut disk;
    device.flush().unwrap();
    let mut buffer = [0; BLOCK_SIZE];
    device.read_block(0, &mut buffer).unwrap();
    assert_eq!(buffer, [0x55; BLOCK_SIZE]);
}
