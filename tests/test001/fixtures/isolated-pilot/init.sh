#!/bin/sh
export PATH=/bin LC_ALL=C TZ=UTC TERM=dumb
umask 022
/bin/busybox --install -s /bin
mount -t proc proc /proc || poweroff -f
mount -t sysfs sysfs /sys || poweroff -f
mount -t devtmpfs devtmpfs /dev || poweroff -f
mkdir -p /tmp /var/tmp /dev/shm
mount -t tmpfs tmpfs /dev/shm || poweroff -f
cd /suite || poweroff -f
export LD_LIBRARY_PATH=/suite/lib LTP_COLORIZE_OUTPUT=0
printf 'PILOT BEGIN\n'
/bin/sh /suite/pilot-case.sh >/tmp/stdout 2>/tmp/stderr &
child=$!
printf 'PILOT CHILD %s\n' "$child"
wait "$child"
status=$?
printf 'PILOT STATUS %s\n' "$status"
# No external helper runs during enumeration. Zombies and live user tasks count.
for file in /proc/[0-9]*/stat; do
    read -r pid comm state rest <"$file" || continue
    [ "$pid" = 1 ] && continue
    if [ -e "/proc/$pid/exe" ] || [ "$state" = Z ]; then
        printf 'PILOT RESIDUAL %s %s\n' "$pid" "$state"
    fi
done
for stream in stdout stderr; do
    printf 'PILOT %s ' "$stream"
    od -An -v -tx1 "/tmp/$stream" | tr -d ' \n'
    printf '\n'
done
printf 'PILOT END\n'
poweroff -f
