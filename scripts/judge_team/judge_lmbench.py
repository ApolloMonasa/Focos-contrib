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
measured = False
pending = None


def observe(index, ok):
    global measured
    values = found.setdefault(names[index], [])
    if context == index and pending is not None:
        if measured:
            values.append(False)
        else:
            values[pending] = ok
        measured = True
    else:
        values.append(ok)


for raw in sys.stdin.read().splitlines():
    line = raw.strip()
    if not line:
        continue
    if line.startswith('+ '):
        context = None
        pending = None
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
        if context in (16, 18, 19, 20, 21, 22, 23):
            values = found.setdefault(names[context], [])
            pending = len(values)
            values.append(False)
            measured = False
        continue
    if line == 'file system latency':
        context = 18
        pending = None
        continue
    if line == 'context switch overhead':
        context = 23
        pending = None
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
            observe(context, ok)
            # 每次命令预留失败记录；结果非法、缺失或重复都不能被重试覆盖。
        else:
            observe(context, False)
            context = None
    elif context == 18:
        match = re.fullmatch(r'10k\s+(\d+)\s+(\d+)\s+(\d+)', line)
        if match:
            observe(18, all(int(n) > 0 for n in match.groups()))
        elif not re.fullmatch(r'\d+k\s+\d+\s+\d+\s+\d+', line):
            observe(18, False)
            context = None
    elif context == 23:
        match = re.fullmatch(rf'96\s+({NUMBER})', line)
        if match:
            observe(23, positive(match[1]))
        elif not (re.fullmatch(r'"size=32k ovr=\d+(?:\.\d+)?', line)
                  or re.fullmatch(rf'(?:2|4|8|16|24|32|64)\s+{NUMBER}', line)):
            observe(23, False)
            context = None
emit(names, found)
