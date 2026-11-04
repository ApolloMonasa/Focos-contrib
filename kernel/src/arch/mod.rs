#[cfg(target_arch = "riscv64")]
mod riscv64;
#[cfg(target_arch = "riscv64")]
pub use riscv64::{console_putchar, shutdown};

#[cfg(not(target_arch = "riscv64"))]
compile_error!("BOOT-001 currently supports only RISC-V; LoongArch is a future milestone");
