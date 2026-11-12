//! 契约检查自身的负例：每个非合规设备对应一类真实后端可能的缺陷，
//! 验证检查能在对应场景下拒绝它。

use super::{
    check_buffer_length_validation, check_first_last_write_preserves_neighbors, check_known_data,
    check_out_of_bounds_rejected, check_read_write_roundtrip,
};
use crate::block::{BLOCK_SIZE, BlockDevice, BlockError, MemoryDisk};

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
fn length_priority_covers_read_path() {
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
fn neighbor_check_covers_adjacent_blocks() {
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
fn known_data_rejects_cache_only_reads() {
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

/// 非合规设备：写首块时把垃圾数据串写到末块，模拟地址回绕式跨块破坏。
struct FirstWriteClobbersLastDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for FirstWriteClobbersLastDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.inner.write_block(block, buffer)?;
        if block == 0 && self.block_count() > 1 {
            let last = self.block_count() - 1;
            self.inner.write_block(last, &[0xEE; BLOCK_SIZE])?;
        }
        Ok(())
    }
}

#[test]
fn neighbor_check_catches_clobber_before_healing() {
    // 写末块会治愈首块造成的破坏，检查必须在写末块之前发现它。
    let mut storage = [0x33; BLOCK_SIZE * 4];
    let mut disk = FirstWriteClobbersLastDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_first_last_write_preserves_neighbors(&mut disk).is_err());
}

/// 非合规设备：被拒的非法长度写入先把垃圾写进第 0 块，再返回长度错误。
struct DestructiveRejectDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for DestructiveRejectDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if buffer.len() != BLOCK_SIZE {
            self.inner.write_block(0, &[0xEE; BLOCK_SIZE])?;
            return Err(BlockError::InvalidBufferLength);
        }
        self.inner.write_block(block, buffer)
    }
}

#[test]
fn length_validation_rejects_destructive_errors() {
    // 只查调用者缓冲区发现不了"先破坏再报错"的后端，必须重读目标块。
    let mut storage = [0x33; BLOCK_SIZE];
    let mut disk = DestructiveRejectDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_buffer_length_validation(&mut disk).is_err());
}

/// 非合规设备：对空切片特判返回长度错误，对非空长度却先查块号。
struct EmptySpecialCaseDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for EmptySpecialCaseDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if buffer.is_empty() {
            return Err(BlockError::InvalidBufferLength);
        }
        if block >= self.block_count() {
            return Err(BlockError::OutOfBounds);
        }
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if buffer.is_empty() {
            return Err(BlockError::InvalidBufferLength);
        }
        if block >= self.block_count() {
            return Err(BlockError::OutOfBounds);
        }
        self.inner.write_block(block, buffer)
    }
}

#[test]
fn length_priority_covers_all_illegal_lengths() {
    // 非空非法长度也必须与非法块号组合验证长度优先，
    // 否则"只特判空切片"的后端能绕过优先级约定。
    let mut storage = [0x33; BLOCK_SIZE];
    let mut disk = EmptySpecialCaseDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_buffer_length_validation(&mut disk).is_err());
}

/// 非合规设备：越界访问被拒时误写第 1 块（模拟越界偏移换算错误）。
struct OobSpillDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for OobSpillDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if block >= self.block_count() {
            let _ = self.inner.write_block(1, &[0xEE; BLOCK_SIZE]);
            return Err(BlockError::OutOfBounds);
        }
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        if block >= self.block_count() {
            let _ = self.inner.write_block(1, &[0xEE; BLOCK_SIZE]);
            return Err(BlockError::OutOfBounds);
        }
        self.inner.write_block(block, buffer)
    }
}

#[test]
fn out_of_bounds_check_covers_every_block() {
    // 4 块盘上采样探针会漏掉第 1 块，越界被拒时写坏它必须被检出。
    let mut storage = [0x33; BLOCK_SIZE * 4];
    let mut disk = OobSpillDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_out_of_bounds_rejected(&mut disk).is_err());
}

/// 非合规设备：写首块或末块时误写中间的第 2 块（模拟跨块地址串扰）。
struct MidSpillDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for MidSpillDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.inner.write_block(block, buffer)?;
        let count = self.block_count();
        if block == 0 || block == count - 1 {
            self.inner.write_block(2, &[0xEE; BLOCK_SIZE])?;
        }
        Ok(())
    }
}

#[test]
fn neighbor_check_covers_every_block() {
    // 6 块盘上采样探针会漏掉第 2 块，写首末块时写坏它必须被检出。
    let mut storage = [0x33; BLOCK_SIZE * 6];
    let mut disk = MidSpillDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_first_last_write_preserves_neighbors(&mut disk).is_err());
}

/// 非合规设备：读取路径丢失块号 bit 8，把第 257 块读成第 1 块。
struct AliasingReadDisk<'a> {
    inner: MemoryDisk<'a>,
}

impl BlockDevice for AliasingReadDisk<'_> {
    fn block_count(&self) -> u64 {
        self.inner.block_count()
    }
    fn read_block(&mut self, block: u64, buffer: &mut [u8]) -> Result<(), BlockError> {
        if block == 257 {
            return self.inner.read_block(1, buffer);
        }
        self.inner.read_block(block, buffer)
    }
    fn write_block(&mut self, block: u64, buffer: &[u8]) -> Result<(), BlockError> {
        self.inner.write_block(block, buffer)
    }
}

#[test]
fn probe_patterns_distinguish_all_block_numbers() {
    // 块 1 与块 257 的图样必须不同，读混相差 256 整数倍块的实现才能被检出。
    let mut storage = [0x33; BLOCK_SIZE * 259];
    let mut disk = AliasingReadDisk {
        inner: MemoryDisk::new(&mut storage).unwrap(),
    };
    assert!(check_out_of_bounds_rejected(&mut disk).is_err());
}
