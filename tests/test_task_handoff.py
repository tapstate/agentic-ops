"""真实本地 Git 远端验证归档接续；Jira 仅提供显式工具回读夹具。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_station_records as records
from station_fixture import coordination_proof
from workflow import station, station_archive, station_operation, task_store, task_handoff


class HandoffTests(unittest.TestCase):
    setUp = records.RecordTests.setUp
    prepare_engineering = records.RecordTests.prepare_engineering
    write = records.RecordTests.write
    git = records.RecordTests.git
    takeover = records.RecordTests.takeover

    def prepare_task(self):
        task = self.takeover()
        self.branch = task_store.generated_work_branch(self.ws, task)
        station.scope_change(self.ws, task['issue_key'], task['run_id'], task['_revision'],
                             'op-handoff-scope', 'tapdata/tapdata', self.branch, 'develop', ['file.txt'], 'unit')
        path = self.ws / 'source/tapdata/tapdata'
        self.git(path, 'config', 'user.name', 'Test')
        self.git(path, 'config', 'user.email', 'test@example.com')
        (path / 'file.txt').write_text('retained code\n')
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-m', 'retained')
        self.git(path, 'push', 'origin', self.branch)
        self.task = task_store.read_task(self.ws)
        self.request = {'handoff': {'plan': '已确认实施方案', 'progress': '代码已实现，待验证',
                                   'remaining': '执行业务验证', 'environment': '新工位配置测试数据库'}}
        return path

    def archive(self, op='op-handoff-first', receipt=None):
        return station.execute(self.ws, 'archive', self.task['issue_key'], self.task['run_id'],
                               self.task['_revision'], op, self.request, jira_readback=receipt)

    def readback(self, operation, comment='101'):
        result = operation['archive_result']
        return {'site': result['site'], 'issue_key': result['issue_key'], 'comment_id': comment,
                'body': result['body'], 'source_ref': 'fixture:jira/comment/' + comment}

    def test_repeated_archive_and_new_station_continuation(self):
        self.prepare_task()
        pending = self.archive()
        self.assertEqual('awaiting_jira', pending['archive_result']['status'])
        before = copy.deepcopy(pending['archive_payload'])
        self.assertEqual(before, self.archive()['archive_payload'])
        saved = self.archive(receipt=self.readback(pending))
        self.assertEqual('jira', saved['archive_result']['storage'])
        self.assertEqual('done', saved['status'])
        directory = self.product / '.archive' / self.task['run_id']
        snapshots = {p.name: p.read_bytes() for p in directory.glob('result-*.json')}
        again = self.archive('op-handoff-second')
        self.assertEqual(pending['archive_result']['body'], again['archive_result']['body'])
        self.archive('op-handoff-second', self.readback(again))
        self.assertEqual(snapshots, {p.name: p.read_bytes() for p in directory.glob('result-*.json')})
        packet = saved['archive_payload']['snapshot']['handoff']['packet']
        continuation = task_handoff.continuation_input(packet)
        # 新工位不读取旧归档；仅从已回读正文取回的 packet 生成现有接管输入。
        old_ws = self.ws
        self.ws = self.root / 'new-station'
        self.ws.mkdir()
        for name in ('source', 'config', 'runtime', '.agenticops'):
            (self.ws / name).mkdir()
        for name in ('station.json', 'init.json'):
            self.write(self.ws / '.agenticops' / name, json.loads((old_ws / '.agenticops' / name).read_text()))
        task_store.initialize_current(self.ws)
        self.request = {
            'issue_key': 'TAP-123', 'task_class': 'technical_task', 'version': 'develop',
            'profile': 'full-application', 'explicit_branches': {'tapdata/tapdata': 'develop'}, 'continuations': continuation}
        # 归档不携带接管权；本测试单独提供新工位已核实交接的夹具。
        coordination_proof(self.ws, 'TAP-123')
        station.takeover(self.ws, self.request, 'op-new-handoff-run', 0)
        recovered = task_store.read_task(self.ws)
        row = continuation['tapdata/tapdata']
        station.scope_change(self.ws, recovered['issue_key'], recovered['run_id'], recovered['_revision'],
                             'op-new-handoff-scope', 'tapdata/tapdata', row['work_branch'], 'develop',
                             ['file.txt'], 'unit', row['expected_head'])
        self.assertEqual(row['expected_head'], self.git(self.ws / 'source/tapdata/tapdata', 'rev-parse', 'HEAD'))
        self.assertFalse(task_store.authorization_path(self.ws, 'TAP-123').exists())

    def test_changed_material_appends_and_bad_readback_preserves_pending(self):
        self.prepare_task()
        first = self.archive()
        self.archive(receipt=self.readback(first))
        self.request['handoff']['progress'] = '新增验证结果，继续剩余工作'
        second = self.archive('op-handoff-changed')
        self.assertNotEqual(first['archive_result']['content_digest'], second['archive_result']['content_digest'])
        bad = self.readback(second, '102')
        bad['issue_key'] = 'TAP-999'
        with self.assertRaisesRegex(ValueError, '不匹配'):
            self.archive('op-handoff-changed', bad)
        self.assertEqual('awaiting_jira', self.archive('op-handoff-changed')['archive_result']['status'])
        self.archive('op-handoff-changed', self.readback(second, '102'))
        self.assertEqual(2, len(list((self.product / '.archive' / self.task['run_id']).glob('result-*.json'))))

    def test_unpushed_changes_require_preservation_or_explicit_discard(self):
        path = self.prepare_task()
        (path / 'file.txt').write_text('not retained\n')
        with self.assertRaisesRegex(ValueError, '未提交'):
            self.archive()
        self.request['handoff']['discard_repositories'] = {'tapdata/tapdata': 'fixture:user:explicit-discard'}
        pending = self.archive()
        packet = pending['archive_payload']['snapshot']['handoff']['packet']
        self.assertEqual({}, task_handoff.continuation_input(packet))
        self.assertEqual('discarded', packet['repositories']['tapdata/tapdata']['disposition'])
        self.assertIn('not retained', (path / 'file.txt').read_text())

    def test_unpushed_commit_and_sensitive_material_do_not_start_archive(self):
        path = self.prepare_task()
        (path / 'file.txt').write_text('unpublished commit\n')
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-m', 'not pushed')
        with self.assertRaisesRegex(ValueError, '远端分支'):
            self.archive()
        self.git(path, 'push', 'origin', self.branch)
        self.request['handoff']['plan'] = 'password=fixture-secret'
        with self.assertRaises(ValueError) as error:
            self.archive()
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertNotEqual('archive', station_operation.read(self.ws)['kind'])

    def test_unknown_external_requires_recovery_clues(self):
        self.prepare_task()
        from workflow import external_sync
        warning = {'kind': 'comment:old', 'status': 'unknown', 'reason': 'timeout', 'source_ref': 'fixture:original'}
        with mock.patch.object(external_sync, 'existing_warnings', return_value=[warning]):
            with self.assertRaisesRegex(ValueError, '原操作线索'):
                self.archive()
            self.request['handoff']['unresolved_operations'] = {'comment:old': 'fixture:original；原评论正文及工具操作编号已保全'}
            pending = self.archive()
        packet = pending['archive_payload']['snapshot']['handoff']['packet']
        self.assertEqual('unknown', packet['pending_external'][0]['status'])

    def test_unsent_cancellation_and_unknown_write_cleanup_boundary(self):
        self.prepare_task()
        pending = self.archive()
        from workflow import station_operation
        with self.assertRaisesRegex(ValueError, 'Jira 写入尚未核实'):
            station_operation.handoff_clean(self.ws, 'op-clean-pending-point', self.task['_revision'], {}, self.task['run_id'], {})
        receipt = self.readback(pending)
        receipt.pop('comment_id')
        receipt.update(status='not_sent', decision_ref='fixture:user:cancel-before-send')
        result = self.archive(receipt=receipt)
        self.assertEqual('done', result['status'])
        self.assertEqual('discarded', result['archive_result']['status'])
        self.assertFalse(list((self.product / '.archive' / self.task['run_id']).glob('result-*.json')))
        with self.assertRaisesRegex(ValueError, '不能替换'):
            self.archive(receipt=self.readback(pending))

    def test_lost_finish_reuses_original_jira_receipt_and_snapshot(self):
        self.prepare_task()
        pending = self.archive()
        from workflow import station_operation
        with mock.patch.object(station_operation, 'finish', side_effect=OSError('lost finish')):
            with self.assertRaises(OSError):
                self.archive(receipt=self.readback(pending))
        directory = self.product / '.archive' / self.task['run_id']
        before = {p.name: p.read_bytes() for p in directory.glob('result-*.json')}
        resumed = self.archive()
        self.assertEqual('done', resumed['status'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.glob('result-*.json')})

    def test_local_copy_failure_does_not_erase_verified_jira_point(self):
        self.prepare_task()
        pending = self.archive()
        with mock.patch.object(station_archive, 'publish', side_effect=PermissionError('fixture disk')):
            saved = self.archive(receipt=self.readback(pending))
        self.assertEqual('done', saved['status'])
        self.assertEqual('saved', saved['archive_result']['status'])
        self.assertIn('local_warning', saved['archive_result'])


if __name__ == '__main__':
    unittest.main()
