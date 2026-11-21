#!/bin/sh
export PATH=/bin
export LC_ALL=C TZ=UTC TERM=dumb
umask 022
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/shm /tmp /var/tmp
mount -t tmpfs tmpfs /dev/shm
ip link set lo up
cd /suite
export LD_LIBRARY_PATH=/suite/lib
export LTP_COLORIZE_OUTPUT=0
run_group() {
 name=$1; limit=$2; shift 2
 echo "OUTPUT_AUDIT_BEGIN $name"
 timeout -s KILL "$limit" setsid "$@"
 status=$?
 echo "OUTPUT_AUDIT_END $name status=$status"
}
run_group basic 45 ./busybox sh ./basic_testcode.sh
run_group busybox 30 ./busybox sh ./busybox_testcode.sh
run_group lua 20 ./busybox sh ./lua_testcode.sh
run_group libctest 120 ./busybox sh ./libctest_testcode.sh
run_group libcbench 120 ./busybox sh ./libcbench_testcode.sh
run_group cyclictest 35 ./busybox sh ./cyclictest_testcode.sh
run_group iperf 60 ./busybox sh ./iperf_testcode.sh
killall iperf3
run_group netperf 60 ./busybox sh ./netperf_testcode.sh
killall netserver
run_group iozone 120 ./busybox sh ./iozone_testcode.sh
run_group lmbench 180 ./busybox sh ./lmbench_testcode.sh
run_group unixbench 600 ./busybox sh ./unixbench_testcode.sh
# 本实验只装入四个列明的 LTP 程序，不是完整比赛调用清单。
run_group ltp_subset 45 ./busybox sh ./ltp_testcode.sh
sync
echo OUTPUT_AUDIT_DONE
poweroff -f
