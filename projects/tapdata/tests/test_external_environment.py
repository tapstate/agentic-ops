"""外部 TM 双 FE 的配置、身份和失败保全回归；不访问真实 Docker/TM。"""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import test_test_environment as fixture

mod = fixture.mod
ROOT = fixture.ROOT


class ExternalEnvironmentTests(unittest.TestCase):
    setUp = fixture.EnvironmentTests.setUp
    save = fixture.EnvironmentTests.save
    fake_call = fixture.EnvironmentTests.fake_call
    separate_inputs = fixture.EnvironmentTests.separate_inputs

    def external(self):
        self.config["external_tm_url"] = "http://192.0.2.10:3030"
        self.config["nodes"] = {name: ["FE"] for name in ("node1", "node2")}
        self.config["ports"] = {name: {"tm": None, "api": None, "license": ""} for name in self.config["nodes"]}
        self.save("dev")

    def test_draft_and_saved_config_round_trip(self):
        result = self.env.prepare_inputs("external", "http://192.0.2.10:3030/")
        draft = mod.read_json(Path(result["config_file"]))
        self.assertEqual(draft["external_tm_url"], "http://192.0.2.10:3030")
        self.assertEqual(draft["nodes"], {"node1": ["FE"], "node2": ["FE"]})
        self.assertTrue(all(p["tm"] is None and p["api"] is None for p in draft["ports"].values()))
        self.assertEqual(self.env.prepare_inputs("external", "http://192.0.2.10:3030"), result)
        with self.assertRaisesRegex(mod.EnvironmentError, "不覆盖"):
            self.env.prepare_inputs("external", "http://192.0.2.11")
        self.external()
        runtime, preparation = self.separate_inputs("external")
        value = mod.read_json(runtime)
        value["external_tm_url"] = self.config["external_tm_url"]
        mod.write_json(runtime, value)
        self.env.configure("external", runtime, preparation)
        self.assertEqual(self.env.config("external")[1], self.config)

    def test_invalid_urls_and_mixed_components_rejected(self):
        self.external()
        for url in ("", None, "ftp://tm", "http://user:pass@tm", "http://tm/api/",
                    "http://tm?x", "http://tm?", "http://tm#x", "http://tm#",
                    "http://tm:0", "http://tm:65536", "http://tm:bad", "http://tm\n", "http://tm\\x"):
            with self.subTest(url=url), self.assertRaises(mod.EnvironmentError):
                mod.validate(dict(self.config, external_tm_url=url))
        for roles in (["TM"], ["FE", "TM"], ["FE", "APIServer"]):
            value = copy.deepcopy(self.config)
            value["nodes"]["node1"] = roles
            with self.assertRaisesRegex(mod.EnvironmentError, "FE-only"):
                mod.validate(value)
        value = dict(self.config)
        del value["fe_heartbeat"]
        with self.assertRaisesRegex(mod.EnvironmentError, "fe_heartbeat"):
            mod.validate(value)
        value = dict(self.config)
        del value["external_tm_url"]
        with self.assertRaisesRegex(mod.EnvironmentError, "至少"):
            mod.validate(value)

    def test_dual_fe_identity_update_and_uninstall_preserve_database(self):
        self.external()
        self.env.existing_collections = 50
        result = self.env.deploy("dev")  # 外部环境无需 reuse-mongo，也不提示清库。
        self.assertTrue(result["ready"])
        original = self.env.state()
        ids = [node["uuid"] for node in original["nodes"]]
        self.assertEqual(len(set(ids)), 2)
        compose = mod.read_json(self.env.root / "compose.json")
        for name, service in compose["services"].items():
            self.assertNotIn("depends_on", service)
            self.assertEqual(service["ports"], [])
            settings = mod.read_json(self.env.root / "nodes" / name / "settings.json")
            self.assertEqual(settings["backend_url"], "http://192.0.2.10:3030/api/")
            self.assertEqual(settings["fe_process_id"], settings["uuid"])
            self.assertEqual(settings["components"], ["FE"])
        self.env.artifact = "new"
        self.env.deploy(update=True)
        self.assertEqual([n["uuid"] for n in self.env.state()["nodes"]], ids)
        self.assertEqual(self.env.state()["artifact_digest"], "new")
        secret = self.station / "config/shared/secrets/mongodb-uri"
        content = secret.read_bytes()
        self.env.remove()
        self.assertEqual(secret.read_bytes(), content)
        self.assertTrue((self.station / "runtime/tapdata-test-env-diagnostics").is_dir())
        self.assertFalse(self.env.root.exists())

    def test_failure_before_replacement_and_no_reuse_flag(self):
        self.external()
        with self.assertRaisesRegex(mod.EnvironmentError, "reuse-mongo"):
            self.env.deploy("dev", reuse_mongo=True)
        self.assertEqual(self.env.history, [])
        self.env.deploy("dev")
        before = (self.env.root / "active.json").read_bytes()
        self.env.build_failure = True
        with self.assertRaises(mod.EnvironmentError):
            self.env.deploy(update=True)
        self.assertNotIn("down", self.env.history)
        self.assertEqual((self.env.root / "active.json").read_bytes(), before)

    def test_legacy_preparation_still_accepted(self):
        self.config.pop("fe_heartbeat")
        mod.validate(self.config)
        runtime, preparation = self.separate_inputs("legacy")
        self.env.configure("legacy", runtime, preparation)
        self.assertNotIn("fe_heartbeat", self.env.config("legacy")[1])

    def test_probe_errors_only_expose_reviewed_messages(self):
        completed = subprocess.CompletedProcess(["probe"], 1, '{"probe_error":"mongo_auth"}', "private-password")
        with mock.patch.object(mod.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(mod.EnvironmentError, "^认证失败$"):
                fixture.REAL_CALL(["probe"], failure_messages={"mongo_auth": "认证失败"})
            completed.stdout = '{"probe_error":"private-password"}'
            with self.assertRaises(mod.EnvironmentError) as error:
                fixture.REAL_CALL(["probe"], failure_messages={"mongo_auth": "认证失败"})
            self.assertNotIn("private-password", str(error.exception))


@unittest.skipUnless(shutil.which("node"), "需要 Node 执行现役探测脚本")
class ExternalProbeTests(unittest.TestCase):
    def test_effective_yaml_passes_identity_and_api_url_to_launcher(self):
        script = ROOT / "projects/tapdata/scripts/test-environment-node.js"
        harness = r"""
const fs=require('fs'),vm=require('vm');const writes=[];
const settings={backend_url:'http://192.0.2.10:3030/api/',engine_opts:'-Xmx2G',tm_opts:'-Xmx1G',
uuid:'identity-one',fe_process_id:'identity-one'};
const mocks={fs:{readFileSync:p=>p==='/secret/mongo-uri'?'mongodb://private/db':JSON.stringify(settings),
mkdirSync:()=>{},writeFileSync:(p,text,options)=>writes.push({p,text,mode:options.mode})},
path:require('path'),child_process:{},net:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{require:n=>mocks[n],process:{argv:['','','configure']}});
console.log(JSON.stringify(writes));
"""
        result = subprocess.run(["node", "-e", harness, str(script)], capture_output=True, text=True, check=True)
        files = json.loads(result.stdout)
        for file in (f for f in files if f["p"].endswith("application.yml")):
            self.assertIn('process_id: "identity-one"', file["text"])
            self.assertIn('baseURLs: "http://192.0.2.10:3030/api/"', file["text"])
            self.assertIn('backendUrl: "http://192.0.2.10:3030/api/"', file["text"])
            self.assertEqual(file["mode"], 0o600)

    def probe(self, failure=""):
        script = ROOT / "projects/tapdata/scripts/test-environment-probe.js"
        harness = r"""
const fs=require('fs'),vm=require('vm'),events=require('events');
const mode=process.argv[1];let output='',waits=0,queries=[];
const input=new events.EventEmitter();input.setEncoding=()=>{};
const proc={argv:[],stdin:input,exitCode:0};
const projectionChecks=[];
const MongoClient=class {
 async connect(){if(mode==='auth')throw Error('private-uri');}
 async close(){}
 db(){return {command:async()=>({hosts:[]}),listCollections:()=>{throw Error('must not list external database')},
 collection:name=>({find:(q,options)=>{
 queries.push([name,q]); projectionChecks.push(options);
 const engine=name==='Workers',missing=mode==='missing'&&engine;
 let row=engine?{ping_time:Date.now()-1000+(waits?500:0)}:{systemInfo:{time:Date.now()-1000+(waits?500:0)}};
 if(mode==='stalled')row=engine?{ping_time:1900000000000}:{systemInfo:{time:1900000000000}};
 if(mode==='deleted'&&engine)row.isDeleted=true;
 if(mode==='stopping'&&engine)row.stopping=true;
 return {limit:()=>({toArray:async()=>missing?[]:mode==='duplicate'?[row,row]:[row]})};
 }})};}
};
const http={get:(url,options,callback)=>{const req=new events.EventEmitter();req.destroy=()=>{};
 queueMicrotask(()=>callback({statusCode:mode==='http'?503:200,resume:()=>{}}));return req;}};
const mocks={'/driver/mongodb':{MongoClient},net:{},http,https:http};
const fixedDate=class extends Date {static now(){return 1900000001000+waits*21000;}};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{
 require:n=>mocks[n],process:proc,console:{log:s=>output=s},Date:fixedDate,Number,
 setTimeout:fn=>{waits++;fn();}
});
input.emit('data',JSON.stringify({
 uri:'mongodb://private',external_tm_url:'http://tm:3030',nodes:[{name:'node1',uuid:'one'},{name:'node2',uuid:'two'}],
 heartbeat:{collection:'ClusterState',uuid_field:'uuid',time_field:'systemInfo.time',max_age_seconds:60},
 fe_heartbeat:{collection:'Workers',uuid_field:'process_id',time_field:'ping_time',type_field:'worker_type',
 type_value:'connector',deleted_field:'isDeleted',stopped_field:'stopping'}
}));input.emit('end');
setTimeout(()=>console.log(JSON.stringify({output,code:proc.exitCode,queries,waits,projectionChecks})),40);
"""
        # mock 记录的时间也使用同一时钟域。
        harness = harness.replace("Date.now()-1000+(waits?500:0)", "1900000000000+(waits?21000:0)")
        result = subprocess.run(["node", "-e", harness, failure, str(script)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_distinct_nodes_and_layers_require_advancing_samples(self):
        value = self.probe()
        report = json.loads(value["output"])
        self.assertTrue(report["verified"])
        self.assertEqual(value["waits"], 1)
        self.assertTrue(all(n["engine"]["heartbeat_advancing"] and n["launcher"]["heartbeat_advancing"] for n in report["nodes"]))
        self.assertIn(["Workers", {"process_id": "one", "worker_type": "connector"}], value["queries"])
        self.assertIn(["Workers", {"process_id": "two", "worker_type": "connector"}], value["queries"])
        self.assertTrue(all("projection" in options for options in value["projectionChecks"]))

    def test_missing_duplicate_deleted_stopping_and_stalled_fail(self):
        for failure in ("missing", "duplicate", "deleted", "stopping", "stalled"):
            with self.subTest(failure=failure):
                self.assertFalse(json.loads(self.probe(failure)["output"])["verified"])

    def test_unreachable_tm_and_auth_error_never_expose_secret(self):
        for failure in ("http", "auth"):
            value = self.probe(failure)
            self.assertEqual(value["code"], 1)
            self.assertNotIn("private", value["output"])


if __name__ == "__main__":
    unittest.main()
