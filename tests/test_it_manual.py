"""AO-287：真实质量归约与 Jira 回读夹具验证，不把夹具当外部联调。"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import it_manual, quality, quality_contract, test_coverage, verification
import test_quality as fixtures
from test_quality import proof


class ManualClosureTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.TestCoverageClosureTests()
        fixture.setUp()
        for name in ('repo', 'head', 'row', 'ctx', 'result', 'material', 'task', 'packet', 'version'):
            setattr(self, name, copy.deepcopy(getattr(fixture, name)))
        self.rules = json.loads((ROOT / 'projects/tapdata/quality.json').read_text())
        self.policy = it_manual.config(self.rules)
        self.ctx.update(issue_key='TAP-123', run_id='run-fixture')
        self.ctx['facts']['fix_plan']['integration_tests'] = [self.row]
        self.material['results'] = [self.result]
        self.model = {'items': {}, 'checkpoints': {}, 'publications': {}}
        self.row.update(level='L3', framework_supported=False)
        self.row['cases'][0]['manual_item'] = 'manual-one'
        self.result.update(level='L3', result='NOT_RUN')
        self.result['case_coverage'].update(status='missing', framework_supported=False,
            cases=[{'id': 'behavior', 'status': 'missing', 'result': 'NOT_RUN', 'report_ref': 'fixture:unsupported'}])
        plan = {'id': 'manual-one', 'checkpoint': 'q4-acceptance', 'timing': 'after_fix',
            'case_ref': 'TAP-456', 'case_version': 'jira-v1', 'case_status': 'existing',
            'method': 'manual', 'repository': self.repo, 'target_revision': self.head,
            'criterion': '目标业务行为符合预期', 'steps': '准备输入，执行操作，核对预期输出',
            'expected_result': 'PASS', 'scope': self.row['scope']}
        self.apply('item', {'plan': plan, 'reason': '手动验证目标场景'})
        item = self.model['items']['manual-one']
        self.apply('select', {'item_id': 'manual-one', 'digest': quality.plan_digest(item,self.rules,self.ctx), 'proof': proof()})
        execution = {k: plan[k] for k in ('case_ref','case_version','method','repository','target_revision')}
        execution.update(id='run-one', origin='manual', source_ref='fixture:manual-evidence',
            environment='fixture', observed_at=proof()['at'], raw_result='PASS', failure_kind='none', observation='实际观察符合预期')
        self.apply('execute', {'item_id':'manual-one','execution':execution})
        self.apply('decide', {'item_id':'manual-one','digest':quality.item_digest(item,self.rules,self.ctx),
            'decision':{'outcome':'accept','evidence_id':'run-one','reason':'人工核对结果','proof':proof()}})
        observation = {'source_ref':'fixture:jira-readback','observed_at':proof()['at'],'business_issues':['TAP-123']}
        self.follow = {'scope':self.row['scope'],'level':'L3', 'tests':[
            dict(observation,key='TAP-456',url=self.policy['jira_base_url']+'/browse/TAP-456',test_type='Manual',case_version='jira-v1',
                 definition_refs={k:'fixture:jira-definition#'+k for k in
                     ('goal','preconditions','steps','input_data','expected_result','cleanup')})],
            'task':dict(observation,key='TAP-789',url=self.policy['jira_base_url']+'/browse/TAP-789',
                assignee_account_id=self.policy['assignee_account_id'],manual_tests=['TAP-456'],
                reason='框架缺少该场景能力',dedup_ref='fixture:按场景搜索结果并核验后复用')}
        self.local = copy.deepcopy(self.material)
        self.local.update(kind='local',it_followups=[self.follow])
        self.local['results']=[{'scope':self.row['scope'],'result':'PASS','tests':1,'failures':0,'errors':0,'skipped':0,'report_ref':'fixture:local'}]
        self.refresh()

    def apply(self, action, payload):
        command={'action':action,'payload':payload}
        quality_contract.validate(command,'quality-action.schema.json')
        quality.reduce(self.model,command,self.rules,self.ctx)

    def refresh(self):
        self.apply('verification',self.local)
        self.apply('verification',self.material)
        self.packet['pull_requests'][0]['body']=test_coverage.summary([self.row],self.material,self.version,self.model,self.policy)

    def problems(self):
        return test_coverage.publication_problems(self.task,self.packet,self.model,self.ctx,self.rules,self.version)[0]

    def test_complete_l3_manual_path_reuses_execution_and_jira_and_preserves_automation_gap(self):
        self.assertEqual([],it_manual.closure_problems(self.model,self.ctx,self.rules))
        self.assertEqual([],self.problems())
        body=self.packet['pull_requests'][0]['body']
        for value in ('TAP-456','TAP-789','自动化待补','结果 PASS','fixture:manual-evidence'):
            self.assertIn(value,body)
        self.assertEqual('NOT_RUN',self.result['result'])
        self.packet['issues']='old GitHub field is no longer required'
        self.assertEqual([],self.problems())

    def test_supported_and_unsupported_l1_l2(self):
        for level in ('L1','L2'):
            self.row['level']=self.result['level']=self.follow['level']=level
            self.refresh(); self.assertEqual([],self.problems())
        self.row['framework_supported']=True
        self.result['case_coverage']['framework_supported']=True
        self.refresh(); self.assertTrue(self.problems())

    def test_l3_cannot_claim_framework_supported_or_automated_pass(self):
        self.row['framework_supported']=True
        self.assertTrue(verification.plan_problems(self.rules,self.ctx))
        self.row['framework_supported']=False
        self.result['result']='PASS'; self.result.update(tests=1,failures=0,errors=0,skipped=0)
        self.refresh(); self.assertTrue(self.problems())

    def test_concentrated_diagnostics_and_partial_repair(self):
        self.follow['task'].update(assignee_account_id='wrong',manual_tests=[],reason='',dedup_ref='')
        self.follow['tests'][0]['case_version']='stale'
        self.refresh(); problems=self.problems()
        for word in ('Leon','全部手动','reason','dedup_ref','版本'):
            self.assertTrue(any(word in p for p in problems),problems)
        self.follow['task']['assignee_account_id']=self.policy['assignee_account_id']
        self.refresh(); problems=self.problems()
        self.assertFalse(any('Leon' in p for p in problems)); self.assertTrue(problems)

    def test_failure_unexecuted_and_risk_acceptance_never_complete_manual_path(self):
        item=self.model['items']['manual-one']
        for raw in ('FAIL','NOT_RUN','UNKNOWN','SKIPPED'):
            item['executions'][-1]['raw_result']=raw
            self.assertTrue(self.problems(),raw)
        item['executions'][-1]['raw_result']='PASS'
        item['decision']['decision']['outcome']='accept_risk'
        self.assertTrue(self.problems())

    def test_case_definition_completeness_is_independent_of_execution(self):
        test = self.follow['tests'][0]
        original = copy.deepcopy(test['definition_refs'])
        for field in original:
            test['definition_refs'] = dict(original, **{field: ' '})
            self.refresh()
            self.assertTrue(any(field in error for error in self.problems()), field)
        test['definition_refs'] = original
        self.refresh()
        self.assertEqual([], self.problems())
        before = copy.deepcopy(test)
        item = self.model['items']['manual-one']
        execution = dict(item['executions'][-1], id='run-two', observation='再次验证通过')
        self.apply('execute', {'item_id':'manual-one', 'execution':execution})
        self.assertEqual(before, test)
        self.assertEqual(['run-one','run-two'], [e['id'] for e in item['executions']])
        self.assertTrue(self.problems())  # 新执行需要重新确认，不继承旧确认。

    def test_q2_requires_mapping_but_not_future_jira_or_execution(self):
        self.model['verification']={}
        self.model['items']['manual-one']['executions']=[]
        self.assertEqual([],it_manual.closure_problems(self.model,self.ctx,self.rules,planning=True))
        self.assertTrue(it_manual.closure_problems(self.model,self.ctx,self.rules))
        self.row['cases'][0].pop('manual_item')
        self.assertTrue(it_manual.closure_problems(self.model,self.ctx,self.rules,planning=True))

    def test_versions_case_changes_and_duplicate_snapshots_invalidate(self):
        original=copy.deepcopy(self.ctx)
        self.ctx['repositories'][self.repo]['live_revision']='b'*40
        self.assertTrue(self.problems()); self.ctx=original
        self.follow['tests'][0]['business_issues']=[]
        self.refresh(); self.assertTrue(self.problems())
        self.follow['tests'][0]['business_issues']=['TAP-123']
        self.local['it_followups'].append(copy.deepcopy(self.follow))
        self.refresh(); self.assertTrue(self.problems())

    def test_recovery_read_is_idempotent_and_missing_or_unknown_write_does_not_pass(self):
        before=copy.deepcopy(self.model)
        for _ in range(2): self.assertEqual([],self.problems())
        self.assertEqual(before,self.model)
        self.follow['task'].pop('source_ref'); self.refresh()
        self.assertTrue(self.problems())

    def test_function_and_defect_use_same_closure_policy(self):
        feature=json.loads((ROOT/'projects/tapdata/quality-feature.json').read_text())
        self.assertEqual(self.policy,it_manual.config(feature))
        self.ctx['facts']['implementation_plan']=self.ctx['facts']['fix_plan']
        self.rules=feature
        # 规则改变使旧选择/确认失效，不能继承成新规则 PASS。
        self.assertTrue(it_manual.closure_problems(self.model,self.ctx,self.rules))

    def test_supported_ci_gap_keeps_explicit_disposition_without_claiming_closure(self):
        self.row.update(level='L1',framework_supported=True)
        self.result.update(level='L1',result='NOT_RUN')
        self.result['case_coverage'].update(status='covered',framework_supported=True,
            cases=[{'id':'behavior','status':'written','case_ref':'fixture:case@sha',
                    'result':'NOT_RUN','report_ref':'fixture:ci-not-triggered'}])
        self.refresh()
        self.assertTrue(verification.problems(self.model,self.ctx,['ci'],self.rules))
        self.result['decision']={'reason':'CI 环境不可用','uncovered':self.row['scope'],'level':'L1',
            'follow_up':'恢复后补跑','proof':proof()}
        self.refresh()
        self.assertEqual([],verification.problems(self.model,self.ctx,['ci'],self.rules))
        self.assertEqual([],self.problems())
        self.assertIn('测试闭环未完成',self.packet['pull_requests'][0]['body'])
        self.assertEqual('NOT_RUN',self.result['result'])
        self.result['result']='FAIL'; self.result['case_coverage']['cases'][0]['result']='FAIL'
        self.refresh(); self.assertTrue(self.problems())
        self.result['failure_kind']='environment'
        self.refresh(); self.assertEqual([],self.problems())

    def test_q4_gate_includes_missing_followup_and_repairs_without_changing_results(self):
        self.follow['task']['assignee_account_id']='wrong'
        self.refresh()
        view=quality.checkpoint_view(self.model,'q4-acceptance',self.rules,self.ctx)
        self.assertTrue(any('Leon' in p for p in view['problems']))
        self.follow['task']['assignee_account_id']=self.policy['assignee_account_id']
        self.refresh()
        view=quality.checkpoint_view(self.model,'q4-acceptance',self.rules,self.ctx)
        self.assertFalse(any('Leon' in p for p in view['problems']))

    def test_invalid_jira_list_is_reported_not_crashed(self):
        self.follow['tests'][0]['business_issues']=None
        self.follow['task']['business_issues']=None
        self.refresh(); self.assertTrue(self.problems())
        self.follow['tests']=None
        self.refresh(); self.assertTrue(self.problems())


if __name__=='__main__': unittest.main()
