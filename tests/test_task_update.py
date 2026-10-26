"""临时真实 Git 远端、隔离安装与工位的启动前更新回归；Agent 为测试替身。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TaskUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ao-task-update-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.seed, self.install, self.station = (self.root / n for n in ('seed', 'install', 'station'))
        self.seed.mkdir()
        for name in ('adapters','bootstrap','contracts','docs','gate','policies','projects','workflow','skills'):
            shutil.copytree(ROOT / name, self.seed / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('agenticops','.gitignore'):
            shutil.copy2(ROOT / name, self.seed / name)
        config = self.root / 'gitconfig'
        config.write_text('[user]\n name = Fixture\n email = fixture@example.test\n')
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(config), PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('AO_PRODUCT_LEASE', None)
        self.run_cmd('git','-C',str(self.seed),'init','-q','-b','channel')
        self.original = self.commit('initial lease client')
        self.run_cmd('bash',str(self.seed/'bootstrap/install.sh'),'--install-home',str(self.install),
                     '--repository',str(self.seed),'--branch','channel','--source-pool',str(self.root/'pool'))
        self.run_cmd(str(self.install/'agenticops'),'station','init','--station',str(self.station),'--project','tapdata','--agent','codex')
        binary = self.root/'bin'; binary.mkdir()
        agent = binary/'codex'
        agent.write_text('#!/usr/bin/env python3\nimport os,sys\nprint("session-ready",flush=True)\nif os.environ.get("WAIT_AGENT"): sys.stdin.readline()\n')
        agent.chmod(0o755)
        self.env['PATH'] = str(binary)+os.pathsep+self.env['PATH']

    def run_cmd(self, *args, check=True, env=None):
        result = subprocess.run(args, capture_output=True, text=True, env=env or self.env, timeout=60)
        if check:
            self.assertEqual(0,result.returncode,result.stdout+'\n'+result.stderr)
        return result

    def commit(self, text):
        self.run_cmd('git','-C',str(self.seed),'add','.')
        self.run_cmd('git','-C',str(self.seed),'commit','-qm',text)
        return self.run_cmd('git','-C',str(self.seed),'rev-parse','HEAD').stdout.strip()

    def advance(self, incompatible=False):
        (self.seed/'docs/fixture-update.md').write_text('new product content\n')
        if incompatible:
            p=self.seed/'contracts/station-state-compatibility.json'
            data=json.loads(p.read_text());data['station_state_epoch']+=1;p.write_text(json.dumps(data))
        return self.commit('next version')

    def state(self):
        return json.loads((self.install/'.local/product.json').read_text())

    def start(self, *extra, check=True):
        return self.run_cmd(str(self.install/'agenticops'),'station','start','codex','--station',str(self.station),
                            '--non-interactive',*extra,check=check)

    def approved_start(self):
        packet = json.loads(self.run_cmd(str(self.install/'agenticops'), 'update', '--check', '--probe').stdout)
        return self.start('--writers-stopped', '--apply-update', '--update-current-ref', packet['original_ref'], '--update-target-ref', packet['target_ref'], '--expected-stations', json.dumps(packet['stations']))

    def test_upgrade_repair_multiarchive_independent_restore_and_both_cleanups(self):
        # 真实本地 Git 与安装切换；Agent/Jira 使用显式夹具，不冒充线上服务验收。
        sys.path.insert(0, str(ROOT / 'tests'))
        import test_task_handoff
        from workflow import task_store
        from unittest import mock
        flow = test_task_handoff.HandoffTests()
        flow.root = self.root / 'business-fixture'
        flow.root.mkdir()
        flow.ws = self.station
        flow.product = self.seed
        cleanup = json.loads((self.seed / 'projects/tapdata/station-clean.json').read_text())
        cleanup['resources'] = []
        (self.seed / 'projects/tapdata/station-clean.json').write_text(json.dumps(cleanup))
        flow.prepare_engineering()
        self.advance()
        self.approved_start()
        flow.product = self.install
        with mock.patch("workflow.station_source.refresh_shared_references"):
            source_path = flow.prepare_task()
        original = task_store.read_current(self.station)
        legacy = json.loads(json.dumps(original))
        legacy['schema_version'] = 2
        legacy['current']['old_phase'] = legacy['current'].pop('stage')
        # 明确模拟旧生产者格式；产品修补不直接手改控制或业务事实。
        task_store.current_path(self.station).write_text(json.dumps(legacy))
        before = task_store.current_path(self.station).read_bytes()
        operation_before = (self.station / '.agenticops/operation.json').read_bytes()
        rejected = self.run_cmd(sys.executable, str(self.install / 'workflow/task.py'), 'takeover', '--dir', str(self.station), check=False)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn('状态结构无效', rejected.stderr)
        self.assertEqual(operation_before, (self.station / '.agenticops/operation.json').read_bytes())
        target = self.advance(incompatible=True)
        self.approved_start()
        self.assertEqual(target, self.state()['current_ref'])
        self.assertEqual(before, task_store.current_path(self.station).read_bytes())
        report = task_store.inspect_data(self.station)
        request = {'source_digest': report['source_digest'], 'renames': {'old_phase': 'stage'},
                   'analysis_ref': '夹具原合同与项目事实核实字段语义不变'}
        preview = task_store.preview_data_repair(self.station, request)
        task_store.apply_data_repair(self.station, request, preview['proposal_digest'], '测试研发确认差异', True)
        self.assertEqual(original, task_store.read_current(self.station))
        flow.task = task_store.read_task(self.station)
        shutil.rmtree(self.root / "pool")
        with mock.patch("workflow.station_source.refresh_shared_references"):
            flow.verify_independent_resume_and_cleanup(source_path)

    def test_invalid_operation_allows_diagnostic_session_without_overwriting_it(self):
        operation = self.station / '.agenticops/operation.json'
        operation.write_text('invalid historical operation')
        result = self.start()
        self.assertIn('session-ready', result.stdout)
        self.assertIn('会话可启动以分析修补', result.stderr)
        self.assertEqual('invalid historical operation', operation.read_text())
        rejected = self.run_cmd(sys.executable, str(self.install / 'workflow/task.py'), 'takeover', '--dir', str(self.station), check=False)
        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual('invalid historical operation', operation.read_text())

    def test_safe_start_updates_repairs_and_starts_new_session(self):
        second=self.root/'second'
        self.run_cmd(str(self.install/'agenticops'),'station','init','--station',str(second),'--project','tapdata','--agent','codex')
        before=(second/'.agenticops/init.json').read_bytes()
        target=self.advance()
        result=self.approved_start()
        self.assertEqual(before,(second/'.agenticops/init.json').read_bytes())
        self.assertIn('session-ready',result.stdout)
        self.assertEqual(target,self.state()['current_ref'])
        self.assertEqual(self.original,self.state()['previous_ref'])
        self.run_cmd(str(self.install/'agenticops'),'station','doctor','--station',str(self.station))
        self.assertIsNone(json.loads((self.station/'.agenticops/current-task.json').read_text())['current'])
        self.assertFalse((self.station/'.agenticops/operation.json').exists())
        self.start()
        self.assertEqual(self.original,self.state()['previous_ref'])

    def test_update_requires_choice_without_unmanaged_session_proof(self):
        target=self.advance()
        result=self.start(check=False)
        self.assertEqual(3,result.returncode,result.stderr)
        self.assertIn('unmanaged_sessions_unverified',result.stderr)
        self.assertEqual(self.original,self.state()['current_ref'])
        self.assertIn('session-ready',self.start('--update-current-ref',self.original,'--update-target-ref',target).stdout)
        self.assertEqual(3,self.start(check=False).returncode)

    def test_feature_cross_epoch_start_updates_without_epoch_barrier(self):
        before = (self.station/'.agenticops/current-task.json').read_bytes()
        origin = (self.station/'.agenticops/init.json').read_bytes()
        target = self.advance(incompatible=True)
        result = self.run_cmd(str(self.install/'agenticops'),'update','--check','--probe')
        packet = json.loads(result.stdout)
        self.assertTrue(packet['compatible'])
        self.assertNotIn('incompatible_epoch', packet['blockers'])
        self.assertEqual(target, packet['target_ref'])
        refused = self.run_cmd(str(self.install/'agenticops'),'update', check=False)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('writers-stopped', refused.stderr)
        result = self.approved_start()
        self.assertIn('session-ready', result.stdout)
        self.assertEqual(target, self.state()['current_ref'])
        self.assertEqual(before, (self.station/'.agenticops/current-task.json').read_bytes())
        refreshed = json.loads((self.station/'.agenticops/init.json').read_text())
        self.assertEqual(json.loads(origin)['station_state_epoch'], refreshed['station_state_epoch'])
        self.assertEqual(target, refreshed['product_ref'])
        self.assertIn('session-ready', self.start().stdout)

    def test_legacy_cross_epoch_keeps_incompatible_guidance(self):
        manifest = self.seed/'contracts/station-state-compatibility.json'
        data = json.loads(manifest.read_text())
        data.pop('validation', None)
        manifest.write_text(json.dumps(data))
        self.advance(incompatible=True)
        packet = json.loads(self.run_cmd(str(self.install/'agenticops'),'update','--check','--probe').stdout)
        self.assertFalse(packet['compatible'])
        self.assertIn('incompatible_epoch', packet['blockers'])
        result = self.start('--writers-stopped', check=False)
        self.assertEqual(3, result.returncode)
        self.assertIn('旧模式目标不兼容', result.stderr)
        self.assertIn('退出并 purge', result.stderr)
        self.assertEqual(self.original, self.state()['current_ref'])

    def test_new_takeover_checks_before_creating_operation(self):
        self.advance()
        result=self.run_cmd(sys.executable,str(self.install/'workflow/task.py'),'takeover','--dir',str(self.station),check=False)
        self.assertEqual(3,result.returncode,result.stderr)
        self.assertFalse((self.station/'.agenticops/operation.json').exists())
        self.assertFalse((self.install/'.archive').exists())

    def test_managed_session_holds_lease_between_tool_calls(self):
        env=dict(self.env,WAIT_AGENT='1')
        proc=subprocess.Popen([str(self.install/'agenticops'),'station','start','codex','--station',str(self.station),'--non-interactive'],
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.PIPE,text=True,env=env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(15):
            if proc.stdout.readline().strip()=='session-ready': break
        else: self.fail('session did not start')
        self.advance()
        result=self.run_cmd(str(self.install/'agenticops'),'update',check=False)
        self.assertEqual(3,result.returncode,result.stderr)
        self.assertEqual(self.original,self.state()['current_ref'])
        proc.communicate('\n',timeout=15)
        self.assertEqual(0,proc.returncode)
        self.approved_start()
        self.assertNotEqual(self.original,self.state()['current_ref'])

    def test_network_failure_continues_trusted_version_without_latest_claim(self):
        self.seed.rename(self.root/'offline')
        result=self.start()
        self.assertIn('session-ready',result.stdout)
        self.assertIn('未能确认',result.stderr)
        self.assertNotIn('已经最新',result.stdout+result.stderr)
        self.assertEqual(self.original,self.state()['current_ref'])

    def test_changed_target_invalidates_current_version_choice(self):
        target = self.advance()
        (self.seed/'docs/fixture-update.md').write_text('another target\n')
        self.commit('changed target')
        result = self.start('--update-current-ref',self.original,'--update-target-ref',target,check=False)
        self.assertNotEqual(0,result.returncode)
        self.assertIn('目标已变化',result.stderr)
        self.assertEqual(self.original,self.state()['current_ref'])


    def test_stopped_writers_are_not_update_approval(self):
        self.advance()
        result = self.start('--writers-stopped', check=False)
        self.assertEqual(3, result.returncode)
        self.assertEqual(self.original, self.state()['current_ref'])
        self.assertIn('--apply-update', result.stderr)

    def test_active_task_start_checks_update_preserves_run_and_can_continue_old(self):
        path = self.station/'.agenticops/current-task.json'
        document = json.loads(path.read_text())
        document['current'] = {'issue_key':'TAP-123','run_id':'run-abc12345','task_class':'technical_task',
            'stage':'waiting_takeover','outcome':'in_progress','facts':{},'history':[], 'pending':None,
            'engineering_baseline':{'status':'resolving'}, 'task_repositories':{},'terminal_proof':None,'archive_ref':None}
        path.write_text(json.dumps(document))
        before = path.read_bytes()
        target = self.advance(incompatible=True)
        self.assertEqual(3, self.start(check=False).returncode)
        self.assertIn('session-ready', self.start('--update-current-ref', self.original, '--update-target-ref', target).stdout)
        self.assertEqual(self.original, self.state()['current_ref'])
        self.assertEqual(before, path.read_bytes())
        self.assertIn('session-ready', self.approved_start().stdout)
        self.assertEqual(target, self.state()['current_ref'])
        self.assertEqual(before, path.read_bytes())

    def test_update_approval_binds_scope_and_exact_target(self):
        target = self.advance()
        result = self.start('--apply-update', '--writers-stopped', '--update-current-ref', self.original,
                            '--update-target-ref', target, '--expected-stations', '[]', check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('登记工位范围', result.stderr)
        self.assertEqual(self.original, self.state()['current_ref'])
        (self.seed/'docs/fixture-update.md').write_text('third version')
        self.commit('third')
        result = self.start('--apply-update', '--writers-stopped', '--update-current-ref', self.original,
                            '--update-target-ref', target, '--expected-stations', json.dumps([str(self.station)]), check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('原/目标提交', result.stderr)
        self.assertEqual(self.original, self.state()['current_ref'])

    def test_existing_run_skips_network_check(self):
        path = self.station/'.agenticops/current-task.json'
        document = json.loads(path.read_text())
        document['current'] = {'issue_key':'TAP-123','run_id':'run-abc12345','task_class':'technical_task',
            'stage':'waiting_takeover','outcome':'in_progress','facts':{},'history':[], 'pending':None,
            'engineering_baseline':{'status':'resolving'}, 'task_repositories':{},'terminal_proof':None,'archive_ref':None}
        path.write_text(json.dumps(document))  # 合法历史状态夹具，不是用户工位。
        self.seed.rename(self.root/'offline')
        result = self.run_cmd(sys.executable,str(self.install/'bootstrap/task_update_check.py'),'--station',str(self.station))
        self.assertEqual('resume',json.loads(result.stdout)['status'])
        self.assertEqual(document,json.loads(path.read_text()))


if __name__=='__main__':
    unittest.main()
