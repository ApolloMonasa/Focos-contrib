"""Team judge for iperf (no official stdin judge published).

The test script prints "====== iperf <NAME> end: success|fail ======"
for each subcase. A missing marker is scored 0, matching the platform
contract that missing test points must not be treated as passes.
"""

import json
import re
import sys

EXPECTED = [
    "BASIC_UDP",
    "BASIC_TCP",
    "PARALLEL_UDP",
    "PARALLEL_TCP",
    "REVERSE_UDP",
    "REVERSE_TCP",
]

text = sys.stdin.read()
found = {}
for match in re.finditer(r"^=+\s*iperf\s+(\S+)\s+end:\s*(success|fail)\s*=+\s*$", text, re.M):
    found[match.group(1)] = match.group(2)

result = []
for name in EXPECTED:
    ok = found.get(name) == "success"
    result.append({"name": name, "all": 1, "pass": 1 if ok else 0, "score": 1 if ok else 0})
print(json.dumps(result))
