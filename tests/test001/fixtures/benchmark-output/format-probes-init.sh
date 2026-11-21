#!/bin/sh
export PATH=/bin LC_ALL=C TZ=UTC TERM=dumb
umask 022
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/shm /tmp /var/tmp
mount -t tmpfs tmpfs /dev/shm
cd /suite
export LD_LIBRARY_PATH=/suite/lib UB_BINDIR=./
ln -s libc.so /suite/lib/ld-musl-riscv64-sf.so.1
run_group() {
 name=$1; shift
 echo "OUTPUT_AUDIT_BEGIN $name"
 timeout -s KILL 25 setsid "$@"
 status=$?
 echo "OUTPUT_AUDIT_END $name status=$status"
}
# 参数缩短的格式探针，不等同原组脚本/性能成绩。
for name in dhry2reg whetstone-double syscall context1 pipe spawn execl arithoh short int long float double hanoi; do
 run_group unix_$name ./$name 1
done
for flag in w r c; do
 run_group unix_fstime_$flag ./fstime -$flag -t 1 -b 256 -m 500
done
run_group unix_shell1 ./looper 1 ./multi.sh 1
run_group unix_shell8 ./looper 1 ./multi.sh 8
run_group unix_shell16 ./looper 1 ./multi.sh 16
run_group unix_exec ./syscall 1 exec
run_group lmdd_file ./lmbench_all lmdd label="File /var/tmp/XXX write bandwidth:" of=/var/tmp/XXX move=1m fsync=1 print=3
run_group lm_syscall ./lmbench_all lat_syscall -N 1 -P 1 null
run_group lm_bw_pipe ./lmbench_all bw_pipe -N 1 -P 1
run_group lm_bw_file ./lmbench_all bw_file_rd -N 1 -P 1 512k io_only /var/tmp/XXX
run_group lm_mmap ./lmbench_all lat_mmap -N 1 -P 1 512k /var/tmp/XXX
run_group lm_ctx ./lmbench_all lat_ctx -N 1 -P 1 -s 32 2 4
run_group lm_fs ./lmbench_all lat_fs -N 1 /var/tmp
sync
echo OUTPUT_AUDIT_DONE
poweroff -f
