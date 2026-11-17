#!/usr/bin/env python3
"""由冻结清单更新区域计划和文档表格；--check 用于 CI，保留实际执行状态。"""
import argparse
import atexit
import copy
import json
from pathlib import Path
import sys
import tempfile

_source_cache = tempfile.TemporaryDirectory(prefix='focos-source-cache-')
atexit.register(_source_cache.cleanup)
sys.pycache_prefix = _source_cache.name
sys.dont_write_bytecode = True

from inventory import ROOT, load_inventory

BEGIN = '<!-- TEST001_TABLE_BEGIN -->'
END = '<!-- TEST001_TABLE_END -->'


def project_plan(plan, inventory):
    plan = copy.deepcopy(plan)
    plan.update(case_inventory_status='FROZEN_WITH_KNOWN_DIFFERENCES',
                case_count=inventory['total_subcases'], case_inventory_file='tests/inventory.json',
                inventory_frozen_at=inventory['frozen_at'])
    plan['repository'] = inventory['source']['repo']
    plan['branch'] = inventory['source']['branch']
    plan['commit'] = inventory['source']['commit']
    plan['judge'] = {key: inventory['judge'][key] for key in ('repo', 'commit')}
    prior = {entry['name']: entry for entry in plan['groups']}
    plan['groups'] = []
    for name, cfg in inventory['groups'].items():
        row = prior.get(name, {'name': name, 'script': f'scripts/{name}/{name}_testcode.sh',
                              'status_by_libc': {'musl': 'NOT_RUN', 'glibc': 'NOT_RUN'}})
        row.update(case_count=len(cfg['subcases']), judge_source=cfg['kind'])
        plan['groups'].append(row)
    for image in plan['candidate_images']:
        if image['purpose'] == 'preliminary':
            frozen = inventory['image']
            image.update(published_compressed_sha256=frozen['published_compressed_sha256'],
                         image_sha256=frozen['uncompressed_sha256'], local_verification=frozen['local_verification'],
                         digest_source='tests/inventory.json; release digest and local hash verified',
                         source_match='MATCHED_GROUP_SCRIPTS_WITH_KNOWN_DIFFERENCES')
    plan['locks_pending'] = [key for key in plan['locks_pending']
                             if key not in ('expanded_case_inventory', 'image_source_script_match')]
    return plan


def table(inventory):
    lines = [BEGIN, '| 组 | 子用例数 | 判据来源 | judge 文件 | 说明 |',
             '| --- | ---: | --- | --- | --- |']
    for name, cfg in inventory['groups'].items():
        note = cfg.get('note', '按冻结判据解释；参考结果另列').replace('|', '&#124;')
        source = '官方' if cfg['kind'] == 'official' else '团队'
        lines.append(f"| {name} | {len(cfg['subcases'])} | {source} | `{cfg['judge_file']}` | {note} |")
    lines += ['', f"合计 **{inventory['total_subcases']}** 项；本表由 `scripts/sync_test_plan.py` 从清单生成。", END]
    return '\n'.join(lines)


def sync(check=False):
    inventory = load_inventory()
    plan = ROOT/'tests/regional-plan.json'
    doc = ROOT/'docs/reference/test-inventory.md'
    source = doc.read_text()
    if source.count(BEGIN) != 1 or source.count(END) != 1:
        raise ValueError('missing/duplicate generated inventory table markers')
    a, b = source.index(BEGIN), source.index(END) + len(END)
    updates = {plan: json.dumps(project_plan(json.loads(plan.read_text()), inventory), ensure_ascii=False, indent=2)+'\n',
               doc: source[:a] + table(inventory) + source[b:]}
    stale = [str(p.relative_to(ROOT)) for p, content in updates.items() if p.read_text() != content]
    if check and stale:
        raise ValueError('inventory projections stale: ' + ', '.join(stale))
    if not check:
        for p, content in updates.items():
            p.write_text(content, encoding='utf-8')
    return stale


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    try:
        sync(parser.parse_args().check)
        print('TEST-001 inventory projections consistent')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc))
