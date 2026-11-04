    .section .text.entry
    .globl _start
_start:
    # BOOT-001 只接受单 hart，QEMU virt 的启动 hart 为 0。
    bnez a0, .Lpark
    la sp, boot_stack_top
    la t0, __bss_start
    la t1, __bss_end
.Lclear_bss:
    bgeu t0, t1, .Lenter_rust
    sd zero, 0(t0)
    addi t0, t0, 8
    j .Lclear_bss
.Lenter_rust:
    tail kernel_main
.Lpark:
    wfi
    j .Lpark

    .section .bss.stack
    .balign 16
boot_stack_bottom:
    .space 65536
boot_stack_top:
