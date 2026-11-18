"""团队判据共用冻结名称、有限数值检查与重复结果拒绝。"""
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory import load_inventory

NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


def expected(group):
    return load_inventory()['groups'][group]['subcases']


def positive(value):
    number = float(value)
    return math.isfinite(number) and number > 0


def emit(names, observations):
    # 同一指标出现两次也不能自动选最后一次；需先核查日志来源。
    rows = []
    for name in names:
        values = observations.get(name, [])
        ok = len(values) == 1 and values[0]
        rows.append({'name': name, 'all': 1, 'pass': int(ok), 'score': int(ok)})
    print(json.dumps(rows, allow_nan=False))


def markers(group, text):
    names = expected(group)
    found = {}
    pattern = rf'^=+\s*{group}\s+(\S+)\s+end:\s*(\S+)\s*=+\s*$'
    for match in re.finditer(pattern, text, re.M):
        found.setdefault(match[1], []).append(match[2] == 'success')
    emit(names, found)
