"""LTP 原生结果适配器的正反例；不依赖运行中的内核。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from test001.ltp import parse_ltp_section

EXPECTED = [{'name': 'alpha'}, {'name': 'beta'}]


def parse(text):
    return parse_ltp_section(text, EXPECTED)


def item(result, name):
    return next(entry for entry in result.items if entry['name'] == name)


class PositiveTests(unittest.TestCase):
    """完整结束、退出 0 且至少一个 TPASS 才是 PASS。"""

    def test_tpass_only_is_pass(self):
        result = parse('RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE alpha : 0')
        self.assertEqual(item(result, 'alpha')['judgment'], 'PASS')
        self.assertEqual(result.native['events'], {'TPASS': 1})

    def test_info_and_debug_do_not_block(self):
        result = parse(
            'RUN LTP CASE alpha\nTINFO: info\nTDEBUG: debug\nTPASS: ok\n'
            'FAIL LTP CASE alpha : 0'
        )
        self.assertEqual(item(result, 'alpha')['judgment'], 'PASS')
        self.assertEqual(result.native['events']['TINFO'], 1)
        self.assertEqual(result.native['events']['TDEBUG'], 1)

    def test_summary_is_preserved_next_to_events(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nSummary:\npassed   1\nfailed   0\n'
            'broken   0\nskipped  0\nwarnings 0\nFAIL LTP CASE alpha : 0'
        )
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'PASS')
        self.assertEqual(entry['summaries'][0]['passed'], 1)
        self.assertFalse(entry['summary_incomplete'])
        self.assertEqual(result.native['summaries']['passed'], 1)


class BlockingResultTests(unittest.TestCase):
    """跳过、警告、BROK、FAIL、非零退出和无结果都不能通过。"""

    def assert_fails(self, text, reason):
        entry = item(parse(text), 'alpha')
        self.assertEqual(entry['judgment'], 'FAIL')
        self.assertTrue(any(reason in value for value in entry['reasons']))

    def test_tconf_blocks_even_with_tpass(self):
        self.assert_fails(
            'RUN LTP CASE alpha\nTPASS: ok\nTCONF: skip\nFAIL LTP CASE alpha : 0',
            'TCONF_1',
        )

    def test_twarn_blocks_even_with_tpass(self):
        self.assert_fails(
            'RUN LTP CASE alpha\nTPASS: ok\nTWARN: warn\nFAIL LTP CASE alpha : 0',
            'TWARN_1',
        )

    def test_tfail_blocks(self):
        self.assert_fails(
            'RUN LTP CASE alpha\nTPASS: ok\nTFAIL: failed\nFAIL LTP CASE alpha : 0',
            'TFAIL_1',
        )

    def test_tbrok_blocks(self):
        self.assert_fails(
            'RUN LTP CASE alpha\nTBROK: broken\nFAIL LTP CASE alpha : 0',
            'TBROK_1',
        )

    def test_nonzero_exit_blocks(self):
        self.assert_fails(
            'RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE alpha : 1',
            'EXIT_1',
        )

    def test_no_native_result_blocks(self):
        self.assert_fails(
            'RUN LTP CASE alpha\nFAIL LTP CASE alpha : 0',
            'NO_TPASS',
        )


class IncompleteAndIllegalTests(unittest.TestCase):
    """缺结束、重复尝试、非法退出和未知标志都单列。"""

    def test_missing_end_marks_missing(self):
        result = parse('RUN LTP CASE alpha\nTPASS: ok')
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'MISSING')
        self.assertIn('INCOMPLETE_ATTEMPT', entry['reasons'])
        self.assertTrue(result.errors)

    def test_duplicate_attempt_is_judge_error(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE alpha : 0\n'
            'RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE alpha : 0'
        )
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'JUDGE_ERROR')
        self.assertIn('DUPLICATE_ATTEMPT', entry['reasons'])
        self.assertIn('duplicate LTP case', result.errors[0])

    def test_unknown_result_flag_is_judge_error(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nTFOO: unexpected\n'
            'FAIL LTP CASE alpha : 0'
        )
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'JUDGE_ERROR')
        self.assertTrue(any('UNKNOWN_RESULT_FLAG' in value for value in entry['reasons']))

    def test_invalid_exit_is_judge_error(self):
        result = parse('RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE alpha : 999')
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'JUDGE_ERROR')
        self.assertIn('INVALID_EXIT', entry['reasons'])

    def test_orphan_end_is_error(self):
        result = parse('FAIL LTP CASE alpha : 0')
        self.assertTrue(result.errors)
        self.assertEqual(item(result, 'alpha')['judgment'], 'MISSING')

    def test_mismatched_end_is_error(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE beta : 0'
        )
        self.assertTrue(any('does not match' in error for error in result.errors))
        self.assertEqual(item(result, 'alpha')['judgment'], 'MISSING')

    def test_extra_case_is_not_in_denominator(self):
        result = parse(
            'RUN LTP CASE extra\nTPASS: ok\nFAIL LTP CASE extra : 0'
        )
        self.assertEqual([extra['name'] for extra in result.extras], ['extra'])
        self.assertTrue(any('unknown LTP case' in error for error in result.errors))
        self.assertEqual(item(result, 'alpha')['judgment'], 'MISSING')


class SummaryTests(unittest.TestCase):
    """原生汇总只作独立事实保存，不能替代事件计数。"""

    def test_summary_mismatch_is_recorded(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nSummary:\npassed   2\nfailed   0\n'
            'broken   0\nskipped  0\nwarnings 0\nFAIL LTP CASE alpha : 0'
        )
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'PASS')
        self.assertTrue(entry['summary_mismatch'])
        self.assertEqual(result.native['summary_mismatch'], 1)

    def test_incomplete_summary_is_recorded(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nSummary:\npassed   1\n'
            'FAIL LTP CASE alpha : 0'
        )
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'PASS')
        self.assertTrue(entry['summary_incomplete'])
        self.assertEqual(result.native['summary_incomplete'], 1)

    def test_multiple_summaries_are_preserved_but_rejected(self):
        result = parse(
            'RUN LTP CASE alpha\nTPASS: ok\nSummary:\npassed   1\n'
            'Summary:\npassed   1\nFAIL LTP CASE alpha : 0'
        )
        entry = item(result, 'alpha')
        self.assertEqual(entry['judgment'], 'JUDGE_ERROR')
        self.assertEqual(len(entry['summaries']), 2)

    def test_order_mismatch_is_reported(self):
        result = parse(
            'RUN LTP CASE beta\nTPASS: ok\nFAIL LTP CASE beta : 0\n'
            'RUN LTP CASE alpha\nTPASS: ok\nFAIL LTP CASE alpha : 0'
        )
        self.assertFalse(result.order_matches)


if __name__ == '__main__':
    unittest.main()
