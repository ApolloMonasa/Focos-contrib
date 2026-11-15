#!/usr/bin/env python3
"""TEST-001 test runner.

Capture or replay a serial log, split it into OS COMP test groups, run the
official (or team) judge for each group, and emit a per-subcase result table.

Design notes:
- The runner never decides pass/fail by itself. It only normalizes the judge
  output and marks missing/truncated/error cases as not passed.
- Missing group -> NOT_RUN (all subcases 0). START without END -> TIMEOUT.
- Judge crash -> JUDGE_ERROR (all subcases 0). Unknown judge names -> extras,
  excluded from the inventory denominator.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

GROUP_RE = re.compile(r"^####\s*OS COMP TEST GROUP\s+(START|END)\s+(\S+)\s*####\s*$")
LIBC_SUFFIX_RE = re.compile(r"-(musl|glibc)$")


@dataclass
class Section:
    group: str
    libc: str
    lines: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class JudgeResult:
    items: list[dict]
    source: str
    error: str | None = None


def normalize_group(raw: str, default_libc: str) -> tuple[str, str]:
    match = LIBC_SUFFIX_RE.search(raw)
    if match:
        return raw[: match.start()], match.group(1)
    return raw, default_libc


def split_sections(log_text: str, default_libc: str) -> dict[tuple[str, str], Section]:
    """Split a serial log into group sections keyed by (group, libc)."""
    sections: dict[tuple[str, str], Section] = {}
    current: Section | None = None

    for line in log_text.splitlines():
        match = GROUP_RE.match(line)
        if match:
            state, raw = match.groups()
            group, libc = normalize_group(raw, default_libc)
            if state == "START":
                if current is not None:
                    current.truncated = True
                    sections[(current.group, current.libc)] = current
                current = Section(group=group, libc=libc)
            else:
                if current is not None and current.group == group:
                    current.truncated = False
                    sections[(current.group, current.libc)] = current
                    current = None
            continue
        if current is not None:
            current.lines.append(line)

    if current is not None:
        current.truncated = True
        sections[(current.group, current.libc)] = current
    return sections


def run_judge(judge_path: Path, section_text: str, timeout: int) -> JudgeResult:
    if not judge_path.exists():
        return JudgeResult([], "missing", f"judge not found: {judge_path}")
    if judge_path.suffix == ".sh":
        return JudgeResult([], "unsupported", f"shell judge is not a stdin parser: {judge_path}")
    try:
        proc = subprocess.run(
            [sys.executable, str(judge_path)],
            input=section_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return JudgeResult([], "timeout", f"judge timed out after {timeout}s")
    except OSError as exc:
        return JudgeResult([], "error", f"judge failed to start: {exc}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:2000]
        return JudgeResult([], "error", f"judge exited {proc.returncode}: {detail}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return JudgeResult([], "error", f"judge output is not JSON: {exc}; stdout={proc.stdout[:500]!r}")
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        return JudgeResult([], "error", "judge output must be a JSON list of objects")
    return JudgeResult(data, "ok")


def normalize_item(item: dict, mode: str) -> tuple[str, int, int, float]:
    name = str(item.get("name", "")).strip()
    all_count = int(item.get("all", item.get("total", 1)) or 1)
    passed = int(item.get("pass", 0) or 0)
    score = float(item.get("score", 0) or 0)
    if mode == "performance":
        result = float(item.get("result", 0) or 0)
        passed = 1 if (result > 0 or score > 0) else 0
        all_count = 1
    return name, all_count, passed, score


def judge_group(
    group: str,
    section: Section | None,
    cfg: dict,
    judge_dir: Path,
    team_dir: Path,
    judge_timeout: int,
) -> dict:
    expected = list(cfg.get("subcases", []))
    mode = cfg.get("mode", "pass_fail")
    result = {
        "group": group,
        "libc": section.libc if section else None,
        "status": "NOT_RUN",
        "judge_source": cfg.get("kind", "unknown"),
        "judge_error": None,
        "subcases": [],
        "extras": [],
        "unexpected_in_log": [],
        "section_found": section is not None,
        "section_truncated": bool(section and section.truncated),
    }
    if section is not None:
        logged = {
            name.strip()
            for name in re.findall(r"^=+\s*START\s+(.+?)\s*=+\s*$", section.text, re.M)
        }
        if group == "libctest":
            normalized = set()
            for name in logged:
                if name.startswith("entry-static.exe "):
                    normalized.add("libctest static " + name[len("entry-static.exe "):])
                elif name.startswith("entry-dynamic.exe "):
                    normalized.add("libctest dynamic " + name[len("entry-dynamic.exe "):])
                else:
                    normalized.add(name)
            logged = normalized
        result["unexpected_in_log"] = sorted(logged - set(expected))

    if section is None:
        result["subcases"] = [
            {"name": n, "status": "NOT_RUN", "all": 1, "pass": 0, "score": 0.0}
            for n in expected
        ]
        return result

    judge_file = cfg.get("judge_file", "")
    if cfg.get("kind") == "official":
        judge_path = judge_dir / Path(judge_file).name
    else:
        judge_path = team_dir / Path(judge_file).name

    judged = run_judge(judge_path, section.text, judge_timeout)
    result["judge_source"] = cfg.get("kind", "unknown")
    if judged.error:
        result["status"] = "TIMEOUT" if section.truncated else "JUDGE_ERROR"
        result["judge_error"] = judged.error
        result["subcases"] = [
            {"name": n, "status": "JUDGE_ERROR", "all": 1, "pass": 0, "score": 0.0}
            for n in expected
        ]
        return result

    by_name: dict[str, dict] = {}
    for item in judged.items:
        name, all_count, passed, score = normalize_item(item, mode)
        if name:
            by_name[name] = {"all": all_count, "pass": passed, "score": score}

    subcases = []
    for name in expected:
        item = by_name.pop(name, None)
        if item is None:
            subcases.append({"name": name, "status": "MISSING", "all": 1, "pass": 0, "score": 0.0})
            continue
        if item["pass"] >= item["all"] and item["all"] > 0:
            status = "PASS"
        elif item["pass"] > 0 or item["score"] > 0:
            status = "PARTIAL"
        else:
            status = "FAIL"
        subcases.append({"name": name, "status": status, **item})

    result["subcases"] = subcases
    result["extras"] = sorted(by_name.keys())

    if section.truncated:
        result["status"] = "TIMEOUT"
    elif all(x["status"] == "PASS" for x in subcases):
        result["status"] = "PASS"
    elif any(x["status"] in ("PASS", "PARTIAL") for x in subcases):
        result["status"] = "PARTIAL"
    else:
        result["status"] = "FAIL"
    return result


def capture(cmd: str, timeout: int) -> tuple[str, bool, int | None]:
    """Run a capture command (e.g. QEMU) and return (log, truncated, exit_code)."""
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    truncated = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        truncated = True
    return out, truncated, proc.returncode


def summarize(results: list[dict]) -> dict:
    totals = {
        "subcases": 0,
        "pass": 0,
        "partial": 0,
        "fail": 0,
        "not_run": 0,
        "score": 0.0,
        "groups": {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "TIMEOUT": 0, "NOT_RUN": 0, "JUDGE_ERROR": 0},
    }
    for result in results:
        totals["groups"][result["status"]] = totals["groups"].get(result["status"], 0) + 1
        for sub in result["subcases"]:
            totals["subcases"] += 1
            totals["score"] += float(sub.get("score", 0) or 0)
            status = sub["status"]
            if status == "PASS":
                totals["pass"] += 1
            elif status == "PARTIAL":
                totals["partial"] += 1
            elif status == "NOT_RUN":
                totals["not_run"] += 1
            else:
                totals["fail"] += 1
    totals["pass_rate"] = round(totals["pass"] / totals["subcases"], 4) if totals["subcases"] else 0.0
    return totals


def write_summary(out_dir: Path, meta: dict, results: list[dict], totals: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "totals": totals, "groups": results}
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# TEST-001 result summary",
        "",
        f"- platform: {meta.get('platform')}",
        f"- libc: {meta.get('libc')}",
        f"- inventory: {meta.get('inventory')}",
        f"- log: {meta.get('log')}",
        f"- captured_at: {meta.get('captured_at')}",
        "",
        "| group | libc | status | judge | subcases | pass | partial | fail | not_run | score |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "NOT_RUN": 0}
        for sub in result["subcases"]:
            key = sub["status"] if sub["status"] in counts else "FAIL"
            counts[key] += 1
        lines.append(
            f"| {result['group']} | {result['libc'] or '-'} | {result['status']} | {result['judge_source']} | "
            f"{len(result['subcases'])} | {counts['PASS']} | {counts['PARTIAL']} | {counts['FAIL']} | {counts['NOT_RUN']} | "
            f"{sum(float(x.get('score', 0) or 0) for x in result['subcases']):.2f} |"
        )
    lines += [
        "",
        f"**Total**: {totals['subcases']} subcases, {totals['pass']} pass, {totals['partial']} partial, "
        f"{totals['fail']} fail, {totals['not_run']} not run; pass rate {totals['pass_rate']:.2%}",
        "",
        "A group is PASS only when every inventory subcase passes. TIMEOUT/NOT_RUN/JUDGE_ERROR are not passes.",
        "The score column is judge-specific (points, lps, KBps, seconds); do not sum or compare it across groups.",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TEST-001 runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", help="replay an existing serial log")
    source.add_argument("--capture-cmd", help="run a command (e.g. QEMU) and capture stdout")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--group", action="append", default=None, help="group to judge; repeatable; default all")
    parser.add_argument("--libc", default="unknown", help="libc label when the group marker has no suffix")
    parser.add_argument("--timeout", type=int, default=600, help="capture timeout in seconds")
    parser.add_argument("--judge-timeout", type=int, default=120, help="judge timeout in seconds")
    parser.add_argument("--judge-dir", default="third_party/oskernel-testsuits-cooperation/judge")
    parser.add_argument("--team-judge-dir", default="scripts/judge_team")
    parser.add_argument("--inventory", default="tests/inventory.json")
    parser.add_argument("--platform", default="qemu")
    args = parser.parse_args(argv)

    inventory_path = Path(args.inventory)
    if not inventory_path.exists():
        print(f"inventory not found: {inventory_path}", file=sys.stderr)
        return 2
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    groups_cfg = inventory["groups"]
    selected = args.group or list(groups_cfg.keys())
    unknown = [g for g in selected if g not in groups_cfg]
    if unknown:
        print(f"unknown group(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    truncated = False
    if args.log:
        log_text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        log_label = str(Path(args.log))
        exit_code = None
    else:
        log_text, truncated, exit_code = capture(args.capture_cmd, args.timeout)
        log_label = args.capture_cmd

    sections = split_sections(log_text, args.libc)
    results = []
    for group in selected:
        section = sections.get((group, args.libc))
        if section is None:
            # If libc was not given, accept any libc for this group.
            if args.libc == "unknown":
                candidates = [s for (g, _), s in sections.items() if g == group]
                section = candidates[0] if candidates else None
        result = judge_group(
            group,
            section,
            groups_cfg[group],
            Path(args.judge_dir),
            Path(args.team_judge_dir),
            args.judge_timeout,
        )
        results.append(result)

    if truncated:
        for result in results:
            result["status"] = "TIMEOUT"
            for sub in result["subcases"]:
                if sub["status"] == "NOT_RUN":
                    sub["status"] = "TIMEOUT"

    totals = summarize(results)
    import datetime

    meta = {
        "platform": args.platform,
        "libc": args.libc,
        "inventory": str(inventory_path),
        "log": log_label,
        "capture_exit_code": exit_code,
        "capture_truncated": truncated,
        "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.log:
        (out_dir / "serial.log").write_text(log_text, encoding="utf-8")
    else:
        (out_dir / "serial.log").write_text(log_text, encoding="utf-8")

    sections_dir = out_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    for result in results:
        key = (result["group"], result["libc"] or args.libc)
        section = sections.get(key)
        if section is not None:
            (sections_dir / f"{result['group']}-{result['libc'] or args.libc}.log").write_text(
                section.text, encoding="utf-8"
            )

    write_summary(out_dir, meta, results, totals)

    print(json.dumps(totals, ensure_ascii=False))
    return 0 if all(r["status"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
