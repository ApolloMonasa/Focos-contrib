"""Team judge for netperf (no official stdin judge published).

The test script prints "====== netperf <NAME> end: success|fail ======".
"""

import json
import re
import sys

EXPECTED = ["UDP_STREAM", "TCP_STREAM", "UDP_RR", "TCP_RR", "TCP_CRR"]

text = sys.stdin.read()
found = {}
for match in re.finditer(r"^=+\s*netperf\s+(\S+)\s+end:\s*(success|fail)\s*=+\s*$", text, re.M):
    found[match.group(1)] = match.group(2)

result = []
for name in EXPECTED:
    ok = found.get(name) == "success"
    result.append({"name": name, "all": 1, "pass": 1 if ok else 0, "score": 1 if ok else 0})
print(json.dumps(result))
