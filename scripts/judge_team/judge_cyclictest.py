"""Team judge for cyclictest (no official stdin judge published).

The test script prints "====== cyclictest <NAME> end: success|fail ======".
The final hackbench kill line is informational and is not a subcase.
"""

import json
import re
import sys

EXPECTED = ["NO_STRESS_P1", "NO_STRESS_P8", "STRESS_P1", "STRESS_P8"]

text = sys.stdin.read()
found = {}
for match in re.finditer(r"^=+\s*cyclictest\s+(\S+)\s+end:\s*(success|fail)\s*=+\s*$", text, re.M):
    found[match.group(1)] = match.group(2)

result = []
for name in EXPECTED:
    ok = found.get(name) == "success"
    result.append({"name": name, "all": 1, "pass": 1 if ok else 0, "score": 1 if ok else 0})
print(json.dumps(result))
