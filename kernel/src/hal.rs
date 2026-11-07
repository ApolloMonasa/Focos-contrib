//! 初期启动接口。设备、内存和中断契约在相应 spec 中逐步增加。

pub fn console_write(message: &str) {
    for byte in message.bytes() {
        crate::arch::console_putchar(byte);
    }
}

pub fn shutdown(failed: bool) -> ! {
    crate::arch::shutdown(failed)
}

/// 输出当前启动路径的成功标记，并执行对应的结束动作。
pub fn finish_boot() -> ! {
    #[cfg(feature = "vf2-bootelf")]
    {
        console_write(concat!(
            "FocOS ",
            env!("CARGO_PKG_VERSION"),
            ": VisionFive2 bootelf / SBI console\r\nFOCOS_VF2_BOOT_OK\r\n",
        ));
        crate::arch::halt()
    }
    #[cfg(not(feature = "vf2-bootelf"))]
    {
        console_write("FocOS: early S-mode boot\nFOCOS_BOOT_OK\n");
        shutdown(false)
    }
}
