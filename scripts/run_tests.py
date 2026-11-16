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
import math
import signal
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from inventory import load_inventory, digest, ROOT
from reference_protocol import validate_reference
from reference_manifest import verify_manifest

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
    """严格配对组与 libc；重复、嵌套或孤立 END 不允许被覆盖。"""
    sections = {}
    seen = set()
    current = None
    for line in log_text.splitlines():
        match = GROUP_RE.fullmatch(line)
        if not match:
            if '#### OS COMP TEST GROUP' in line and not line.startswith('+ '):
                raise ValueError('malformed group marker')
            if current is not None:
                current.lines.append(line)
            continue
        state, raw = match.groups()
        group, libc = normalize_group(raw, default_libc)
        key = (group, libc)
        if state == 'START':
            if current is not None or key in seen:
                raise ValueError(f'nested or duplicate section: {raw}')
            seen.add(key)
            current = Section(group=group, libc=libc)
        else:
            if current is None or (current.group, current.libc) != key:
                raise ValueError(f'unmatched END: {raw}')
            sections[key] = current
            current = None
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

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field: ' + key)
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    try:
        data = json.loads(proc.stdout, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except ValueError as exc:
        return JudgeResult([], "error", f"judge output is not JSON: {exc}; stdout={proc.stdout[:500]!r}")
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        return JudgeResult([], "error", "judge output must be a JSON list of objects")
    return JudgeResult(data, "ok")


def normalize_item(item: dict, mode: str) -> tuple[str, int, int, float]:
    name = item.get('name')
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError('invalid judge name')
    score = item.get('score', 0)
    if type(score) not in (int, float) or not math.isfinite(score) or score < 0:
        raise ValueError('invalid/nonfinite judge score')
    if mode == 'performance':
        value = item.get('result')
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid/nonfinite performance result')
        return name, 1, int(value > 0), float(score)
    if 'all' in item and 'total' in item and item['all'] != item['total']:
        raise ValueError('conflicting all/total assertion counts')
    all_count = item.get('all', item.get('total'))
    passed = item.get('pass')
    if (type(all_count) is not int or type(passed) is not int or
            all_count <= 0 or not 0 <= passed <= all_count):
        raise ValueError('invalid judge assertion counts')
    return name, all_count, passed, float(score)


def judge_group(
    group: str,
    section: Section | None,
    cfg: dict,
    judge_dir: Path,
    team_dir: Path,
    judge_timeout: int,
    official_hashes: dict | None = None,
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

    if cfg.get('kind') == 'official' and official_hashes is not None and judge_path.exists() and digest(judge_path) != official_hashes.get(judge_path.name):
        judged = JudgeResult([], 'error', 'official judge hash mismatch')
    else:
        judged = run_judge(judge_path, section.text, judge_timeout)
    result["judge_source"] = cfg.get("kind", "unknown")
    if judged.error:
        result["status"] = "JUDGE_ERROR"
        result["judge_error"] = judged.error
        result["subcases"] = [
            {"name": n, "status": "JUDGE_ERROR", "all": 1, "pass": 0, "score": 0.0}
            for n in expected
        ]
        return result

    by_name = {}
    try:
        for item in judged.items:
            name, all_count, passed, score = normalize_item(item, mode)
            if name in by_name:
                raise ValueError('duplicate judge name: ' + name)
            by_name[name] = {'all': all_count, 'pass': passed, 'score': score}
    except (ValueError, TypeError, OverflowError) as exc:
        result['status'] = 'JUDGE_ERROR'
        result['judge_error'] = str(exc)
        result['subcases'] = [
            {'name': name, 'status': 'JUDGE_ERROR', 'all': 1, 'pass': 0, 'score': 0.0}
            for name in expected]
        return result

    subcases = []
    for name in expected:
        item = by_name.pop(name, None)
        if item is None:
            subcases.append({"name": name, "status": "MISSING", "all": 1, "pass": 0, "score": 0.0})
            continue
        if item["pass"] == item["all"] and item["all"] > 0:
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


def capture(cmd: str, timeout: int) -> tuple[bytes, bool, int | None]:
    """超时关闭整个进程组，避免 QEMU/后代继续持有串口管道。"""
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    truncated = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        truncated = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
    return out, truncated, proc.returncode


def summarize(results: list[dict]) -> dict:
    totals = {
        "subcases": 0,
        "pass": 0,
        "partial": 0,
        "fail": 0,
        "not_run": 0,
        "groups": {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "TIMEOUT": 0, "NOT_RUN": 0, "JUDGE_ERROR": 0},
    }
    for result in results:
        totals["groups"][result["status"]] = totals["groups"].get(result["status"], 0) + 1
        for sub in result["subcases"]:
            totals["subcases"] += 1
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
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    lines = [
        "# TEST-001 result summary",
        "",
        f"- platform: {meta.get('platform')}",
        f"- libc: {meta.get('libc')}",
        f"- inventory: {meta.get('inventory')}",
        f"- log: {meta.get('log')}",
        f"- captured_at: {meta.get('captured_at')}",
        f"- environment: {meta['environment']['status']}",
        f"- execution_valid: {meta['execution_valid']}",
        f"- errors: {meta['environment']['errors'] or meta['log_error'] or '-'}",
        "",
        "| group | libc | status | judge | subcases | pass | partial | fail | not_run |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "NOT_RUN": 0}
        for sub in result["subcases"]:
            key = sub["status"] if sub["status"] in counts else "FAIL"
            counts[key] += 1
        lines.append(
            f"| {result['group']} | {result['libc'] or '-'} | {result['status']} | {result['judge_source']} | "
            f"{len(result['subcases'])} | {counts['PASS']} | {counts['PARTIAL']} | {counts['FAIL']} | {counts['NOT_RUN']} |"
        )
    lines += [
        "",
        f"**Total**: {totals['subcases']} subcases, {totals['pass']} pass, {totals['partial']} partial, "
        f"{totals['fail']} fail, {totals['not_run']} not run; pass rate {totals['pass_rate']:.2%}",
        "",
        "A group is PASS only when every inventory subcase passes. TIMEOUT/NOT_RUN/JUDGE_ERROR are not passes.",
        "Scores remain in per-case JSON only; mixed units are never summed.",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TEST-001 runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", help="replay an existing serial log")
    source.add_argument("--capture-cmd", help="run a command (e.g. QEMU) and capture stdout")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--group", action="append", default=None, help="group to judge; repeatable; default all")
    parser.add_argument("--libc", required=True, choices=["musl", "glibc"])
    parser.add_argument("--reference", choices=["strict", "legacy"], help="严格新协议或显式回放未验证的旧环境")
    parser.add_argument('--reference-manifest', type=Path)
    parser.add_argument('--reference-artifacts', nargs=2, metavar=('KERNEL', 'INITRAMFS'))
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
    try:
        inventory = load_inventory(inventory_path)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'invalid inventory: {exc}', file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.judge_timeout <= 0:
        print('timeouts must be positive', file=sys.stderr)
        return 2
    groups_cfg = inventory["groups"]
    selected = args.group or list(groups_cfg.keys())
    if len(set(selected)) != len(selected):
        print("duplicate selected groups", file=sys.stderr)
        return 2
    unknown = [g for g in selected if g not in groups_cfg]
    if unknown:
        print(f"unknown group(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    truncated = False
    if args.log:
        raw_log = Path(args.log).read_bytes()
        log_label = str(Path(args.log))
        exit_code = None
    else:
        raw_log, truncated, exit_code = capture(args.capture_cmd, args.timeout)
        log_label = args.capture_cmd

    log_text = raw_log.decode('utf-8', errors='replace')
    log_error = None
    try:
        sections = split_sections(log_text, args.libc)
    except ValueError as exc:
        sections, log_error = {}, str(exc)
    environment = validate_reference(log_text, args.reference, selected, args.libc)
    provenance = None
    strict = args.reference == 'strict' or (args.reference is None and environment['status'] != 'NOT_APPLICABLE')
    if strict and not truncated:
        try:
            if args.reference_manifest is None or args.reference_artifacts is None:
                raise ValueError('strict reference requires --reference-manifest and --reference-artifacts KERNEL INITRAMFS')
            provenance = verify_manifest(args.reference_manifest, args.reference_artifacts, log_text,
                                         inventory, selected, args.libc, inventory_path)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            environment['status'] = 'ERROR'
            environment['errors'].append(str(exc))
    results = []
    for group in selected:
        section = sections.get((group, args.libc))
        result = judge_group(
            group,
            section,
            groups_cfg[group],
            Path(args.judge_dir),
            Path(args.team_judge_dir),
            args.judge_timeout,
            inventory['judge']['files'],
        )
        results.append(result)

    failure = None
    if re.search(r'^[ \t]*REF_FAIL(?:\s|$)', log_text, re.M):
        failure = 'ENV_ERROR'
    elif truncated:
        failure = 'TIMEOUT'
    elif environment['status'] == 'ERROR':
        failure = 'ENV_ERROR'
    elif exit_code not in (None, 0):
        failure = 'CAPTURE_ERROR'
    elif log_error:
        failure = 'LOG_ERROR'
    for result in results:
        result['libc'] = args.libc
        if failure:
            result['status'] = failure
            # 无效运行的子项不能留 PASS；数值仍供诊断，不能计入通过率。
            for sub in result['subcases']:
                sub['observed'] = {key: sub[key] for key in ('status', 'pass', 'score')}
                sub.update(status=failure, **{'pass': 0, 'score': 0.0})
        elif environment['script_exits'].get(f"{result['group']}-{args.libc}", 0) != 0:
            result['status'] = 'FAIL'
            for sub in result['subcases']:
                sub['observed'] = {key: sub[key] for key in ('status', 'pass', 'score')}
                sub.update(status='FAIL', **{'pass': 0, 'score': 0.0})

    totals = summarize(results)
    import datetime

    meta = {
        "platform": args.platform,
        "libc": args.libc,
        "inventory": str(inventory_path),
        "log": log_label,
        "capture_exit_code": exit_code,
        "capture_truncated": truncated,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        'inventory_sha256': digest(inventory_path),
        'log_sha256': hashlib.sha256(raw_log).hexdigest(),
        'environment': environment,
        'reference_provenance': provenance,
        'execution_valid': failure is None and environment['status'] != 'LEGACY_UNVERIFIED',
        'log_error': log_error,
        'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in
                          [Path(__file__).resolve(), ROOT/'scripts/inventory.py', ROOT/'scripts/reference_protocol.py', ROOT/'scripts/reference_manifest.py']},
        'judge_sha256': {str(p): digest(p) for p in
                         sorted(set(Path(args.judge_dir).glob('judge_*.py')) | set(Path(args.team_judge_dir).glob('*.py')))},
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'serial.log').write_bytes(raw_log)

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
    return 0 if environment["status"] != "LEGACY_UNVERIFIED" and all(r["status"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as exc:
        print(f'runner: {exc}', file=sys.stderr)
        raise SystemExit(2)
