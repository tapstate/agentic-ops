"""任务级协调：真实本地状态、原生回读夹具及双工位冲突，不模拟判定成功。"""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest import mock
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_station_lifecycle as fixture
from workflow import jira_coordination as c, task_store, execution, station

A,B='a'*32,'b'*32

class CoordinationTests(unittest.TestCase):
    write=fixture.StationTests.write
    git=fixture.StationTests.git
    prepare_engineering=fixture.StationTests.prepare_engineering
    def setUp(self):
        fixture.StationTests.setUp(self)
        self.env=mock.patch.dict(os.environ, {execution.SESSION_ENV:A});self.env.start();self.addCleanup(self.env.stop)
        self.other=self.root/'other';shutil.copytree(self.ws,self.other)
        binding=json.loads((self.other/'.agenticops/station.json').read_text());binding['station_id']=B
        self.write(self.other/'.agenticops/station.json',binding)
        self.comments=[]
    def snapshot(self):
        return {'site':'https://tapdata.atlassian.net','issue_id':'42687','issue_key':'TAP-13146','source_ref':'https://tapdata.atlassian.net/browse/TAP-13146',
                'pages':[{'startAt':0,'total':len(self.comments),'isLast':True,'comments':copy.deepcopy(self.comments)}]}
    def prepare(self,base=None,operation='claim-one',**kw):
        base=base or self.ws;s=self.snapshot();v=c.snapshot(base,s)
        return c.run(base,'prepare',s,operation,expected_digest=v['digest'],executor='测试执行者',**kw)
    def publish(self,result,base=None):
        base=base or self.ws
        cid=result['comment_id'] or str(len(self.comments)+1)
        row={'id':cid,'body':result['body'],'updated':datetime.now(timezone.utc).isoformat()}
        self.comments=[r for r in self.comments if r['id']!=cid]+[row]
        return c.run(base,'complete',self.snapshot(),claim_id=result['claim_id'])
    def test_empty_prepare_readback_and_normal_continue(self):
        with self.assertRaisesRegex(ValueError,'尚未核实'):c.require(self.ws,'TAP-13146')
        p=self.prepare();self.assertTrue(p['pending'])
        with self.assertRaisesRegex(ValueError,'尚未核实'):c.require(self.ws,'TAP-13146')
        self.assertTrue(self.publish(p)['verified']);c.require(self.ws,'TAP-13146')
        before=task_store.current_path(self.ws).read_bytes();c.run(self.ws,'status');self.assertEqual(before,task_store.current_path(self.ws).read_bytes())
    def test_active_transfer_requires_decision_and_old_report_is_fenced(self):
        p=self.prepare();self.publish(p)
        os.environ[execution.SESSION_ENV]=B
        with self.assertRaisesRegex(ValueError,'decision-ref'):self.prepare(self.other,'claim-two',conflict_check_ref='old stopped')
        second=self.prepare(self.other,'claim-two',conflict_check_ref='old stopped',decision_ref='用户决定')
        self.assertTrue(self.publish(second,self.other)['verified'])
        os.environ[execution.SESSION_ENV]=A
        self.assertFalse(c.run(self.ws,'check',self.snapshot())['verified'])
        with self.assertRaisesRegex(ValueError,'尚未核实'):c.require(self.ws,'TAP-13146')
        with self.assertRaisesRegex(ValueError,'已被接替'):self.prepare(operation='report-old',mode='report',claim_id=p['claim_id'])
    def test_eight_hours_uses_remote_report_not_local_activity(self):
        p=self.prepare();self.publish(p);now=int(time.time());self.comments[0]['updated']=datetime.fromtimestamp(now-8*3600,timezone.utc).isoformat()
        os.environ[execution.SESSION_ENV]=B
        with mock.patch.object(c.time,'time',return_value=now):
            self.assertTrue(c.run(self.other,'check',self.snapshot())['holders'][0]['idle'])
            with self.assertRaisesRegex(ValueError,'conflict-check-ref'):self.prepare(self.other,'claim-two')
            second=self.prepare(self.other,'claim-two',conflict_check_ref='已核实停写')
        self.publish(second,self.other)
    def test_concurrent_claims_are_visible_not_last_writer_wins(self):
        p=self.prepare()
        os.environ[execution.SESSION_ENV]=B;q=self.prepare(self.other,'claim-two');self.publish(q,self.other)
        os.environ[execution.SESSION_ENV]=A
        self.assertFalse(self.publish(p)['verified'])
        self.assertEqual(2,len(c.run(self.ws,'check',self.snapshot())['holders']))
        with self.assertRaisesRegex(ValueError,'decision-ref'):self.prepare(operation='resolve',conflict_check_ref='停写')
    def test_lost_receipt_reuses_body_and_does_not_duplicate_intent(self):
        p=self.prepare();self.assertEqual(p['body'],self.prepare()['body'])
        self.comments.append({'id':'1','body':p['body'],'updated':datetime.now(timezone.utc).isoformat()})
        self.assertTrue(c.run(self.ws,'complete',self.snapshot(),claim_id=p['claim_id'])['verified'])
        self.assertEqual(1,len(c.pending(self.ws)))
    def test_report_edits_own_comment_and_end_survives_local_release(self):
        p=self.prepare();self.publish(p)
        update=self.prepare(operation='report-one',mode='report',claim_id=p['claim_id']);self.assertEqual('1',update['comment_id']);self.publish(update)
        with task_store.task_state_lock(self.ws):pass
        with task_store.task_state_lock(self.ws,execution=False):execution.release(self.ws,A)
        end=self.prepare(operation='end-one',mode='end',claim_id=p['claim_id']);self.publish(end)
        self.assertEqual([],c.pending(self.ws));self.assertFalse(c.run(self.ws,'check',self.snapshot())['verified'])
    def test_old_station_end_cannot_release_new_station(self):
        p=self.prepare();self.publish(p);os.environ[execution.SESSION_ENV]=B
        q=self.prepare(self.other,'claim-two',conflict_check_ref='停写',decision_ref='交接');self.publish(q,self.other)
        os.environ[execution.SESSION_ENV]=A
        end=self.prepare(operation='end-old',mode='end',claim_id=p['claim_id']);self.publish(end)
        os.environ[execution.SESSION_ENV]=B
        self.assertTrue(c.run(self.other,'check',self.snapshot())['verified']);self.assertTrue(c.pending(self.other)[0]['active'])
    def test_local_successor_can_end_but_not_report_old_record(self):
        p=self.prepare();self.publish(p);os.environ[execution.SESSION_ENV]=B
        with self.assertRaisesRegex(ValueError,'旧会话'):self.prepare(operation='end-old',mode='end',claim_id=p['claim_id'])
        end=self.prepare(operation='end-old',mode='end',claim_id=p['claim_id'],conflict_check_ref='停写',decision_ref='清理授权');self.publish(end)
        self.assertEqual([],c.pending(self.ws))
    def test_invalid_input_invalidates_previous_proof(self):
        p=self.prepare();self.publish(p);s=self.snapshot();s['pages'][0]['total']+=1
        with self.assertRaisesRegex(ValueError,'分页'):c.run(self.ws,'check',s)
        with self.assertRaisesRegex(ValueError,'尚未核实'):c.require(self.ws,'TAP-13146')
    def test_pagination_and_unrelated_comments(self):
        p=self.prepare();self.publish(p)
        self.comments.append({'id':'2','body':'普通评论','updated':datetime.now(timezone.utc).isoformat()})
        s=self.snapshot();s['pages']=[{'startAt':0,'total':2,'isLast':False,'comments':[self.comments[0]]},{'startAt':1,'total':2,'isLast':True,'comments':[self.comments[1]]}]
        self.assertTrue(c.run(self.ws,'check',s)['verified'])
        s['pages']=s['pages'][1:]
        with self.assertRaisesRegex(ValueError,'分页'):c.run(self.ws,'check',s)
    def test_cancel_unsent_claim_and_report_and_repeat_end_readback(self):
        p=self.prepare()
        c.run(self.ws,'cancel',self.snapshot(),claim_id=p['claim_id'],decision_ref='用户确认从未发送')
        self.assertEqual([],c.pending(self.ws))
        p=self.prepare(operation='claim-new');self.publish(p)
        r=self.prepare(operation='report',mode='report',claim_id=p['claim_id'])
        c.run(self.ws,'cancel',self.snapshot(),claim_id=p['claim_id'],decision_ref='尚未发送')
        self.assertTrue(c.run(self.ws,'check',self.snapshot())['verified'])
        e=self.prepare(operation='end',mode='end',claim_id=p['claim_id']);self.publish(e)
        self.assertTrue(c.run(self.ws,'complete',self.snapshot(),claim_id=p['claim_id'])['saved'])

    def test_takeover_gate_and_cleanup_after_remote_handoff(self):
        from workflow import station_cleanup
        self.prepare_engineering();self.request['issue_key']='TAP-13146'
        c.save(self.ws,{'entries':{},'proof':None})
        with self.assertRaisesRegex(ValueError,'尚未核实'):
            station.takeover(self.ws,self.request,'op-coordination-takeover',0)
        p=self.prepare();self.publish(p)
        station.takeover(self.ws,self.request,'op-coordination-takeover',0)
        task=task_store.read_task(self.ws)
        os.environ[execution.SESSION_ENV]=B
        q=self.prepare(self.other,'claim-two',conflict_check_ref='已停止 A 写入',decision_ref='用户交接');self.publish(q,self.other)
        os.environ[execution.SESSION_ENV]=A
        c.run(self.ws,'check',self.snapshot())
        with self.assertRaisesRegex(ValueError,'尚未核实'):
            with task_store.task_state_lock(self.ws):pass
        plan=station_cleanup.plan(self.ws,'task')
        result=station_cleanup.execute(self.ws,task['issue_key'],task['run_id'],task['_revision'],'op-clean-after-handoff',
            {'scope':'task','materials':'preserved','expected_plan_digest':plan['digest'],'decision_ref':'用户清理旧工位'})
        self.assertTrue(result['cleanup_result']['complete'])
        self.assertTrue(result['cleanup_result']['coordination']['records'])
        self.assertIsNone(task_store.read_task(self.ws))
        e=self.prepare(operation='end-after-clean',mode='end',claim_id=p['claim_id']);self.publish(e)
        os.environ[execution.SESSION_ENV]=B
        self.assertTrue(c.run(self.other,'check',self.snapshot())['verified'])

    def test_native_jira_timezone_without_colon(self):
        native=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000+0000')
        self.assertLess(abs(time.time()-c.timestamp(native)),2)

    def test_wrong_site_id_malformed_and_future_timestamp_rejected(self):
        p=self.prepare();self.publish(p)
        for field,value in [('site','https://other.atlassian.net'),('issue_id','999')]:
            s=self.snapshot();s[field]=value
            with self.assertRaises(ValueError):c.run(self.ws,'check',s)
        self.comments[0]['updated']='2100-01-01T00:00:00Z'
        with self.assertRaises(ValueError):c.run(self.ws,'check',self.snapshot())
    def test_observation_change_rejects_stale_decision(self):
        p=self.prepare();self.publish(p);observed=c.snapshot(self.ws,self.snapshot())['digest'];self.comments[0]['updated']=datetime.now(timezone.utc).isoformat()
        os.environ[execution.SESSION_ENV]=B
        with self.assertRaisesRegex(ValueError,'观察已变化'):c.run(self.other,'prepare',self.snapshot(),'claim-two',executor='B',expected_digest=observed,decision_ref='旧决定',conflict_check_ref='停写')

if __name__=='__main__':unittest.main()
