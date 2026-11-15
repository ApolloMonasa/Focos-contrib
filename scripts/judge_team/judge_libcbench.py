"""Team judge for libc-bench (official stdin judge not published).

libc-bench prints a label line, then "  time: <seconds>, virt: ...".
A subcase passes when its label is followed by a numeric time line.
"""

import json
import re
import sys

EXPECTED = [
    "b_malloc_sparse (0)",
    "b_malloc_bubble (0)",
    "b_malloc_tiny1 (0)",
    "b_malloc_tiny2 (0)",
    "b_malloc_big1 (0)",
    "b_malloc_big2 (0)",
    "b_malloc_thread_stress (0)",
    "b_malloc_thread_local (0)",
    'b_string_strstr ("abcdefghijklmnopqrstuvwxyz")',
    'b_string_strstr ("azbycxdwevfugthsirjqkplomn")',
    'b_string_strstr ("aaaaaaaaaaaaaacccccccccccc")',
    'b_string_strstr ("aaaaaaaaaaaaaaaaaaaaaaaaac")',
    'b_string_strstr ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaac")',
    "b_string_memset (0)",
    "b_string_strchr (0)",
    "b_string_strlen (0)",
    "b_pthread_createjoin_serial1 (0)",
    "b_pthread_createjoin_serial2 (0)",
    "b_pthread_create_serial1 (0)",
    "b_pthread_uselesslock (0)",
    "b_utf8_bigbuf (0)",
    "b_utf8_onebyone (0)",
    "b_stdio_putcgetc (0)",
    "b_stdio_putcgetc_unlocked (0)",
    'b_regex_compile ("(a|b|c)*d*b")',
    'b_regex_search ("(a|b|c)*d*b")',
    'b_regex_search ("a{25}b")',
]

text = sys.stdin.read()
found = set()
current = None
for raw in text.splitlines():
    line = raw.strip()
    if line in EXPECTED:
        current = line
        continue
    if current and line.startswith("time:") and re.search(r"\d", line):
        found.add(current)
        current = None

result = []
for name in EXPECTED:
    ok = name in found
    result.append({"name": name, "all": 1, "pass": 1 if ok else 0, "score": 1 if ok else 0})
print(json.dumps(result))
