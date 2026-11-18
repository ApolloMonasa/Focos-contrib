"""日志契约：正文不能伪造结果，重复不能变成功，错误不能改变证据位置。"""

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from test001.ltp import parse_ltp_section
from test001.logs import split_sections
from test001.observations import extract_metrics, check_observations
from test001.protocol import lex, RESULT_FLAGS

SUMMARY = 'Summary:\npassed 1\nfailed 0\nbroken 0\nskipped 0\nwarnings 0\n'


class ProtocolInvariants(unittest.TestCase):
    def test_diagnostic_quotes_cannot_create_pass(self):
        for prefix in ('', 'tst_test.c:123: ', 'alpha 1 '):
            for flag in ('TPASS', 'TFAIL', 'TWHAT'):
                with self.subTest(prefix=prefix, quoted=flag):
                    p = parse_ltp_section(
                        'RUN LTP CASE a\n'
                        + prefix
                        + 'TINFO: quotes '
                        + flag
                        + ': only\nFAIL LTP CASE a : 0',
                        [{'name': 'a'}],
                    )
                    self.assertEqual(p.items[0]['judgment'], 'FAIL')
                    self.assertEqual(p.items[0]['events'], {'TINFO': 1})

    def test_duplicate_summaries_block_without_double_count(self):
        for copies in (2, 3):
            p = parse_ltp_section(
                'RUN LTP CASE a\nTPASS: ok\n' + SUMMARY * copies + 'FAIL LTP CASE a : 0',
                [{'name': 'a'}],
            )
            self.assertEqual(p.items[0]['judgment'], 'JUDGE_ERROR')
            self.assertEqual(len(p.items[0]['summaries']), copies)
            self.assertEqual(p.native['summaries'].get('passed', 0), 0)

    def test_invalid_markers_do_not_shift_source_positions(self):
        markers = (
            '#### OS COMP TEST GROUP START lua-glibc ####',
            '#### OS COMP TEST GROUP START bad ####',
            '#### OS COMP TEST GROUP nonsense ####',
        )
        for marker in markers:
            for padding in (0, 3):
                raw = (
                    '\n' * padding
                    + '#### OS COMP TEST GROUP START lmbench-glibc ####\n'
                    + marker
                    + '\nSimple syscall: 1 microseconds\n#### OS COMP TEST GROUP END lmbench-glibc ####\n'
                )
                p = split_sections(raw)
                section = p.sections[('lmbench', 'glibc')]
                m = extract_metrics('lmbench', section.text, [], line_offset=section.start_line)
                self.assertEqual(m[0]['line'], padding + 3)
                self.assertEqual(
                    raw.splitlines()[m[0]['line'] - 1], 'Simple syscall: 1 microseconds'
                )
                self.assertNotEqual(p.protocol, 'VALID')

    def test_environment_never_skips_public_launcher(self):
        spec = importlib.util.spec_from_file_location('launcher', ROOT / 'scripts/test001.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        for value in ('1', '0', ''):
            with (
                patch.dict(os.environ, {'TEST001_SNAPSHOT': value}),
                patch.object(launcher, '_child', return_value=0) as child,
            ):
                self.assertEqual(launcher.main(['analyze']), 0)
                child.assert_called_once_with(['analyze'])


class EventConsumerInvariants(unittest.TestCase):
    def test_consumers_use_the_same_events_without_relexing(self):
        text = 'RUN LTP CASE a\nTPASS: ok\nFAIL LTP CASE a : 0'
        records = lex(text, line_offset=20)
        with patch('test001.ltp.lex', side_effect=AssertionError('must reuse events')):
            result = parse_ltp_section(text, [{'name': 'a'}], line_offset=20, events=records)
        self.assertEqual(result.items[0]['run_line'], 21)
        self.assertEqual(result.items[0]['end_line'], 23)
        self.assertEqual(result.items[0]['run_section_line'], 1)
        records = lex('Simple syscall: 1 microseconds', line_offset=40)
        with patch('test001.proofs.lex', side_effect=AssertionError('must reuse events')):
            self.assertEqual(extract_metrics('lmbench', '', [], events=records)[0]['line'], 41)
            check_observations('lmbench', '', [], events=records)

    def test_structural_flags_have_the_same_owner_everywhere(self):
        for owner in (
            'lua',
            'busybox',
            'basic',
            'libctest',
            'iperf',
            'netperf',
            'cyclictest',
            'libcbench',
            'unixbench',
            'iozone',
            'lmbench',
        ):
            for flag in (*RESULT_FLAGS, 'TWHAT'):
                with self.subTest(owner=owner, flag=flag):
                    raw = f'#### OS COMP TEST GROUP START {owner}-glibc ####\n{flag}: message quotes TPASS: text\n#### OS COMP TEST GROUP END {owner}-glibc ####'
                    p = split_sections(raw)
                    self.assertEqual(p.protocol, 'ERROR')
                    self.assertEqual(p.unattributed[0]['line'], 2)
                    self.assertEqual(p.unattributed[0]['kind'], 'cross_group')

    def test_message_control_characters_do_not_create_records(self):
        for separator in ('\v', '\f', '\x85', '\u2028', '\u2029'):
            raw = 'TINFO: quoted' + separator + 'TPASS: only'
            records = lex(raw)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].fields[0], 'TINFO')

    def test_crlf_preserves_raw_evidence_and_judge_line_semantics(self):
        raw = (
            '#### OS COMP TEST GROUP START libctest-glibc ####\r\n'
            'Pass!\r\n#### OS COMP TEST GROUP END libctest-glibc ####\r\n'
        )
        section = split_sections(raw).sections[('libctest', 'glibc')]
        self.assertEqual(section.events[0].raw, 'Pass!\r')
        self.assertEqual(section.events[0].line, 2)
        self.assertEqual(section.text, 'Pass!')

    def test_record_prefixes_are_explicit_and_message_is_opaque(self):
        for prefix in ('', 'test.c:42: ', 'tag 7 '):
            for flag in RESULT_FLAGS:
                e = lex(prefix + flag + ': diagnostic quotes TWHAT: text')[0]
                self.assertEqual(e.kind, 'ltp_result')
                self.assertEqual(e.fields, (flag, 'diagnostic quotes TWHAT: text'))
        self.assertEqual(lex('arbitrary diagnostic quotes TPASS: text')[0].kind, 'text')

    def test_summary_field_duplicates_and_mixed_formats_block(self):
        bodies = (
            SUMMARY.replace('passed 1', 'passed 1\npassed 1'),
            SUMMARY + 'Total Tests: 1\nPassed: 1\nFailed: 0\nNot Supported: 0\n',
            'Passed: 1\nPassed: 1\n',
        )
        for body in bodies:
            p = parse_ltp_section(
                'RUN LTP CASE a\nTPASS: ok\n' + body + 'FAIL LTP CASE a : 0', [{'name': 'a'}]
            )
            self.assertEqual(p.items[0]['judgment'], 'JUDGE_ERROR')


if __name__ == '__main__':
    unittest.main()
