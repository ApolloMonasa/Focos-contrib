"""Team judge for unixbench (official stdin judge not published).

The test script prints "Unixbench <NAME> test(<unit>): <number>".
A valid positive number means the benchmark completed; the competition
requires completion with valid results, not matching Linux performance.
"""

import json
import re
import sys

EXPECTED = [
    "Unixbench DHRY2 test(lps)",
    "Unixbench WHETSTONE test(MFLOPS)",
    "Unixbench SYSCALL test(lps)",
    "Unixbench CONTEXT test(lps)",
    "Unixbench PIPE test(lps)",
    "Unixbench SPAWN test(lps)",
    "Unixbench EXECL test(lps)",
    "Unixbench FS_WRITE_SMALL test(KBps)",
    "Unixbench FS_READ_SMALL test(KBps)",
    "Unixbench FS_COPY_SMALL test(KBps)",
    "Unixbench FS_WRITE_MIDDLE test(KBps)",
    "Unixbench FS_READ_MIDDLE test(KBps)",
    "Unixbench FS_COPY_MIDDLE test(KBps)",
    "Unixbench FS_WRITE_BIG test(KBps)",
    "Unixbench FS_READ_BIG test(KBps)",
    "Unixbench FS_COPY_BIG test(KBps)",
    "Unixbench SHELL1 test(lpm)",
    "Unixbench SHELL8 test(lpm)",
    "Unixbench SHELL16 test(lpm)",
    "Unixbench ARITHOH test(lps)",
    "Unixbench SHORT test(lps)",
    "Unixbench INT test(lps)",
    "Unixbench LONG test(lps)",
    "Unixbench FLOAT test(lps)",
    "Unixbench DOUBLE test(lps)",
    "Unixbench HANOI test(lps)",
    "Unixbench EXEC test(lps)",
]

text = sys.stdin.read()
found = {}
for match in re.finditer(r"^Unixbench\s+(.+?):\s*([0-9]+(?:\.[0-9]+)?)\s*$", text, re.M):
    found["Unixbench " + match.group(1).strip()] = float(match.group(2))

result = []
for name in EXPECTED:
    value = found.get(name, 0.0)
    ok = value > 0
    result.append({"name": name, "all": 1, "pass": 1 if ok else 0, "score": value if ok else 0})
print(json.dumps(result))
