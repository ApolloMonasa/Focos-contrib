//! 保存、关闭和恢复本核可屏蔽中断，供 `with_local_irqs_disabled` 使用。

use core::arch::asm;
use core::sync::atomic::{Ordering, compiler_fence};

/// `sstatus.SIE`：S 模式全局中断使能位。
const SSTATUS_SIE: usize = 1 << 1;

/// 读取本核当前是否允许 S 模式可屏蔽中断。
#[inline]
pub fn local_irqs_enabled() -> bool {
    let sstatus: usize;
    // SAFETY: 只读 sstatus，不访问内存，也不改变 CPU 状态。
    unsafe {
        asm!(
            "csrr {sstatus}, sstatus",
            sstatus = out(reg) sstatus,
            options(nomem, nostack),
        );
    }
    sstatus & SSTATUS_SIE != 0
}

/// 在关闭本核可屏蔽中断的状态下执行一次 `f`，返回时恢复进入前的 SIE。
///
/// 可以嵌套：内层保存到的 SIE 已为 0，退出内层不会提前开启中断；
/// 最外层按保存值恢复。闭包只执行一次，返回值原样透传。
///
/// # 调用前提
/// - 闭包不得阻塞、让出 CPU、迁移到其他核或自行修改中断状态。
/// - 只屏蔽本核当前 hart 的可屏蔽中断，不提供跨核互斥，也不代替设备
///   或跨核内存屏障。
/// - 内核 panic 的终止路径不承诺恢复中断状态后继续运行。
///
/// 关中断与恢复之间使用编译器屏障，阻止闭包内外的访存越过边界；
/// 依据和负例见 `docs/reference/local-irq-critical-section.md`。
#[inline]
pub fn with_local_irqs_disabled<R>(f: impl FnOnce() -> R) -> R {
    // SAFETY: csrrc 只清除 sstatus.SIE 并返回旧值，不访问内存。
    let saved = unsafe { disable_local_irqs() };
    // 编译器屏障：阻止闭包内访存被上移到关中断之前。
    compiler_fence(Ordering::SeqCst);
    let result = f();
    // 编译器屏障：阻止闭包内访存被下移到恢复中断之后。
    compiler_fence(Ordering::SeqCst);
    // SAFETY: 按保存值只恢复 sstatus.SIE，不触碰其他 sstatus 位。
    unsafe { restore_local_irqs(saved) };
    result
}

/// 关闭 SIE 并返回关闭前的 sstatus；其他位保持不变。
///
/// # Safety
/// 只能由当前 hart 在可安全关中断的上下文中调用；返回的原值须原样交给
/// `restore_local_irqs`。本函数不提供编译器屏障，调用者须自行约束访存顺序。
#[inline]
unsafe fn disable_local_irqs() -> usize {
    let saved: usize;
    // SAFETY: csrrc 原子地读取 sstatus 并清除 SIE；不访问内存。
    unsafe {
        asm!(
            "csrrc {saved}, sstatus, {mask}",
            saved = out(reg) saved,
            mask = in(reg) SSTATUS_SIE,
            options(nomem, nostack),
        );
    }
    saved
}

/// 按保存值恢复 SIE；只写 SIE 位，避免覆盖其他 sstatus 位。
///
/// # Safety
/// `saved` 必须来自同一 hart 上成对的 `disable_local_irqs`，且中间没有
/// 其他代码修改 sstatus；调用者须先完成受保护访存。
#[inline]
unsafe fn restore_local_irqs(saved: usize) {
    if saved & SSTATUS_SIE != 0 {
        // SAFETY: csrs 只置位 SIE，不访问内存。
        unsafe {
            asm!(
                "csrs sstatus, {mask}",
                mask = in(reg) SSTATUS_SIE,
                options(nomem, nostack),
            );
        }
    } else {
        // SAFETY: csrc 只清零 SIE，不访问内存。
        unsafe {
            asm!(
                "csrc sstatus, {mask}",
                mask = in(reg) SSTATUS_SIE,
                options(nomem, nostack),
            );
        }
    }
}

/// 仅供启动自检切换 SIE 初始状态；不用于生产临界区。
///
/// # Safety
/// 只能在尚未启用任何中断源、且调用者会在返回前恢复关闭状态时调用。
#[inline]
pub(super) unsafe fn set_local_irqs_enabled(enabled: bool) {
    if enabled {
        // SAFETY: 只置位 SIE；调用前提保证没有可触发的已启用中断源。
        unsafe {
            asm!("csrsi sstatus, 2", options(nomem, nostack));
        }
    } else {
        // SAFETY: 只清零 SIE。
        unsafe {
            asm!("csrci sstatus, 2", options(nomem, nostack));
        }
    }
}
