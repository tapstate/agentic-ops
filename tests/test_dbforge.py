"""下载式 CLI 的凭据、恢复与 IT 行为回归；不访问真实服务。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("ao_dbforge", ROOT / "skills/shared/ao-dbforge/scripts/dbforge.py")
dbforge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dbforge)


class DBForgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config"
        dbforge.private_write(self.config, "url = http://127.0.0.1:30880\ntoken = tenant-secret\nadmin_key = admin-secret\n")
        self.settings = dbforge.configuration(self.config)
        self.record = self.root / "runtime/lease.json"

    def test_template_is_private_and_existing_configuration_is_unchanged(self):
        before = self.config.read_bytes()
        self.assertEqual("tenant-secret", self.settings["token"])
        self.assertNotIn("admin_key", self.settings)
        self.assertEqual(before, self.config.read_bytes())
        missing = self.root / "first-use"
        result = dbforge.configuration(missing)
        self.assertEqual(dbforge.DEFAULT_URL, result["url"])
        self.assertFalse(result["token"])
        self.assertEqual(0o600, missing.stat().st_mode & 0o777)
        missing.chmod(0o644)
        with self.assertRaises(dbforge.Error):
            dbforge.configuration(missing)
        self.assertIn("token =", missing.read_text())

    def test_native_child_cannot_inherit_admin_key_or_override_config(self):
        observed = {}
        def call(args, **kwargs):
            path = Path(args[args.index("-config") + 1])
            observed["path"] = path
            observed["config"] = path.read_text()
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertFalse(any(key.startswith("DBFORGE_CLI_") for key in kwargs["env"]))
            return subprocess.CompletedProcess(args, 0, '{"database_types": []}', "")
        with mock.patch.dict(os.environ, {"DBFORGE_CLI_ADMIN_KEY": "inherited-admin", "DBFORGE_CLI_TOKEN": "other-tenant"}), \
                mock.patch.object(dbforge.subprocess, "run", side_effect=call):
            dbforge.native("fake", self.settings, ["capabilities"])
        self.assertIn("tenant-secret", observed["config"])
        self.assertNotIn("admin", observed["config"])
        self.assertFalse(observed["path"].exists())
        for flag in ("-admin-key=x", "--config=x", "-token", "-url", "-o"):
            with self.subTest(flag=flag), self.assertRaises(dbforge.Error):
                dbforge.invoke("fake", self.settings, ["list", flag])

    def test_create_timeout_recovers_same_lease_and_preserves_unknown_request(self):
        response = {"lease_id": "lease-one", "status": "active", "connection": {"password": "db-secret", "uri": "mongodb://secret"}}
        with mock.patch.object(dbforge, "native", side_effect=dbforge.Error("结果未知")):
            with self.assertRaises(dbforge.Error):
                dbforge.invoke("fake", self.settings, ["create", "-db-type", "mysql"], self.record)
        first = json.loads(self.record.read_text())
        self.assertEqual("pending", first["status"])
        self.assertEqual(0o600, self.record.stat().st_mode & 0o777)
        with mock.patch.object(dbforge, "native", side_effect=[json.dumps({"leases": [{"lease_id": "lease-one", "status": "active"}]}), json.dumps(response)]) as call:
            result = dbforge.invoke("fake", self.settings, ["create", "-db-type", "mysql"], self.record)
        self.assertEqual("list", call.call_args_list[0].args[2][0])
        self.assertEqual(first["idempotency_key"], call.call_args_list[0].args[2][-1])
        self.assertNotIn("db-secret", json.dumps(result))
        self.assertIn("db-secret", self.record.read_text())
        with mock.patch.object(dbforge, "native", return_value='{"leases": []}'):
            with self.assertRaises(dbforge.Error):
                dbforge.invoke("fake", self.settings, ["create", "-db-type", "mysql"], self.record)

    def test_release_pending_and_service_drift_remain_recoverable(self):
        dbforge.private_write(self.record, {"url": self.settings["url"], "config": self.settings["config"], "lease_id": "lease-one"})
        with mock.patch.object(dbforge, "native", return_value='{"lease_id":"lease-one","status":"reclaim_pending","reclaim_pending":true}') as call:
            value = dbforge.invoke("fake", self.settings, ["destroy"], self.record)
        self.assertEqual("reclaim_pending", value["status"])
        self.assertEqual(["destroy", "-o", "json", "-yes", "lease-one"], call.call_args.args[2])
        with mock.patch.object(dbforge, "native") as call, self.assertRaises(dbforge.Error):
            dbforge.invoke("fake", dict(self.settings, url="http://other:30880"), ["destroy"], self.record)
        call.assert_not_called()

    def test_concurrent_first_create_cannot_replace_original_idempotency_key(self):
        write = dbforge.private_write
        entered = False
        def before_first_write(path, value):
            nonlocal entered
            if value.get("status") == "pending" and not entered:
                entered = True
                # 第一调用已检查记录不存在，但尚未落盘；第二调用必须在读取前被拒绝。
                with self.assertRaisesRegex(dbforge.Error, "同一租约记录正在使用"):
                    dbforge.invoke("fake", self.settings, ["create", "-db-type", "mysql"], self.record)
            write(path, value)
        response = '{"lease_id":"only-lease","status":"active"}'
        with mock.patch.object(dbforge, "private_write", side_effect=before_first_write), \
                mock.patch.object(dbforge, "native", return_value=response) as native:
            result = dbforge.invoke("fake", self.settings, ["create", "-db-type", "mysql"], self.record)
        native.assert_called_once()
        record = json.loads(self.record.read_text())
        self.assertEqual("only-lease", result["lease_id"])
        self.assertEqual(record["idempotency_key"], native.call_args.args[2][4])
        # 锁随调用结束释放，原记录之后仍可正常查询。
        with mock.patch.object(dbforge, "native", return_value=response):
            self.assertEqual("only-lease", dbforge.invoke("fake", self.settings, ["describe"], self.record)["lease_id"])

    def test_failed_create_can_discover_original_lease_for_cleanup(self):
        dbforge.private_write(self.record, {"url": self.settings["url"], "config": self.settings["config"],
                              "idempotency_key": "original-request", "status": "pending"})
        responses = ['{"leases":[{"lease_id":"original-lease","status":"failed"}]}',
                     '{"lease_id":"original-lease","status":"released"}']
        with mock.patch.object(dbforge, "native", side_effect=responses) as call:
            result = dbforge.invoke("fake", self.settings, ["destroy"], self.record)
        self.assertEqual(["list", "-o", "json", "-idem-key", "original-request"], call.call_args_list[0].args[2])
        self.assertEqual("original-lease", call.call_args_list[1].args[2][-1])
        self.assertEqual("released", result["status"])
        with mock.patch.object(dbforge, "native") as call, self.assertRaises(dbforge.Error):
            dbforge.invoke("fake", self.settings, ["destroy", "another-lease"], self.record)
        call.assert_not_called()

    def test_it_selection_is_local_to_child_and_failure_never_retries(self):
        before = self.config.read_bytes()
        script = self.root / "consumer.py"
        script.write_text("import os,sys,json\nprint(json.dumps({k:v for k,v in os.environ.items() if 'DBFORGE' in k or k=='CONNECTOR_IT_CONFIG_SOURCE'}))\nsys.exit(7)\n")
        log = self.root / "runtime/it.log"
        result = dbforge.run_it(self.settings, "dbforge", [sys.executable, str(script)], self.root, log)
        observed = json.loads(log.read_text())
        self.assertEqual(7, result["exit_code"])
        self.assertEqual("dbforge", observed["CONNECTOR_IT_CONFIG_SOURCE"])
        self.assertEqual("tenant-secret", observed["CONNECTOR_IT_DBFORGE_TOKEN"])
        self.assertFalse(any("ADMIN" in key for key in observed))
        self.assertEqual(0o600, log.stat().st_mode & 0o777)
        self.assertEqual(before, self.config.read_bytes())
        self.assertNotIn("tenant-secret", json.dumps(result))
        with self.assertRaises(dbforge.Error):
            dbforge.run_it(self.settings, "dbforge", ["mvn", "-Dconnector.it.config.source=json"], self.root, log)
        with mock.patch.dict(os.environ, {"MAVEN_OPTS": "-Dconnector.it.config.source=dbforge"}), self.assertRaises(dbforge.Error):
            dbforge.run_it(self.settings, "json", ["mvn", "verify"], self.root, log)

    def test_json_it_does_not_require_dbforge_configuration_or_installation(self):
        missing = self.root / "not-created"
        args = ["dbforge", "--config", str(missing), "it", "--cwd", str(self.root), "--log", str(self.root / "it.log"),
                "--", sys.executable, "-c", "print('ordinary tests')"]
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, dbforge.main())
        self.assertFalse(missing.exists())

    def test_maven_split_and_long_properties_cannot_override_it_selection(self):
        log = self.root / "it.log"
        overrides = [
            ["-D", "connector.it.config.source=json"],
            ["--define", "connector.it.config.source=json"],
            ["--define=connector.it.config.source=json"],
            ["-D", "connector.it.dbforge.token=other-token"],
        ]
        with mock.patch.object(dbforge.subprocess, "run") as run:
            for flags in overrides:
                with self.subTest(flags=flags), self.assertRaises(dbforge.Error):
                    dbforge.run_it(self.settings, "dbforge", ["mvn", *flags, "verify"], self.root, log)
            for value in ('-D "connector.it.config.source=dbforge"', '--define=connector.it.dbforge.url=http://other'):
                with mock.patch.dict(os.environ, {"MAVEN_ARGS": value}), self.assertRaises(dbforge.Error):
                    dbforge.run_it(self.settings, "json", ["mvn", "verify"], self.root, log)
        run.assert_not_called()
        self.assertFalse(log.exists())
        self.assertFalse(dbforge.overrides_it_configuration(["mvn", "-D", "skipTests=false"]))

    def test_failed_native_update_restores_previous_executable(self):
        binary = self.root / "dbforge-cli"
        binary.write_text("old")
        def failed(*args, **kwargs):
            binary.write_text("broken update")
            raise dbforge.Error("更新失败")
        with mock.patch.object(dbforge, "native", side_effect=failed), mock.patch.object(dbforge, "version", return_value="dbforge-cli old"):
            value = dbforge.install(self.root, self.settings, update=True)
        self.assertEqual("update_failed", value["status"])
        self.assertEqual("old", binary.read_text())
        self.assertEqual("not_installed", dbforge.install(self.root / "absent", self.settings, update=True, if_installed=True)["status"])
        self.assertFalse((self.root / "absent").exists())

    def test_first_install_verifies_real_signature_before_executing_artifact(self):
        openssl = next((path for path in (shutil.which("openssl"), "/opt/homebrew/opt/openssl@3/bin/openssl",
            "/usr/local/opt/openssl@3/bin/openssl") if path and Path(path).is_file() and "OpenSSL 3." in
            subprocess.run([path, "version"], capture_output=True, text=True).stdout), None)
        if not openssl:
            self.skipTest("首次安装验签的真实工具回归需要 OpenSSL 3")
        key, pub, artifact, signature = (self.root / name for name in ("key", "pub", "artifact", "sig"))
        subprocess.run([openssl, "genpkey", "-algorithm", "ED25519", "-out", str(key)], check=True, capture_output=True)
        subprocess.run([openssl, "pkey", "-in", str(key), "-pubout", "-outform", "DER", "-out", str(pub)], check=True, capture_output=True)
        artifact.write_text(f"#!{sys.executable}\nprint('dbforge-cli signed-fixture')\n")
        subprocess.run([openssl, "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(artifact),
                        "-out", str(signature)], check=True, capture_output=True)
        def download(url, **kwargs):
            return io.BytesIO(signature.read_bytes().hex().encode() if url.endswith("/signature") else artifact.read_bytes())
        with mock.patch.object(dbforge, "PUBLIC_KEY", pub.read_bytes()[-32:].hex()), \
                mock.patch.object(dbforge.urllib.request, "urlopen", side_effect=download):
            result = dbforge.install(self.root / "installed", self.settings)
            self.assertEqual("installed", result["status"])
            self.assertEqual("dbforge-cli signed-fixture", result["version"])
            artifact.write_text(artifact.read_text() + "# changed after signing\n")
            with self.assertRaises(dbforge.Error):
                dbforge.install(self.root / "rejected", self.settings)
        self.assertFalse((self.root / "rejected/dbforge-cli").exists())

    def test_project_tool_preparation_is_explicit_and_optional(self):
        from workflow import station_takeover
        from projects.tapdata.scripts import engineering_baseline as project
        module = mock.Mock()
        module.preflight.return_value = {"baseline": "verified"}
        with mock.patch.object(station_takeover, "inputs", return_value=(module, {}, {}, {})):
            station_takeover.preflight("station", {"version": "develop"})
            module.prepare_tools.assert_not_called()
            station_takeover.prepare_tools("station", {"version": "develop"})
            module.prepare_tools.assert_called_once_with("station")
        with mock.patch.object(station_takeover, "inputs", return_value=(object(), {}, {}, {})):
            station_takeover.prepare_tools("station", {"version": "develop"})
        with mock.patch.object(project.Path, "is_file", return_value=True), \
                mock.patch.object(project.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"status":"updated"}', '')) as run:
            project.prepare_tools("station")
            run.assert_called_once()
            self.assertIn("--if-installed", run.call_args.args[0])
        with mock.patch.object(project.Path, "is_file", return_value=False), \
                mock.patch.object(project.subprocess, "run") as run:
            project.prepare_tools("station")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
