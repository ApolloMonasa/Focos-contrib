#!/bin/sh
export PATH=/bin LC_ALL=C TZ=UTC TERM=dumb
umask 022
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/shm /tmp /var/tmp
mount -t tmpfs tmpfs /dev/shm
ip link set lo up
cd /suite
export LD_LIBRARY_PATH=/suite/lib LTP_COLORIZE_OUTPUT=0
# 显式实验适配：镜像缺少 musl ELF 请求的绝对解释器名。
ln -s libc.so /suite/lib/ld-musl-riscv64-sf.so.1
run_group() {
 name=$1; limit=$2; shift 2
 echo "OUTPUT_AUDIT_BEGIN $name"
 timeout -s KILL "$limit" setsid "$@"
 status=$?
 echo "OUTPUT_AUDIT_END $name status=$status"
}
# 原 run-all.sh 没有执行权限；本轮显式交给 shell，原文件和 mode 不改。
cd /suite/basic
run_group basic_explicit_shell 45 /bin/sh ./run-all.sh
cd /suite
run_group libctest_pass_exit 10 ./runtest.exe -w entry-static.exe argv
run_group libctest_missing_exit 10 ./runtest.exe __audit_missing
run_group ltp_subset 45 ./busybox sh ./ltp_testcode.sh
run_group netperf_loader 60 ./busybox sh ./netperf_testcode.sh
killall netserver
run_group iozone_loader 120 ./busybox sh ./iozone_testcode.sh
sync
echo OUTPUT_AUDIT_DONE
poweroff -f
