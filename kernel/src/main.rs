#![no_std]
#![no_main]

mod arch;
mod hal;

#[unsafe(no_mangle)]
extern "C" fn kernel_main() -> ! {
    hal::console_write("FocOS: early S-mode boot\nFOCOS_BOOT_OK\n");
    hal::shutdown(false)
}

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo<'_>) -> ! {
    hal::console_write("FOCOS_PANIC\n");
    hal::shutdown(true)
}
