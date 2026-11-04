OUTPUT_ARCH(riscv)
ENTRY(_start)

SECTIONS {
    . = 0x80200000;
    .text : { KEEP(*(.text.entry)) *(.text .text.*) }
    . = ALIGN(4096);
    .rodata : { *(.rodata .rodata.*) *(.srodata .srodata.*) }
    . = ALIGN(4096);
    .data : { *(.data .data.*) *(.sdata .sdata.*) }
    . = ALIGN(4096);
    __bss_start = .;
    .bss (NOLOAD) : { *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) }
    . = ALIGN(8);
    __bss_end = .;
    /DISCARD/ : { *(.eh_frame .eh_frame_hdr) }
}
