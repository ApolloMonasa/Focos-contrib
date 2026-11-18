"""报告展示层：任意名称/原因不能破坏 Markdown 表格或代码跨度。"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from test001.report import _cell, _code, _markdown


def sample_report():
    counts = {
        'selected_total': 1, 'observed_pass': 0, 'accepted_pass': 0,
        'pass': 0, 'partial': 0, 'fail': 1, 'missing': 0, 'not_run': 0,
        'judge_error': 0,
    }
    return {
        'origin': 'external_log', 'subject': 'unknown', 'platform': 'unknown',
        'profile': {'profile_id': 'p', 'revision': 1,
                    'source_file_sha256': 'b' * 64},
        'criteria': {'criteria_id': 'c', 'revision': 1,
                     'source_file_sha256': 'c' * 64},
        'capture': {'status': 'NOT_RECORDED', 'exit_code': None},
        'input_integrity': 'UNVERIFIED',
        'protocol': {'status': 'ERROR', 'errors': ['bad `error|pipe']},
        'acceptance': {'status': 'BLOCKED', 'reasons': ['bad `reason|pipe']},
        'totals': counts,
        'groups': [{
            'group': 'ltp', 'libc': 'musl', 'section': 'COMPLETE',
            'shard_id': 'ltp-musl', 'adapter': 'ltp_native', 'counts': counts,
            'items': [{'name': 'case`one|two', 'judgment': 'FAIL', 'reasons': []}],
            'extras': [{'name': 'extra`one|two'}],
            'native': {
                'events': {'TPASS': 1}, 'summaries': {},
                'unattributed': [{'event': 'TFAIL', 'line': 42,
                                  'section_line': 3}],
            },
            'metrics': [{'name': 'b_malloc_sparse (0)', 'value': 0.0614294,
                         'unit': 's', 'line': 7}],
        }],
        'implementation': {'manifest_sha256': 'a' * 64},
    }


class MarkdownEscapingTests(unittest.TestCase):
    def test_code_span_grows_past_inner_backticks(self):
        self.assertEqual(_code('plain'), '`plain`')
        self.assertEqual(_code('a`b|c'), '``a`b|c``')
        self.assertEqual(_code('a``b'), '```a``b```')

    def test_table_cell_escapes_pipe_and_newline(self):
        self.assertEqual(_cell('a|b\nc'), 'a\\|b c')

    def test_markdown_keeps_names_and_empty_reason_readable(self):
        text = _markdown(sample_report())
        self.assertIn('/ ``case`one|two``', text)
        self.assertIn('/ ``extra`one|two``', text)
        self.assertIn('``bad `error|pipe``', text)
        self.assertIn('未记录原因', text)
        self.assertNotIn('（-）', text)
        self.assertIn('| ltp | musl | COMPLETE | ltp_native |', text)
        self.assertIn('line 42', text)
        self.assertIn('`TFAIL`', text)
        self.assertIn('性能原值', text)
        self.assertIn('0.0614294', text)


if __name__ == '__main__':
    unittest.main()
