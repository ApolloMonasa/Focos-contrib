"""冻结输入、区域计划和判分器来源不能独立漂移。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from inventory import load_inventory
from sync_test_plan import project_plan, sync
from fetch_judges import fetch


class InputTests(unittest.TestCase):
    def test_inventory_projections_are_current(self):
        sync(check=True)

    def test_plan_projection_preserves_execution_results(self):
        plan = json.loads((ROOT/'tests/regional-plan.json').read_text())
        plan['groups'][0]['status_by_libc']['musl'] = 'FAIL'
        projected = project_plan(plan, load_inventory())
        self.assertEqual(projected['groups'][0]['status_by_libc']['musl'], 'FAIL')
        self.assertEqual(projected['groups'][0]['status_by_libc']['glibc'], 'NOT_RUN')
        self.assertEqual(projected['case_count'], 427)
        self.assertEqual(sum(g['case_count'] for g in projected['groups']), 427)
        self.assertEqual(project_plan(projected, load_inventory()), projected)

    def test_invalid_inventory_is_rejected(self):
        original = load_inventory()
        changes = [lambda d: d.update(total_subcases=426),
                   lambda d: d['groups']['basic']['subcases'].append('test_brk'),
                   lambda d: d['judge']['files'].clear(),
                   lambda d: d['groups']['basic'].update(judge_file='../judge_basic.py')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'inventory.json'
            for change in changes:
                data = copy.deepcopy(original)
                change(data)
                path.write_text(json.dumps(data))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_inventory(path)

    def test_fetch_does_not_overwrite_existing_modified_judge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'official'
            (root/'judge').mkdir(parents=True)
            path = root/'judge/judge_basic.py'
            path.write_text('modified')
            with self.assertRaisesRegex(ValueError, 'modified'):
                fetch(root)
            self.assertEqual(path.read_text(), 'modified')
