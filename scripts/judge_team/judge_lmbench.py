"""Team judge for lmbench (the published lmbench.sh is a runner, not a stdin parser).

This is a conservative keyword-based parser. It only proves that a numeric
result for each command appeared; it does not compare against a performance
baseline. Confidence is medium until validated against a real reference run.
"""

import json
import re
import sys

EXPECTED = [
    "1:syscall null latency (micro second)",
    "2:syscall read latency (micro second)",
    "3:syscall write latency (micro second)",
    "4:syscall stat latency (micro second)",
    "5:syscall fstat latency (micro second)",
    "6:syscall open latency (micro second)",
    "7:select latency (micro second)",
    "8:sig install latency (micro second)",
    "9:sig catch latency (micro second)",
    "10:sig prot latency (micro second)",
    "11:pipe latency (micro second)",
    "12:proc fork latency (micro second)",
    "13:proc exec latency (micro second)",
    "14:proc shell latency (micro second)",
    "15:fs write bandwidth (KB/sec)",
    "16:pagefault latency (micro second)",
    "17:mmap latency (micro second)",
    "18:pipe bandwidth (KB/sec)",
    "19:file system latency (creations+deletes per second)",
    "20:512k file read ioonly (megabytes per second)",
    "21:512k file read open2close (megabytes per second)",
    "22:512k mmap read mmaponly (megabytes per second)",
    "23:512k mmap read open2close (megabytes per second)",
    "24:context switch latency (micro second when there are 96 processes)",
]

RULES = [
    r"Simple syscall",
    r"Simple read",
    r"Simple write",
    r"Simple stat",
    r"Simple fstat",
    r"Simple open/close",
    r"Select on",
    r"Signal handler installation",
    r"Signal handler overhead",
    r"Protection fault",
    r"Pipe latency",
    r"\bfork\b",
    r"\bexec\b",
    r"\bshell\b",
    r"write bandwidth|XXX",
    r"[Pp]agefault",
    r"[Mm]map",
    r"pipe bandwidth",
    r"creations|file system latency|^\s*\d+k\s",
    r"io_only",
    r"open2close",
    r"mmap_only",
    r"open2close",
    r"context",
]

text = sys.stdin.read()
pending = list(range(len(EXPECTED)))
assigned = {}
number_re = re.compile(r"\d+(?:\.\d+)?")

for raw in text.splitlines():
    line = raw.strip()
    if not line or not number_re.search(line):
        continue
    for idx in list(pending):
        if re.search(RULES[idx], line, re.I):
            assigned[idx] = line
            pending.remove(idx)
            break

result = []
for idx, name in enumerate(EXPECTED):
    ok = idx in assigned
    result.append({"name": name, "all": 1, "pass": 1 if ok else 0, "score": 1 if ok else 0})
print(json.dumps(result))
