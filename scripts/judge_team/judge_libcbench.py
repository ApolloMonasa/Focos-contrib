"""libc-bench 标签后须直接出现完整 time 行；错误不能跨行桥接成成功。"""
import re
import sys
from common import NUMBER, emit, expected, positive

names = expected('libcbench')
found = {}
current = None
pattern = re.compile(rf'time:\s*({NUMBER}),\s*virt:\s*\d+,\s*res:\s*\d+(?:,\s*dirty:\s*\d+)?')
for raw in sys.stdin.read().splitlines():
    line = raw.strip()
    if not line:
        continue
    if line in names:
        current = line
        found.setdefault(current, []).append(False)
        continue
    if current:
        match = pattern.fullmatch(line)
        found[current][-1] = bool(match and positive(match[1]))
        current = None
emit(names, found)
