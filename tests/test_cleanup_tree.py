#!/usr/bin/env python3
"""整区删除的真实文件系统中断恢复，不模拟清理结果。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import station_cleanup_tree as tree, station_directories, station_operation


class CleanupTreeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ws = self.root / 'station'
        (self.ws / '.agenticops').mkdir(parents=True)
        self.source = self.ws / 'source'
        self.source.mkdir()
        (self.source / '.git').mkdir()
        (self.source / '.git/config').write_text('metadata')
        (self.source / 'file').write_text('source')
        self.operation = {'operation_id': 'op-tree-cleanup-test', 'steps': {}}

    def test_remove_git_tree_and_preserve_external_link_target(self):
        external = self.root / 'external'
        external.write_text('keep')
        (self.source / 'link').symlink_to(external)
        item = tree.entry(self.ws, 'source', recreate=True)
        tree.remove(self.ws, self.operation, item)
        self.assertEqual([], list(self.source.iterdir()))
        self.assertEqual('keep', external.read_text())
        tree.remove(self.ws, self.operation, item)
        tree.finish(self.ws, self.operation)
        self.assertFalse((self.ws / '.agenticops/cleanup').exists())

    def test_partial_delete_resumes_original_private_directory(self):
        item = tree.entry(self.ws, 'source', recreate=True)
        original = station_directories._walk
        def interrupted(fd, device, delete=False, allow_git=False):
            if delete:
                import os
                os.unlink('file', dir_fd=fd)
                raise KeyboardInterrupt()
            return original(fd, device, delete, allow_git)
        with mock.patch.object(station_directories, '_walk', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                tree.remove(self.ws, self.operation, item)
        self.assertFalse(self.source.exists())
        operation = json.loads((self.ws / '.agenticops/operation.json').read_text())
        tree.remove(self.ws, operation, item)
        self.assertEqual([], list(self.source.iterdir()))

    def test_late_replacement_not_deleted(self):
        item = tree.entry(self.ws, 'source', recreate=True)
        with mock.patch.object(station_directories, '_walk', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                tree.remove(self.ws, self.operation, item)
        self.source.mkdir()
        (self.source / 'new').write_text('keep')
        with self.assertRaisesRegex(ValueError, '新内容'):
            tree.remove(self.ws, self.operation, item)
        self.assertEqual('keep', (self.source / 'new').read_text())

    def test_completed_root_with_late_content_is_preserved(self):
        item = tree.entry(self.ws, 'source', recreate=True)
        tree.remove(self.ws, self.operation, item)
        (self.source / 'new').write_text('keep')
        with self.assertRaisesRegex(ValueError, '新内容'):
            tree.remove(self.ws, self.operation, item)
        self.assertEqual('keep', (self.source / 'new').read_text())

    def test_changed_content_needs_new_decision(self):
        item = tree.entry(self.ws, 'source', recreate=True)
        (self.source / 'file').write_text('changed')
        with self.assertRaisesRegex(ValueError, '范围已变化'):
            tree.remove(self.ws, self.operation, item)
        self.assertEqual({}, self.operation['steps'])

    def test_receipt_write_interruption_can_finish_empty_root(self):
        item = tree.entry(self.ws, 'source', recreate=True)
        with mock.patch.object(station_operation, 'receipt', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                tree.remove(self.ws, self.operation, item)
        tree.remove(self.ws, self.operation, item)
        self.assertEqual([], list(self.source.iterdir()))

    def test_state_and_external_paths_rejected(self):
        for name in ('../external', '/tmp', '.agenticops', 'source/../external'):
            with self.assertRaises(ValueError):
                tree.entry(self.ws, name)


if __name__ == '__main__':
    unittest.main()
