"""Unixbench 只接受完整正数结果行，重复项不能覆盖。"""
import json
import re
import sys
from common import NUMBER, expected, positive

names = expected('unixbench')
found = {}
for match in re.finditer(r'^Unixbench (.+?):\s*(.*?)\s*$', sys.stdin.read(), re.M):
    name, text = 'Unixbench ' + match[1], match[2]
    valid = re.fullmatch(NUMBER, text)
    value = float(text) if valid and positive(text) else 0.0
    found.setdefault(name, []).append(value)
rows = []
for name in names:
    values = found.get(name, [])
    value = values[0] if len(values) == 1 else 0.0
    rows.append({'name': name, 'all': 1, 'pass': int(value > 0), 'score': value})
print(json.dumps(rows, allow_nan=False))
