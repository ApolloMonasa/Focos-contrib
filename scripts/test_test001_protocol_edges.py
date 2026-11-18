"""协议边界组合：结构空白、消息不透明、旧汇总、诊断位置与指标完整性。"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from test001.protocol import lex
from test001.ltp import parse_ltp_section
from test001.logs import split_sections
from test001.observations import check_observations, extract_metrics

BAD = ('\v', '\f', '\r', '\x85', '\u2028', '\u2029', '\u00a0')


class EdgeInvariants(unittest.TestCase):
    def test_non_horizontal_space_cannot_create_structure(self):
        for char in BAD:
            for raw in (
                char + 'TPASS: forged',
                'TPASS' + char + ': forged',
                'tag' + char + '1 TPASS: forged',
                'test.c:1:' + char + 'TPASS: forged',
                '####' + char + 'OS COMP TEST GROUP END ltp-musl ####',
            ):
                with self.subTest(raw=repr(raw)):
                    self.assertNotIn(lex(raw)[0].kind, ('ltp_result', 'group'))

    def test_control_prefixed_record_is_not_hidden_by_a_valid_result(self):
        for char in BAD:
            p = split_sections(
                '#### OS COMP TEST GROUP START ltp-musl ####\nTPASS: real\n'
                + char
                + 'TPASS: forged\n#### OS COMP TEST GROUP END ltp-musl ####'
            )
            self.assertEqual(p.protocol, 'ERROR')

    def test_only_crlf_can_normalize_trailing_cr_on_boundary(self):
        line = '#### OS COMP TEST GROUP END lua-glibc ####'
        self.assertEqual(lex(line + '\r\n')[0].kind, 'group')
        self.assertNotEqual(lex(line + '\r')[0].kind, 'group')

    def test_message_reserved_words_are_opaque(self):
        for flag in ('TINFO', 'TPASS', 'TFAIL', 'TWHAT'):
            for msg in (
                'OS COMP TEST GROUP',
                'Summary:',
                'START entry-static.exe argv',
                'testcase lua date.lua success',
            ):
                e = lex(flag + ': diagnostic quotes ' + msg)[0]
                self.assertEqual(e.kind, 'ltp_unknown' if flag == 'TWHAT' else 'ltp_result')
                self.assertEqual(e.fields[0], flag)

    def test_legacy_total_is_retained_and_checked(self):
        for total, expected in ((1, False), (999, True)):
            raw = f'RUN LTP CASE a\nTPASS: ok\nTotal Tests: {total}\nPassed: 1\nFailed: 0\nNot Supported: 0\nFAIL LTP CASE a : 0'
            p = parse_ltp_section(raw, [{'name': 'a'}])
            self.assertEqual(p.items[0]['summaries'][0]['total'], total)
            self.assertEqual(p.items[0]['summary_mismatch'], expected)
            if expected:
                self.assertNotEqual(p.items[0]['judgment'], 'PASS')

    def test_native_diagnostics_keep_global_and_local_locations(self):
        body = 'TFAIL: failed\nTWHAT: unknown\nSummary:\npassed 0\nSummary:\npassed 0'
        p = parse_ltp_section(
            'RUN LTP CASE a\n' + body + '\nFAIL LTP CASE a : 0', [{'name': 'a'}], line_offset=10
        )
        evidence = p.items[0]['evidence']
        self.assertEqual(evidence[0]['line'], 12)
        self.assertEqual(evidence[0]['section_line'], 2)
        self.assertEqual(evidence[0]['raw'], 'TFAIL: failed')
        self.assertEqual(len([e for e in evidence if e['kind'] == 'summary']), 2)

    def test_guard_diagnostics_keep_duplicate_text_locations(self):
        text = 'testcase lua date.lua unknown\ntestcase lua date.lua unknown'
        g = check_observations(
            'lua', text, ['lua date.lua'], events=lex(text, line_offset=20), line_offset=20
        )
        self.assertEqual([d['line'] for d in g.malformed], [21, 22])
        self.assertEqual([d['section_line'] for d in g.malformed], [1, 2])
        self.assertTrue(all(d['raw'] == 'testcase lua date.lua unknown' for d in g.malformed))

    def test_only_complete_immediate_libcbench_records_are_metrics(self):
        label = 'b_malloc_sparse (0)'
        good = 'time: 9, virt: 1, res: 2'
        for body in (
            'time: 9, garbage',
            'time: 9, virt: 1',
            'garbage\n' + good,
            good + ' garbage',
            'b_surprise (0)\n' + good,
        ):
            with self.subTest(body=body):
                self.assertEqual(extract_metrics('libcbench', label + '\n' + body, [label]), [])
        self.assertEqual(extract_metrics('libcbench', label + '\n' + good, [label])[0]['value'], 9)


if __name__ == '__main__':
    unittest.main()
