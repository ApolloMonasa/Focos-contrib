"""Lua 解析的手写期望；不使用生产词法生成测试期望。"""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from test001_lua import analyze_lua, LUA_NAMES

START = b'#### OS COMP TEST GROUP START lua-musl ####\n'
END = b'#### OS COMP TEST GROUP END lua-musl ####\n'
A = b'testcase lua date.lua success\n'
B = b'testcase lua file_io.lua success\n'
FAIL = b'testcase lua date.lua fail\n'


class LuaParserTests(unittest.TestCase):
    def codes(self, result):
        return {d.code.value for d in result.diagnostics}

    def parse(self, raw, names=('date.lua',)):
        result = analyze_lua(raw, 'musl', names)
        self.assertEqual(len(result.records), len(result.assignments))
        self.assertEqual([a.record_id for a in result.assignments], [r.record_id for r in result.records])
        self.assertEqual(b''.join(r.raw for r in result.records), raw)
        for r in result.records:
            self.assertEqual(raw[r.start:r.end], r.raw)
        self.assertEqual(result.acceptance, 'BLOCKED')
        self.assertEqual(result.trust, 'external_log/unknown')
        return result

    def test_plan_matches_frozen_source_order(self):
        root = Path(__file__).resolve().parents[1] / 'tests/test001/audit-inputs'
        for libc in ('musl', 'glibc'):
            lines = (root / libc / 'lua_testcode.sh').read_text().split('\n')
            actual = tuple(line[len('./test.sh '):] for line in lines if line.startswith('./test.sh '))
            self.assertEqual(actual, LUA_NAMES)

    def test_complete_and_failed_result(self):
        for row, judgment in ((A, 'PASS'), (FAIL, 'FAIL')):
            r = self.parse(START + row + END)
            self.assertEqual(r.protocol.value, 'COMPLETE')
            self.assertEqual(r.invocations[0].judgment.value, judgment)
            self.assertEqual(r.invocations[0].attempts[0].record_id, 2)
            self.assertEqual(r.assignments[1].owner, 'invocation')
            self.assertTrue(r.order_matches)

    def test_real_two_libc_groups(self):
        root = Path(__file__).resolve().parents[1] / 'tests/test001/fixtures/lua-a0'
        index = json.loads((root / 'index.json').read_text())
        for libc, sample in index['samples'].items():
            raw = (root / sample['serial_fragment']).read_bytes()
            begin, end = sample['group_byte_range_in_fragment']
            r = analyze_lua(raw[begin:end], libc)
            self.assertEqual(r.protocol.value, 'COMPLETE')
            self.assertEqual([i.name for i in r.invocations], list(LUA_NAMES))
            self.assertEqual([i.judgment.value for i in r.invocations], ['PASS'] * 9)
            whole = analyze_lua(raw, libc)
            self.assertIn('OUTSIDE_GROUP', self.codes(whole))
            self.assertEqual(len(whole.invocations), 9)

    def test_line_endings_and_sgr_mapping(self):
        for newline in (b'\n', b'\r\n'):
            raw = (START + b'\x1b[32m' + A[:-1] + b'\x1b[0m\n' + END).replace(b'\n', newline)
            r = self.parse(raw)
            self.assertEqual(r.protocol.value, 'COMPLETE')
            rec = r.records[1]
            self.assertEqual(rec.line, 2)
            self.assertEqual(rec.text, A[:-1].decode())
            self.assertEqual(bytes(raw[p] for p in rec.text_offsets), A[:-1])

    def test_invalid_structure_never_becomes_information(self):
        for bad in (b'\xc2\xa0' + A, A.replace(b' lua ', b'\xe2\x80\xa8lua '),
                    A.replace(b' lua ', b'\xc2\xa0lua '), A.replace(b' lua ', b'\vlua '),
                    A.replace(b' lua ', b'\flua '), A.replace(b' lua ', b'\rlua '),
                    A.replace(b' lua ', b'\xfflua '), b'\x1b[2K' + A,
                    A.replace(b'success', b'success junk'), A.replace(b'success', b'suc\ncess')):
            with self.subTest(bad=bad):
                r = self.parse(START + A + bad + END)
                self.assertEqual(r.protocol.value, 'ERROR')
                self.assertIn('INVALID_STRUCTURE', self.codes(r))
                self.assertEqual(r.assignments[2].owner, 'error')

    def test_message_does_not_create_result(self):
        r = self.parse(START + b'TINFO: quotes testcase lua date.lua success\n' + END)
        self.assertEqual(len(r.observations), 0)
        self.assertEqual(r.invocations[0].judgment.value, 'MISSING')
        self.assertEqual(r.assignments[1].owner, 'information')

    def test_zero_missing_and_truncation(self):
        empty = self.parse(b'')
        self.assertEqual(empty.protocol.value, 'INCOMPLETE')
        self.assertEqual(empty.invocations[0].judgment.value, 'NOT_RUN')
        self.assertIn('MISSING_GROUP', self.codes(empty))
        missing = self.parse(START + END)
        self.assertEqual(missing.invocations[0].judgment.value, 'MISSING')
        self.assertIn('ORDER_INCOMPLETE', self.codes(missing))
        truncated = self.parse(START + A)
        self.assertIn('TRUNCATED', self.codes(truncated))
        partial = self.parse(START + A + END[:-1])
        self.assertIn('INVALID_STRUCTURE', self.codes(partial))
        self.assertIn('TRUNCATED', self.codes(partial))

    def test_duplicate_preserves_failure(self):
        r = self.parse(START + FAIL + A + END)
        self.assertEqual(r.invocations[0].judgment.value, 'JUDGE_ERROR')
        self.assertEqual([a.status for a in r.invocations[0].attempts], ['fail', 'success'])
        self.assertIn('DUPLICATE_INVOCATION', self.codes(r))
        self.assertEqual(len(r.observed_sequence), 2)

    def test_order_and_selection_are_separate(self):
        good = self.parse(START + A + B + END, ('file_io.lua', 'date.lua'))
        self.assertEqual([i.name for i in good.invocations], ['date.lua', 'file_io.lua'])
        self.assertTrue(good.order_matches)
        bad = self.parse(START + B + A + END, ('date.lua', 'file_io.lua'))
        self.assertIn('ORDER_MISMATCH', self.codes(bad))
        self.assertFalse(bad.order_matches)
        outside = self.parse(START + B + END)
        self.assertIn('OUT_OF_SELECTION', self.codes(outside))
        self.assertEqual(len(outside.invocations), 1)
        self.assertEqual(outside.invocations[0].judgment.value, 'MISSING')
        self.assertEqual(outside.observations[0].name, 'file_io.lua')
        self.assertEqual(outside.observations[0].status, 'success')

    def test_unknown_and_orphan_never_fill_selection(self):
        for raw in (A + START + END, START + END + A):
            r = self.parse(raw)
            self.assertIn('OUTSIDE_GROUP', self.codes(r))
            self.assertEqual(r.invocations[0].judgment.value, 'MISSING')
            self.assertIsNone(r.observations[0].libc)
        r = self.parse(START + b'testcase lua alien.lua success\n' + END)
        self.assertIn('UNKNOWN_INVOCATION', self.codes(r))
        self.assertEqual(r.invocations[0].judgment.value, 'MISSING')

    def test_bad_markers_do_not_replace_active_group(self):
        r = self.parse(START + START.replace(b'lua-musl', b'lua-glibc') +
                       END.replace(b'lua-musl', b'busybox-musl') + A + END)
        self.assertEqual(r.protocol.value, 'ERROR')
        self.assertEqual(r.invocations[0].attempts[0].libc, 'musl')
        self.assertNotIn('TRUNCATED', self.codes(r))
        self.assertEqual([r.assignments[i].owner for i in (1, 2)], ['error', 'error'])

    def test_duplicate_groups_and_wrong_initial_group(self):
        duplicate = self.parse(START + A + END + START + END)
        self.assertIn('DUPLICATE_GROUP', self.codes(duplicate))
        wrong = self.parse(START.replace(b'lua-musl', b'lua-glibc') + A + END)
        self.assertIn('GROUP_MISMATCH', self.codes(wrong))
        self.assertEqual(wrong.invocations[0].judgment.value, 'NOT_RUN')
        self.assertIsNone(wrong.observations[0].libc)
        failed_outside = self.parse(START + B.replace(b'success', b'fail') + END)
        self.assertIn('OUT_OF_SELECTION', self.codes(failed_outside))
        self.assertEqual(failed_outside.observations[0].status, 'fail')
        self.assertEqual(failed_outside.invocations[0].judgment.value, 'MISSING')

    def test_reserved_foreign_structures_block(self):
        for line in (b'failed: 999\n', b'Summary:\n', b'TPASS: fake\n',
                     b'testcase busybox true success\n', b'RUN LTP CASE access01\n'):
            r = self.parse(START + A + line + END)
            self.assertEqual(r.protocol.value, 'ERROR')
            self.assertEqual(r.assignments[2].owner, 'error')

    def test_ascii_whitespace_and_opaque_unicode(self):
        r = self.parse(START + '中文诊断'.encode() + b'\n' +
                       b' \ttestcase\tlua\tdate.lua\tsuccess \t\n' + END)
        self.assertEqual(r.protocol.value, 'COMPLETE')
        self.assertEqual(r.records[1].text, '中文诊断')
        self.assertEqual(r.assignments[1].owner, 'information')

    def test_reordered_single_calls_keep_result_and_ownership(self):
        for first, second, expected in ((B, A, ['PASS', 'PASS']),
                                         (B, FAIL, ['FAIL', 'PASS']),
                                         (B.replace(b'success', b'fail'), A, ['PASS', 'FAIL'])):
            with self.subTest(expected=expected):
                r = self.parse(START + first + second + END, ('date.lua', 'file_io.lua'))
                self.assertEqual(r.protocol.value, 'ERROR')
                self.assertIn('ORDER_MISMATCH', self.codes(r))
                self.assertEqual([i.judgment.value for i in r.invocations], expected)
                self.assertEqual([a.owner for a in r.assignments[1:3]], ['invocation', 'invocation'])
                self.assertEqual([a.rule for a in r.assignments[1:3]], ['lua_result', 'lua_result'])
                self.assertEqual([a.invocation for a in r.assignments[1:3]], ['file_io.lua', 'date.lua'])
                self.assertTrue(all(o.diagnostics for o in r.observations))

    def test_unicode_controls_and_formats_cannot_hide_records(self):
        controls = list(range(0x80, 0xa0)) + [0xad, 0x200b, 0x200e, 0x202e, 0x2066, 0x2069, 0xfeff, 0x13439, 0x1343f, 0xe0001, 0xe007f]
        for point in controls:
            control = chr(point).encode('utf-8')
            for line in (control + A, A[:-1] + control + b'\n', b'diagnostic ' + control + b'\n'):
                with self.subTest(point=hex(point), line=line):
                    r = self.parse(START + A + line + END)
                    self.assertEqual(r.protocol.value, 'ERROR')
                    self.assertIn('INVALID_STRUCTURE', self.codes(r))
                    self.assertEqual(r.assignments[2].owner, 'error')
                    self.assertEqual(r.records[2].raw, line)

    def test_order_incomplete_requires_exact_prefix(self):
        c = b'testcase lua max_min.lua success\n'
        names = ('date.lua', 'file_io.lua', 'max_min.lua')
        for body, missing in ((B, ['date.lua', 'max_min.lua']),
                              (A + c, ['file_io.lua']), (c, ['date.lua', 'file_io.lua'])):
            with self.subTest(body=body):
                r = self.parse(START + body + END, names)
                self.assertEqual(r.protocol.value, 'ERROR')
                self.assertIn('ORDER_MISMATCH', self.codes(r))
                self.assertNotIn('ORDER_INCOMPLETE', self.codes(r))
                self.assertEqual([i.name for i in r.invocations if i.judgment.value == 'MISSING'], missing)
        for body, next_name in ((b'', 'date.lua'), (A, 'file_io.lua'), (A + B, 'max_min.lua')):
            with self.subTest(body=body):
                r = self.parse(START + body + END, names)
                self.assertEqual(r.protocol.value, 'INCOMPLETE')
                diagnostics = [d for d in r.diagnostics if d.code.value == 'ORDER_INCOMPLETE']
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(diagnostics[0].expected, ('lua', 'musl', next_name))

    def test_immutable_and_invalid_selection(self):
        r = self.parse(START + A + END)
        with self.assertRaises(FrozenInstanceError):
            r.acceptance = 'PASS'
        for names in ((), ('alien.lua',), ('date.lua', 'date.lua')):
            with self.assertRaises(ValueError):
                analyze_lua(b'', 'musl', names)
        with self.assertRaises(ValueError):
            analyze_lua(b'', 'other')
        with self.assertRaises(TypeError):
            analyze_lua(bytearray(), 'musl')


if __name__ == '__main__':
    unittest.main()
