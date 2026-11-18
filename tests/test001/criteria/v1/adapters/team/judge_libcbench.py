"""libc-bench 标签后须直接出现完整 time 行；错误不能跨行桥接成成功。"""
import re
import sys
from common import NUMBER, emit, expected, positive

names = expected('libcbench')
found = {}
current = None
measured = False
valid_scope = True
pattern = re.compile(rf'time:\s*({NUMBER}),\s*virt:\s*\d+,\s*res:\s*\d+(?:,\s*dirty:\s*\d+)?')
for raw in sys.stdin.read().splitlines():
    line = raw.strip()
    if not line:
        continue
    if line in names:
        current = line
        found.setdefault(current, []).append(False)
        measured = False
        valid_scope = True
        continue
    if current:
        match = pattern.fullmatch(line)
        if valid_scope and not measured and match:
            found[current][-1] = positive(match[1])
            measured = True
        else:
            found[current][-1] = False
            valid_scope = False
emit(names, found)
