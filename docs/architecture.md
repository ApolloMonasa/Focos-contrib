# 一次程序运行会经过哪些层

这是目标架构，图中的兼容层与存储层仍待实现。已验证的启动能力见 README 的当前状态；图不表示完成度。

```mermaid
flowchart TB
    accTitle: FocOS 的区域赛与全国赛目标架构
    accDescr: 区域赛运行 2025 测试和开发工具，经 Linux 系统调用访问共享内核。VFS 下同时有 FAT32 和 Ext4。HAL 隔离硬件；全国赛扩展 LoongArch 与磁盘检索。
    APP["区域赛用户态<br/>2025 非 LTP 测试 · vim / gcc / make"]
    ANN["全国赛用户态<br/>按需读盘的 HNSW 查询"]
    ABI["Linux 系统调用接口<br/>musl / glibc 兼容"]
    CORE["内存 · 进程 · 线程<br/>ELF 装载 · 信号 · 时钟"]
    VFS["VFS<br/>路径 · 文件描述符 · 挂载"]
    IO["终端 · 管道 · socket<br/>TCP / UDP loopback"]
    FAT["FAT32<br/>基础文件系统与优化"]
    EXT["Ext4<br/>2025 原版镜像兼容"]
    CACHE["块设备接口与缓存<br/>可关闭策略做对照"]
    HAL["HAL / arch<br/>CPU 操作与设备驱动"]
    RV["区域赛 RISC-V<br/>QEMU virt · VisionFive2"]
    LA["全国赛 LoongArch<br/>QEMU · 2K1000LA 星云板"]
    APP --> ABI
    ANN --> ABI
    ABI --> CORE
    ABI --> VFS
    ABI --> IO
    VFS --> FAT
    VFS --> EXT
    FAT --> CACHE
    EXT --> CACHE
    CACHE --> HAL
    CORE --> HAL
    IO --> HAL
    HAL --> RV
    HAL --> LA
    classDef later stroke-dasharray: 5 3;
    class ANN,LA later;
```

## 读图的三个要点

- **程序兼容**：gcc 能运行需要加载器、进程、文件和内存共同正确；网络组还需要真实 socket 与回环收发。
- **文件系统**：FAT32 负责基础与优化实验，Ext4 读取并写回历史原版镜像；二者共享 VFS，分别验收格式行为。
- **平台移植**：页表、陷入与设备差异收敛到 HAL/arch；FS/VFS 无架构条件编译。全国赛将同一套上层实现搬到星云板。

AI 记忆检索提供后续压力负载。先测真实访问，再决定缓存或布局优化；全索引驻留内存的查询不能证明文件 I/O 收益。接口细节见 [模块边界](reference/delivery-details.md)。
