"""冻结非 LTP 格式表；词法和语义共享，不包含执行成绩。"""

LMBENCH_COMMANDS = (
    ('lat_syscall', '-P', '1', 'null'),
    ('lat_syscall', '-P', '1', 'read'),
    ('lat_syscall', '-P', '1', 'write'),
    ('lat_syscall', '-P', '1', 'stat', '/var/tmp/lmbench'),
    ('lat_syscall', '-P', '1', 'fstat', '/var/tmp/lmbench'),
    ('lat_syscall', '-P', '1', 'open', '/var/tmp/lmbench'),
    ('lat_select', '-n', '100', '-P', '1', 'file'),
    ('lat_sig', '-P', '1', 'install'),
    ('lat_sig', '-P', '1', 'catch'),
    ('lat_sig', '-P', '1', 'prot', 'lat_sig'),
    ('lat_pipe', '-P', '1'),
    ('lat_proc', '-P', '1', 'fork'),
    ('lat_proc', '-P', '1', 'exec'),
    ('lat_proc', '-P', '1', 'shell'),
    (
        'lmdd',
        'label=File /var/tmp/XXX write bandwidth:',
        'of=/var/tmp/XXX',
        'move=1m',
        'fsync=1',
        'print=3',
    ),
    ('lat_pagefault', '-P', '1', '/var/tmp/XXX'),
    ('lat_mmap', '-P', '1', '512k', '/var/tmp/XXX'),
    ('bw_pipe', '-P', '1'),
    ('lat_fs', '/var/tmp'),
    ('bw_file_rd', '-P', '1', '512k', 'io_only', '/var/tmp/XXX'),
    ('bw_file_rd', '-P', '1', '512k', 'open2close', '/var/tmp/XXX'),
    ('bw_mmap_rd', '-P', '1', '512k', 'mmap_only', '/var/tmp/XXX'),
    ('bw_mmap_rd', '-P', '1', '512k', 'open2close', '/var/tmp/XXX'),
    ('lat_ctx', '-P', '1', '-s', '32', '2', '4', '8', '16', '24', '32', '64', '96'),
)
LMBENCH_INDEX = {command: index for index, command in enumerate(LMBENCH_COMMANDS)}
LMBENCH_LABELS = {
    'Simple syscall',
    'Simple read',
    'Simple write',
    'Simple stat',
    'Simple fstat',
    'Simple open/close',
    "Select on 100 fd's",
    'Signal handler installation',
    'Signal handler overhead',
    'Protection fault',
    'Pipe latency',
    'Process fork+exit',
    'Process fork+execve',
    'Process fork+/bin/sh -c',
    'Static Process fork+exit',
    'Static Process fork+execve',
    'Static Process fork+/bin/sh -c',
    'File /var/tmp/XXX write bandwidth',
    'Pagefaults on /var/tmp/XXX',
    'Pipe bandwidth',
}
IOZONE_HEADERS = (
    ('iozone throughput write/read measurements', 'iozone write/read'),
    ('iozone throughput random-read measurements', 'iozone random-read'),
    ('iozone throughput read-backwards measurements', 'iozone read-backwards'),
    ('iozone throughput stride-read measurements', 'iozone stride-read'),
    ('iozone throughput fwrite/fread measurements', 'iozone fwrite/fread'),
    ('iozone throughput pwrite/pread measurements', 'iozone pwrite/pread'),
    ('iozone throughtput pwritev/preadv measurements', 'iozone pwritev/preadv'),
)


LMBENCH_LABEL_INDEX = {
    label: index
    for index, label in enumerate(
        (
            'Simple syscall',
            'Simple read',
            'Simple write',
            'Simple stat',
            'Simple fstat',
            'Simple open/close',
            "Select on 100 fd's",
            'Signal handler installation',
            'Signal handler overhead',
            'Protection fault',
            'Pipe latency',
            'Process fork+exit',
            'Process fork+execve',
            'Process fork+/bin/sh -c',
            'File /var/tmp/XXX write bandwidth',
            'Pagefaults on /var/tmp/XXX',
            '__mmap__',
            'Pipe bandwidth',
        )
    )
}
for index, label in (
    (11, 'Static Process fork+exit'),
    (12, 'Static Process fork+execve'),
    (13, 'Static Process fork+/bin/sh -c'),
):
    LMBENCH_LABEL_INDEX[label] = index
