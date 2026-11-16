"""按冻结源码的完整标签/单位判 lmbench；无标签数值必须绑定 shell trace。

格式依据 testsuits-for-oskernel@8b58dd1 的 lmbench_src/src。
旧日志缺少 trace 时，不猜测 lat_mmap 与四个读带宽指标的归属。
"""
import math
import re
import shlex
import sys

from common import NUMBER, emit, expected, positive

names = expected('lmbench')
labels = [r'Simple syscall', r'Simple read', r'Simple write', r'Simple stat', r'Simple fstat',
          r'Simple open/close', r"Select on 100 fd's", r'Signal handler installation',
          r'Signal handler overhead', r'Protection fault', r'Pipe latency',
          r'(?:Static )?Process fork\+exit', r'(?:Static )?Process fork\+execve',
          r'(?:Static )?Process fork\+/bin/sh -c']
patterns = [(index, re.compile(rf'{label}:\s*({NUMBER}) microseconds'), re.compile(rf'{label}:'))
            for index, label in enumerate(labels)]
patterns += [(14, re.compile(rf'File /var/tmp/XXX write bandwidth:\s*({NUMBER}) KB/sec'), re.compile(r'File /var/tmp/XXX write bandwidth:')),
             (15, re.compile(rf'Pagefaults on /var/tmp/XXX:\s*({NUMBER}) microseconds'), re.compile(r'Pagefaults on /var/tmp/XXX:')),
             (17, re.compile(rf'Pipe bandwidth:\s*({NUMBER}) MB/sec'), re.compile(r'Pipe bandwidth:'))]
contexts = {
    ('lat_mmap', '-P', '1', '512k', '/var/tmp/XXX'): 16,
    ('bw_file_rd', '-P', '1', '512k', 'io_only', '/var/tmp/XXX'): 19,
    ('bw_file_rd', '-P', '1', '512k', 'open2close', '/var/tmp/XXX'): 20,
    ('bw_mmap_rd', '-P', '1', '512k', 'mmap_only', '/var/tmp/XXX'): 21,
    ('bw_mmap_rd', '-P', '1', '512k', 'open2close', '/var/tmp/XXX'): 22,
}
found = {}
context = None
for raw in sys.stdin.read().splitlines():
    line = raw.strip()
    if not line:
        continue
    if line.startswith('+ '):
        context = None
        try:
            args = shlex.split(line[2:])
        except ValueError:
            continue
        if args and args[0] == './lmbench_all':
            context = contexts.get(tuple(args[1:]))
            if args[1:] == ['lat_fs', '/var/tmp']:
                context = 18
            if args[1:] == ['lat_ctx', '-P', '1', '-s', '32', '2', '4', '8', '16', '24', '32', '64', '96']:
                context = 23
        continue
    if line == 'file system latency':
        context = 18
        continue
    if line == 'context switch overhead':
        context = 23
        continue
    matched = False
    for index, pattern, prefix in patterns:
        match = pattern.fullmatch(line)
        if prefix.match(line):
            found.setdefault(names[index], []).append(bool(match and positive(match[1])))
            matched = True
            break
    if matched:
        continue
    if context in (16, 19, 20, 21, 22):
        match = re.fullmatch(rf'({NUMBER})\s+({NUMBER})', line)
        if match:
            ok = math.isclose(float(match[1]), 0.524288, rel_tol=0, abs_tol=0.0000005) and positive(match[2])
            found.setdefault(names[context], []).append(ok)
            # 保留 context，使同一命令重复输出也能被拒绝。
        else:
            context = None
    elif context == 18:
        match = re.fullmatch(r'10k\s+(\d+)\s+(\d+)\s+(\d+)', line)
        if match:
            found.setdefault(names[18], []).append(all(int(n) > 0 for n in match.groups()))
        elif not re.fullmatch(r'\d+k\s+\d+\s+\d+\s+\d+', line):
            context = None
    elif context == 23:
        match = re.fullmatch(rf'96\s+({NUMBER})', line)
        if match:
            found.setdefault(names[23], []).append(positive(match[1]))
        elif not (re.fullmatch(r'"size=32k ovr=\d+(?:\.\d+)?', line)
                  or re.fullmatch(rf'(?:2|4|8|16|24|32|64)\s+{NUMBER}', line)):
            context = None
emit(names, found)
