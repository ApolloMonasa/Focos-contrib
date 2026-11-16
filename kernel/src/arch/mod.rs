//! CPU 架构入口与固件调用，不持有板级内存布局。

#[cfg(target_arch = "riscv64")]
mod riscv64;
#[cfg(target_arch = "riscv64")]
pub use riscv64::{
    IrqCheckError, check_local_irq_state, console_putchar, local_irqs_enabled, shutdown,
    with_local_irqs_disabled,
};
