"""字面 HTML 表示的严格往返、原操作恢复及拒绝边界。"""
from pathlib import Path
import sys
import contextlib
import io
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import jira_literal_html as body


class LiteralHtmlTests(unittest.TestCase):
    def setUp(self):
        self.original = 'TAP-123：首轮验证\n\n处置：observed；P-01~P-11 [旧_新] *字面*\n路径 a\\b；正则 \\n\n\n缺口：未执行 <script> & \\u65e7'
        self.expected = body.render(self.original)
        self.receipt = {'site': 'https://example.test', 'issue_key': 'TAP-123', 'comment_id': '100',
                        'source_ref': 'fixture:native-jira/100', 'author_account_id': 'fixture-user', 'body': self.expected}

    def compare(self, receipt=None, expected=None):
        return body.compare(self.original, expected or self.expected, receipt or self.receipt,
                            'https://example.test', 'TAP-123', '100')

    def test_roundtrip_keeps_literals_and_only_normalizes_transport(self):
        self.assertIn('&lt;script&gt;', self.expected)
        self.assertIn('<h3>', self.expected)
        result = self.compare()
        self.assertEqual(self.original, result['body'])
        self.assertEqual('exact-text', result['body_representation'])
        self.assertEqual('fixture-user', result['author_account_id'])
        self.assertNotIn('status', result)
        self.assertNotIn('operation_id', result)
        receipt = dict(self.receipt, body='\n' + self.expected.replace('<p>', '<p data-local-id="opaque">').replace('&amp;', '&#38;') + '\n')
        self.assertEqual(result, self.compare(receipt))
        self.assertEqual(result, self.compare())

    def test_identity_and_source_cannot_be_invented(self):
        for key, value in [('site', 'https://other.test'), ('issue_key', 'TAP-124'),
                           ('comment_id', '101'), ('source_ref', ''), ('source_ref', None)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compare(dict(self.receipt, **{key: value}))

    def test_real_text_structure_and_unsupported_semantics_fail_closed(self):
        for actual in [self.expected.replace('P-11', 'P-12'), self.expected.replace('a\\b', 'ab'),
                       self.expected.replace('\\n', '\n'), self.expected.replace('\\u65e7', '旧'),
                       self.expected.replace('<h3>', '<p>').replace('</h3>', '</p>'),
                       self.expected.replace('<strong>', '').replace('</strong>', ''),
                       self.expected + '<!-- hidden -->', '<!DOCTYPE html>' + self.expected,
                       '<?xml version="1.0"?>' + self.expected,
                       self.expected.replace('<p>', '<p style="display:none">'),
                       self.expected + '<a href="https://wrong.test">same text</a>',
                       self.expected + '<table><tr><td>text</td></tr></table>',
                       self.expected.replace('</p>', ''), self.expected + '<p/>']:
            with self.subTest(actual=actual[-50:]), self.assertRaises(ValueError):
                self.compare(dict(self.receipt, body=actual))
        with self.assertRaises(ValueError):
            self.compare(expected=self.expected.replace('P-11', 'P-12'))

    def test_saved_intent_not_current_renderer_controls_readback(self):
        from unittest import mock
        with mock.patch.object(body, 'render', side_effect=AssertionError('不能重建未知操作意图')):
            self.assertEqual(self.original, self.compare()['body'])
        self.assertEqual('标题\n\n甲\n乙', body.compare('标题\n\n甲\n乙', body.render('标题\n\n甲\n乙'),
                         dict(self.receipt, body=body.render('标题\n\n甲\n乙')),
                         'https://example.test', 'TAP-123', '100')['body'])

    def test_cli_compare_is_read_only_and_rejects_duplicate_json_fields(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'plain').write_text(self.original)
            (root / 'intent').write_text(self.expected)
            (root / 'readback').write_text(json.dumps(self.receipt))
            argv = ['jira_literal_html.py', 'compare', '--body-file', str(root / 'plain'),
                    '--expected-html-file', str(root / 'intent'), '--readback', str(root / 'readback'),
                    '--site', 'https://example.test', '--issue-key', 'TAP-123', '--comment-id', '100']
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            output = io.StringIO()
            with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
                self.assertEqual(0, body.main())
            self.assertEqual(self.original, json.loads(output.getvalue())['body'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            (root / 'readback').write_text('{"body":"fixture-private-text","body":"duplicate"}')
            output = io.StringIO()
            with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
                self.assertEqual(3, body.main())
            self.assertNotIn('fixture-private-text', output.getvalue())
            self.assertNotIn(str(root), output.getvalue())
            self.assertIn('JSON', json.loads(output.getvalue())['reason'])

    def test_invalid_literal_inputs(self):
        for value in ('', '\x00', '甲\r乙', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                body.render(value)


if __name__ == '__main__':
    unittest.main()
