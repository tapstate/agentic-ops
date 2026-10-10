import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "projects/tapdata/scripts/test_environment.py"
spec = importlib.util.spec_from_file_location("test_environment", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
REAL_CALL = mod.call


class BuildHelperTests(unittest.TestCase):
    def test_maven_forwards_verified_repository_java_and_subcommand(self):
        helper_spec = importlib.util.spec_from_file_location("env_maven", ROOT / "projects/tapdata/scripts/test-environment-maven.py")
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        with mock.patch.object(helper.subprocess, "call", return_value=7) as invoke:
            self.assertEqual(helper.main(["--maven", "/tools/mvn", "--java-home", "/tools/jdk", "--local-repository", "/station/runtime/maven", "--", "install", "-Penterprise,idaas"]), 7)
        argv = invoke.call_args.args[0]
        self.assertEqual(argv, ["/tools/mvn", "-Dmaven.repo.local=/station/runtime/maven", "install", "-Penterprise,idaas"])
        self.assertEqual(invoke.call_args.kwargs["env"]["JAVA_HOME"], "/tools/jdk")

    def test_maven_rejects_repository_override_before_execution(self):
        helper_spec = importlib.util.spec_from_file_location("env_maven", ROOT / "projects/tapdata/scripts/test-environment-maven.py")
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        with mock.patch.object(helper.subprocess, "call") as invoke, mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                helper.main(["--maven", "/tools/mvn", "--java-home", "/tools/jdk", "--local-repository", "/station/runtime/maven", "--", "install", "-Dmaven.repo.local=/shared"])
        invoke.assert_not_called()


class FakeEnvironment(mod.Environment):
    """仅替换外部构建及 Docker 执行，真实生成配置和生命周期文件。"""
    def __init__(self, station):
        super().__init__(station)
        self.containers = []
        self.history = []
        self.build_failure = False
        self.monitor_ready = True
        self.artifact = "old"
        self.existing_collections = 0

    def docker_resources(self):
        return self.containers

    def compose(self, *args, timeout=300):
        self.history.append(args[0])
        if args[0] == "up":
            state = self.state()
            self.containers = [{"Id": node["name"], "Config": {"Labels": {mod.LABEL: self.identity, "com.docker.compose.service": node["name"]}}, "State": {"Running": True, "Health": {"Status": "healthy"}}} for node in state["nodes"]]
        if args[0] == "down":
            self.containers = []
        return ""

    def build(self, config):
        self.history.append("build")
        if self.build_failure:
            raise mod.EnvironmentError("构建失败")
        candidate = self.root / "builds" / ("b" * 32 if self.artifact == "old" else "c" * 32)
        (candidate / "bundle").mkdir(parents=True, exist_ok=True)
        return candidate, {"artifact_digest": self.artifact, "mongo_preflight": {"database": "test", "collection_count": self.existing_collections}}

    def probe(self, config, bundle, nodes):
        return {"verified": self.monitor_ready, "nodes": [{"node": node["name"], "registered": True, "heartbeat_fresh": self.monitor_ready} for node in nodes]}


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.station = Path(self.tmp.name).resolve() / "station"
        (self.station / ".agenticops").mkdir(parents=True)
        mod.write_json(self.station / ".agenticops/station.json", {"project": "tapdata", "station_id": "a" * 32})
        self.runtime_template = mod.read_json(ROOT / "projects/tapdata/templates/station/test-env.json")
        self.preparation_template = mod.read_json(ROOT / "projects/tapdata/templates/station/test-env-preparation.json")
        self.config = {**self.runtime_template, **self.preparation_template}
        self.config["mongo_uri_file"] = "mongodb-uri"
        self.config["platform"] = "linux/arm64"
        self.config["nodes"]["node1"] = ["TM", "FE", "APIServer"]
        self.config["build"] = [{"cwd": "tapdata/tapdata", "argv": ["bash", "build.sh"]}]
        self.config["assets"] = [{"source": "tapdata/tapdata/output", "target": "."}]
        # 不占用本机固定测试端口。
        self.config["ports"]["node1"] = {"tm": 53131, "api": 53132, "license": ""}
        secret = self.station / "config/secrets/mongodb-uri"
        secret.parent.mkdir(parents=True)
        secret.write_text("mongodb://tester:do-not-print@host.docker.internal:27017/test?replicaSet=rs0")
        secret.chmod(0o600)
        self.env = FakeEnvironment(self.station)
        self.save("dev")
        self.call_patch = mock.patch.object(mod, "call", side_effect=self.fake_call)
        self.call_patch.start()
        self.socket_patch = mock.patch.object(mod.socket, "socket")
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)
        self.addCleanup(self.call_patch.stop)

    def fake_call(self, argv, **kwargs):
        if argv[:2] == ["docker", "exec"]:
            roles = self.env.state()["config"]["nodes"][argv[2]]
            return json.dumps({"time": mod.time.time() * 1000, "components": {"launcher": True, "tm": "TM" in roles, "engine": "FE" in roles, "api": "APIServer" in roles, "tm_port": "TM" in roles, "api_port": "APIServer" in roles}})
        return ""

    def save(self, name, config=None):
        mod.write_json(self.env.config_dir / (name + ".json"), config or self.config)

    def separate_inputs(self):
        runtime = {key: value for key, value in self.config.items() if key in mod.RUNTIME_FIELDS}
        runtime["schema_version"] = 2
        preparation = {key: value for key, value in self.config.items() if key in mod.PREPARATION_FIELDS}
        preparation["schema_version"] = 1
        runtime_path = self.station / "runtime-input.json"
        preparation_path = self.station / "preparation-input.json"
        mod.write_json(runtime_path, runtime)
        mod.write_json(preparation_path, preparation)
        return runtime_path, preparation_path

    def test_separate_runtime_configuration_deploys_and_keeps_preparation_private(self):
        runtime, preparation = self.separate_inputs()
        self.env.configure("local", runtime, preparation)
        self.assertEqual(self.env.names(), ["dev", "local"])
        saved = mod.read_json(self.env.config_dir / "local.json")
        self.assertEqual(set(saved), mod.RUNTIME_FIELDS)
        self.assertEqual(self.env.config("local")[1], self.config)
        self.assertTrue(self.env.deploy("local")["ready"])
        self.env.remove()
        self.assertTrue((self.env.config_dir / "preparation/local.json").is_file())
        self.assertEqual(self.env.config("local")[1], self.config)

    def test_runtime_change_reuses_preparation_and_requires_update(self):
        runtime, preparation = self.separate_inputs()
        self.env.configure("local", runtime, preparation)
        self.env.deploy("local")
        config = mod.read_json(runtime)
        config["java"]["tm"] = "-Xmx2G"
        mod.write_json(runtime, config)
        self.env.configure("local", runtime)
        self.assertTrue(self.env.status()["configuration_changed"])
        with self.assertRaisesRegex(mod.EnvironmentError, "update"):
            self.env.deploy("local")
        self.assertTrue(self.env.deploy(update=True)["ready"])

    def test_invalid_preparation_does_not_replace_existing_config_or_stop_environment(self):
        runtime, preparation = self.separate_inputs()
        self.env.configure("local", runtime, preparation)
        self.env.deploy("local")
        before = self.env.state()
        saved = (self.env.config_dir / "preparation/local.json").read_bytes()
        mod.write_json(preparation, {"schema_version": 1, "build": []})
        with self.assertRaises(mod.EnvironmentError):
            self.env.configure("local", runtime, preparation)
        self.assertEqual(self.env.state(), before)
        self.assertEqual((self.env.config_dir / "preparation/local.json").read_bytes(), saved)

    def test_missing_preparation_guides_agent_and_does_not_create_configuration(self):
        runtime, _ = self.separate_inputs()
        with self.assertRaisesRegex(mod.EnvironmentError, "Agent"):
            self.env.configure("local", runtime)
        self.assertFalse((self.env.config_dir / "local.json").exists())

    def test_runtime_rejects_build_fields_and_preparation_rejects_runtime_fields(self):
        runtime, preparation = self.separate_inputs()
        r, p = mod.read_json(runtime), mod.read_json(preparation)
        r["build"] = []
        with self.assertRaises(mod.EnvironmentError):
            mod.combine_config(r, p)
        r.pop("build")
        p["platform"] = "linux/amd64"
        with self.assertRaises(mod.EnvironmentError):
            mod.combine_config(r, p)

    def test_configured_startup_grace_reaches_container(self):
        self.config["heartbeat"]["startup_timeout_seconds"] = 900
        self.save("dev")
        self.env.deploy("dev")
        state = self.env.state()
        candidate = self.env.root / "builds" / state["build"]
        self.assertEqual(mod.read_json(candidate / "node1-settings.json")["startup_timeout_seconds"], 900)
        service = mod.read_json(self.env.root / "compose.json")["services"]["node1"]
        self.assertEqual(service["healthcheck"]["start_period"], "900s")

    def test_existing_mongo_requires_user_choice_before_first_start(self):
        self.env.existing_collections = 12
        with self.assertRaisesRegex(mod.EnvironmentError, "用户选择"):
            self.env.deploy("dev")
        self.assertIsNone(self.env.state())
        self.assertNotIn("up", self.env.history)
        self.assertTrue(self.env.deploy("dev", reuse_mongo=True)["ready"])

    def test_existing_mongo_switch_keeps_old_environment_without_choice(self):
        self.env.deploy("dev")
        before = self.env.state()
        self.save("other")
        self.env.existing_collections = 12
        self.env.history.clear()
        with self.assertRaisesRegex(mod.EnvironmentError, "reuse-mongo"):
            self.env.deploy("other", switch=True)
        self.assertEqual(self.env.state(), before)
        self.assertNotIn("down", self.env.history)
        self.assertTrue(self.env.deploy(update=True)["ready"])

    def test_node_settings_mountpoint_exists_before_compose_start(self):
        original_start = self.env.start

        def start(state):
            for node in state["nodes"]:
                target = self.env.root / "nodes" / node["name"] / "settings.json"
                source = self.env.root / "builds" / state["build"] / (node["name"] + "-settings.json")
                self.assertTrue(target.is_file())
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            return original_start(state)

        with mock.patch.object(self.env, "start", side_effect=start):
            self.env.deploy("dev")
            self.config["java"]["tm"] = "-Xmx2G"
            self.save("dev")
            self.env.deploy(update=True)

    def test_compose_up_allows_dependency_startup_grace(self):
        self.env.deploy("dev")
        state = self.env.state()
        state["config"]["heartbeat"]["startup_timeout_seconds"] = 900
        with mock.patch.object(self.env, "compose") as compose:
            self.env.start(state)
        compose.assert_called_once_with("up", "-d", "--remove-orphans", timeout=1200)
        real = mod.Environment(self.station)
        with mock.patch.object(mod, "call", return_value="") as call:
            real.compose("down")
        self.assertEqual(call.call_args.kwargs["timeout"], 300)

    def build_fixture(self):
        output = self.station / "source/tapdata/tapdata/output"
        for name in ("tapdata", "tapdata-agent", self.config["node_binary"], "lib/jdk/bin/java"):
            file = output / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("#!/bin/sh\nexit 0\n")
            file.chmod(0o755)
        for name in ("components/tm.jar", "components/tapdata-agent.jar", "components/webroot/index.html"):
            file = output / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture")
        for name in ("connectors/dist", "components/apiserver"):
            (output / name).mkdir(parents=True)
        driver = self.station / "source" / self.config["mongo_driver"] / "mongodb/package.json"
        driver.parent.mkdir(parents=True)
        driver.write_text("{}")
        self.config["build"] = [{"cwd": "tapdata/tapdata", "argv": ["true"]}]
        return output

    def test_bundled_jdk_is_executed_during_preflight(self):
        self.build_fixture()
        real = mod.Environment(self.station)
        with mock.patch.object(real, "snapshot", return_value={}), mock.patch.object(real, "probe", return_value={"database": "test", "collection_count": 0}), mock.patch.object(mod, "call", return_value="") as calls:
            real.build(self.config)
        commands = [c.args[0] for c in calls.call_args_list]
        self.assertTrue(any("/bundle/lib/jdk/bin/java" in c and c[-1] == "-version" for c in commands))
        self.assertFalse(any(c[c.index("--entrypoint") + 1] == "java" for c in commands))

    @unittest.skipUnless(shutil.which("node"), "Node 未安装，模块搜索路径行为待核验")
    def test_probe_resolves_driver_sibling_modules_and_preserves_secret_boundary(self):
        # 模拟驱动实际文件在独立目录、node_modules 以链接暴露包的布局。
        # Node 按真实文件路径解析依赖，需要显式搜索挂载的 node_modules。
        source = self.station / "source"
        driver = source / self.config["mongo_driver"]
        driver.mkdir(parents=True)
        package = source / "driver-package"
        package.mkdir()
        (package / "index.js").write_text("module.exports = {resolved: require('bson').resolved};")
        (driver / "mongodb").symlink_to(package, target_is_directory=True)
        bson = driver / "bson"
        bson.mkdir()
        (bson / "index.js").write_text("module.exports = {resolved: true};")
        real = mod.Environment(self.station)
        bundle = self.env.root / "bundle"
        script = "console.log(JSON.stringify(require(process.argv[1])))"

        def run_probe(argv, input_text, **kwargs):
            self.assertNotIn("do-not-print", " ".join(argv))
            self.assertEqual(json.loads(input_text)["nodes"], [])
            self.assertEqual(json.loads(input_text)["uri"], real.secret(self.config)[1])
            mounts = [argv[i + 1] for i, value in enumerate(argv) if value == "--mount"]
            self.assertIn("type=bind,src=%s,dst=/driver,readonly" % driver, mounts)
            env = os.environ.copy()
            env.pop("NODE_PATH", None)
            for i, value in enumerate(argv):
                if value == "--env":
                    key, val = argv[i + 1].split("=", 1)
                    env[key] = str(driver) if val == "/driver" else val
            result = subprocess.run(["node", "-e", script, str(driver / "mongodb")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "挂载驱动的传递依赖未能解析")
            return result.stdout

        with mock.patch.object(mod, "call", side_effect=run_probe):
            self.assertTrue(real.probe(self.config, bundle, [])["resolved"])
        env = os.environ.copy()
        env.pop("NODE_PATH", None)
        missing = subprocess.run(["node", "-e", script, str(driver / "mongodb")], env=env, capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Cannot find module 'bson'", missing.stderr)

    def test_no_api_build_and_three_nodes_share_compose_project(self):
        output = self.build_fixture()
        shutil.rmtree(output / "components/apiserver")
        self.config["nodes"] = {"node1": ["TM", "FE"], "node2": ["TM", "FE"], "node3": ["FE"]}
        self.config["ports"] = {
            "node1": {"tm": 53131, "api": None, "license": ""},
            "node2": {"tm": 53133, "api": None, "license": ""},
            "node3": {"tm": None, "api": None, "license": ""},
        }
        real = mod.Environment(self.station)
        with mock.patch.object(real, "snapshot", return_value={}), mock.patch.object(real, "probe", return_value={"database": "test", "collection_count": 0}), mock.patch.object(mod, "call", return_value=""):
            candidate, record = real.build(mod.validate(self.config))
            real.generate("dev", self.config, candidate, record)
        compose = mod.read_json(candidate / "compose.json")
        self.assertEqual(set(compose["services"]), {"node1", "node2", "node3"})
        self.assertEqual(compose["services"]["node3"]["depends_on"], {"node1": {"condition": "service_healthy"}, "node2": {"condition": "service_healthy"}})
        with mock.patch.object(mod, "call", return_value="") as calls:
            real.compose("up", "-d")
            real.compose("down", "--remove-orphans")
        for call in calls.call_args_list:
            self.assertEqual(call.args[0][:6], ["docker", "compose", "-p", real.project, "-f", str(real.root / "compose.json")])

    def test_selected_api_requires_api_artifact_before_docker(self):
        output = self.build_fixture()
        shutil.rmtree(output / "components/apiserver")
        real = mod.Environment(self.station)
        with mock.patch.object(real, "snapshot", return_value={}), mock.patch.object(mod, "call") as calls:
            with self.assertRaisesRegex(mod.EnvironmentError, "components/apiserver"):
                real.build(self.config)
        calls.assert_not_called()

    def test_build_can_prepare_declared_mongo_driver(self):
        self.build_fixture()
        driver = self.station / "source" / self.config["mongo_driver"]
        shutil.rmtree(driver)
        self.config["build"] = [{"cwd": "tapdata/tapdata", "argv": [sys.executable, "-c", "from pathlib import Path; p=Path('../tapdata-enterprise/tapdata-agent/node_modules/mongodb/package.json'); p.parent.mkdir(parents=True); p.write_text('{}')"]}]
        real = mod.Environment(self.station)
        with mock.patch.object(real, "snapshot", return_value={}), mock.patch.object(real, "probe", return_value={"database": "test", "collection_count": 0}), mock.patch.object(mod, "call", return_value=""):
            candidate, record = real.build(self.config)
        self.assertTrue((driver / "mongodb/package.json").is_file())
        self.assertEqual(record["commands"][0]["exit_code"], 0)
        self.assertTrue((candidate / "bundle/tapdata").is_file())

    def test_wrong_architecture_bundled_jdk_fails_before_docker(self):
        output = self.build_fixture()
        header = bytearray(20)
        header[:4] = b"\x7fELF"
        header[5] = 1
        header[18:20] = (62).to_bytes(2, "little")
        (output / "lib/jdk/bin/java").write_bytes(header)
        real = mod.Environment(self.station)
        with mock.patch.object(real, "snapshot", return_value={}), mock.patch.object(mod, "call") as calls:
            with self.assertRaisesRegex(mod.EnvironmentError, "架构"):
                real.build(self.config)
        calls.assert_not_called()

    def test_single_configuration_selection_and_ambiguous_names(self):
        self.assertEqual(self.env.config(None)[0], "dev")
        self.save("other")
        with self.assertRaisesRegex(mod.EnvironmentError, "--env"):
            self.env.config(None)
        self.assertEqual(self.env.names(), ["dev", "other"])

    def test_component_validation(self):
        for nodes in ({}, {"node1": ["FE"]}, {"node1": ["TM", "TM"]}, {"node1": ["TM", "unknown"]}, {"../bad": ["TM"]}):
            config = copy.deepcopy(self.config)
            config["nodes"] = nodes
            with self.assertRaises(mod.EnvironmentError):
                mod.validate(config)

    def test_instance_platform_requires_explicit_configuration(self):
        config = copy.deepcopy(self.config)
        config["platform"] = ""
        with self.assertRaisesRegex(mod.EnvironmentError, "platform"):
            mod.validate(config)
        config["platform"] = "linux/amd64"
        mod.validate(config)

    def test_node_count_limits(self):
        config = copy.deepcopy(self.config)
        config["nodes"] = {"n%s" % i: ["TM"] for i in range(4)}
        with self.assertRaises(mod.EnvironmentError):
            mod.validate(config)

    def test_three_roles_backend_routing_ports_and_dependencies(self):
        config = copy.deepcopy(self.config)
        config["nodes"] = {"node1": ["TM", "FE", "APIServer"], "node2": ["TM"], "node3": ["FE"]}
        config["ports"] = {"node1": {"tm": 53131, "api": 53132, "license": ""}, "node2": {"tm": 53133, "api": None, "license": ""}, "node3": {"tm": None, "api": None, "license": ""}}
        mod.validate(config)
        self.save("roles", config)
        result = self.env.deploy("roles")
        self.assertTrue(result["ready"])
        compose = mod.read_json(self.env.root / "compose.json")["services"]
        self.assertEqual(compose["node1"]["user"], "%s:%s" % (os.getuid(), os.getgid()))
        self.assertEqual(compose["node1"]["environment"]["HOME"], "/tapdata/home")
        self.assertEqual(len(compose["node1"]["ports"]), 2)
        self.assertEqual(len(compose["node2"]["ports"]), 1)
        self.assertEqual(compose["node3"]["ports"], [])
        self.assertEqual(set(compose["node3"]["depends_on"]), {"node1", "node2"})
        candidate = self.env.root / "builds" / ("b" * 32)
        self.assertEqual(mod.read_json(candidate / "node3-settings.json")["backend_url"], "http://node1:3030/api/,http://node2:3030/api/")
        self.assertEqual(mod.read_json(candidate / "node2-settings.json")["backend_url"], "http://node1:3030/api/,http://127.0.0.1:3030/api/")
        self.assertTrue(all(service["labels"][mod.LABEL] == self.env.identity for service in compose.values()))

    def test_unused_component_port_rejected(self):
        config = copy.deepcopy(self.config)
        config["nodes"]["node1"] = ["TM"]
        with self.assertRaisesRegex(mod.EnvironmentError, "null"):
            mod.validate(config)

    def test_deploy_reuses_same_environment_without_build(self):
        self.env.deploy("dev")
        self.env.history.clear()
        self.assertTrue(self.env.deploy("dev")["ready"])
        self.assertNotIn("build", self.env.history)

    def test_changed_configuration_not_automatically_applied(self):
        self.env.deploy("dev")
        before = self.env.state()
        self.config["java"]["tm"] = "-Xmx2G"
        self.save("dev")
        self.assertTrue(self.env.status()["configuration_changed"])
        with self.assertRaisesRegex(mod.EnvironmentError, "update"):
            self.env.deploy("dev")
        self.assertEqual(self.env.state(), before)

    def test_changed_secret_requires_update(self):
        self.env.deploy("dev")
        secret = self.station / "config/secrets/mongodb-uri"
        secret.write_text("mongodb://tester:changed@host.docker.internal:27017/test")
        self.assertTrue(self.env.status()["configuration_changed"])
        with self.assertRaises(mod.EnvironmentError):
            self.env.deploy("dev")

    def test_other_environment_requires_explicit_switch(self):
        self.env.deploy("dev")
        self.save("other")
        with self.assertRaisesRegex(mod.EnvironmentError, "--switch"):
            self.env.deploy("other")
        self.assertEqual(self.env.state()["environment"], "dev")
        self.assertEqual(len(self.env.containers), 1)

    def test_switch_build_failure_preserves_original(self):
        self.env.deploy("dev")
        state = self.env.state()
        compose = (self.env.root / "compose.json").read_bytes()
        self.save("other")
        self.env.build_failure = True
        self.env.history.clear()
        with self.assertRaises(mod.EnvironmentError):
            self.env.deploy("other", switch=True)
        self.assertNotIn("down", self.env.history)
        self.assertEqual(self.env.state(), state)
        self.assertEqual((self.env.root / "compose.json").read_bytes(), compose)

    def test_compose_preflight_failure_does_not_stop_original(self):
        self.env.deploy("dev")
        self.env.history.clear()
        before = self.env.state()
        def reject(argv, **kwargs):
            if "--quiet" in argv:
                raise mod.EnvironmentError("配置预检失败")
            return self.fake_call(argv, **kwargs)
        with mock.patch.object(mod, "call", side_effect=reject):
            with self.assertRaises(mod.EnvironmentError):
                self.env.deploy("dev", update=True)
        self.assertNotIn("down", self.env.history)
        self.assertEqual(self.env.state(), before)

    def test_update_preserves_node_identity_and_work(self):
        self.env.deploy("dev")
        identity = self.env.state()["nodes"][0]["uuid"]
        monitor = self.env.root / "nodes/node1/work/os-monitor/uuid.js"
        monitor.parent.mkdir(parents=True)
        monitor.write_text("persistent-os-identity")
        self.env.artifact = "new"
        result = self.env.deploy(update=True)
        self.assertEqual(result["artifact_digest"], "new")
        self.assertEqual(self.env.state()["nodes"][0]["uuid"], identity)
        self.assertEqual(monitor.read_text(), "persistent-os-identity")
        self.assertLess(self.env.history.index("build"), self.env.history.index("down"))

    def test_switch_changes_identity_and_erases_old_work(self):
        self.env.deploy("dev")
        identity = self.env.state()["nodes"][0]["uuid"]
        marker = self.env.root / "nodes/node1/marker"
        marker.write_text("old")
        self.save("other")
        result = self.env.deploy("other", switch=True)
        self.assertEqual(result["active"], "other")
        self.assertNotEqual(self.env.state()["nodes"][0]["uuid"], identity)
        self.assertFalse(marker.exists())
        self.assertEqual(self.env.names(), ["dev", "other"])

    def test_healthy_container_without_heartbeat_is_not_ready(self):
        self.env.deploy("dev")
        self.env.monitor_ready = False
        status = self.env.status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["containers"][0]["health"], "healthy")
        self.assertFalse(status["monitoring"]["verified"])

    def test_uninstall_preserves_configuration_source_and_external_mongo(self):
        self.env.deploy("dev")
        self.save("other")
        source = self.station / "source/tapdata/tapdata/code.java"
        source.parent.mkdir(parents=True)
        source.write_text("source")
        mongo_marker = self.station.parent / "external-mongo-data"
        mongo_marker.write_text("external")
        self.env.remove()
        self.assertFalse(self.env.root.exists())
        self.assertEqual(self.env.names(), ["dev", "other"])
        self.assertTrue(source.exists())
        self.assertEqual(mongo_marker.read_text(), "external")
        self.assertEqual(self.env.containers, [])
        self.assertTrue(list((self.station / "runtime/tapdata-test-env-diagnostics").glob("*/summary.json")))

    def test_stale_component_observation_is_not_ready(self):
        self.env.deploy("dev")
        with mock.patch.object(mod, "call", return_value=json.dumps({"time": 1, "components": {"launcher": True, "tm": True, "engine": True, "api": True}})):
            self.assertFalse(self.env.status()["ready"])

    def test_failed_down_preserves_runtime_for_readback(self):
        self.env.deploy("dev")
        with mock.patch.object(self.env, "compose", side_effect=mod.EnvironmentError("写入结果不明")):
            with self.assertRaises(mod.EnvironmentError):
                self.env.remove()
        self.assertTrue(self.env.root.exists())
        self.assertEqual(self.env.state()["phase"], "stopping")
        self.assertEqual(len(self.env.containers), 1)

    def test_conflicting_port_preflight_preserves_original(self):
        self.env.deploy("dev")
        self.env.history.clear()
        with mock.patch.object(mod.socket, "socket") as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            with self.assertRaisesRegex(mod.EnvironmentError, "端口"):
                self.env.deploy(update=True)
        self.assertNotIn("down", self.env.history)

    def test_owned_failed_build_can_be_uninstalled_without_active_record(self):
        mod.write_json(self.env.root / "owner.json", {"station_id": self.env.identity})
        mod.write_json(self.env.root / "builds" / ("d" * 32) / "build-record.json", {"commands": [{"exit_code": 7}]})
        self.env.remove()
        self.assertFalse(self.env.root.exists())
        self.assertEqual(self.env.names(), ["dev"])

    def test_unknown_resource_without_record_prevents_deployment(self):
        self.env.containers = [{"Id": "unknown"}]
        with self.assertRaisesRegex(mod.EnvironmentError, "未登记"):
            self.env.deploy("dev")
        self.assertFalse(self.env.history)

    def test_unknown_container_label_rejected(self):
        real = mod.Environment(self.station)
        def docker(argv, **kwargs):
            if argv[1] == "ps":
                return '{"ID":"unowned"}\n'
            return json.dumps([{"Config": {"Labels": {mod.LABEL: "other-station"}}}])
        with mock.patch.object(mod, "call", side_effect=docker):
            with self.assertRaisesRegex(mod.EnvironmentError, "归属"):
                real.docker_resources()

    def test_secret_permission_and_localhost_validation(self):
        secret = self.station / "config/secrets/mongodb-uri"
        secret.chmod(0o644)
        with self.assertRaisesRegex(mod.EnvironmentError, "当前用户"):
            self.env.secret(self.config)
        secret.chmod(0o600)
        secret.write_text("mongodb://secret@localhost:27017/test")
        with self.assertRaisesRegex(mod.EnvironmentError, "localhost"):
            self.env.secret(self.config)

    def test_path_escape_and_symlink_runtime_rejected(self):
        with self.assertRaises(mod.EnvironmentError):
            mod.contained(self.env.config_dir, "../outside")
        self.env.root.parent.mkdir(parents=True)
        self.env.root.symlink_to(self.station.parent)
        with self.assertRaisesRegex(mod.EnvironmentError, "符号链接"):
            mod.Environment(self.station)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI 未安装，Compose schema 待核验")
    def test_generated_compose_accepted_by_real_cli(self):
        version = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if version.returncode:
            self.skipTest("Docker Compose CLI 不可用")
        self.env.deploy("dev")
        result = subprocess.run(["docker", "compose", "-p", self.env.project, "-f", str(self.env.root / "compose.json"), "config", "--quiet"], capture_output=True)
        self.assertEqual(result.returncode, 0, "生成的 Compose schema 未通过真实 CLI 校验")

    def test_node_binary_shell_injection_rejected(self):
        config = copy.deepcopy(self.config)
        config["node_binary"] = "node; touch /tmp/bad"
        with self.assertRaises(mod.EnvironmentError):
            mod.validate(config)

    def test_cli_errors_do_not_expose_driver_errors(self):
        error = mod.EnvironmentError("外部命令失败")
        with mock.patch.object(mod.Environment, "status", side_effect=error), mock.patch("sys.stderr") as stderr:
            self.assertEqual(mod.main(["status", "--station", str(self.station)]), 2)
        self.assertNotIn("do-not-print", str(stderr.write.call_args_list))
        with mock.patch.object(mod.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="mongodb://password", stdout="")):
            with self.assertRaises(mod.EnvironmentError) as caught:
                REAL_CALL(["docker", "test"])
        self.assertNotIn("password", str(caught.exception))

    def test_source_build_failure_keeps_running_environment(self):
        self.env.deploy("dev")
        original = self.env.state()
        # 使用真实构建代码、实际 Git 仓库及失败命令。
        repo = self.station / "source/tapdata/tapdata"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        file = repo / "tracked.txt"
        file.write_text("tracked")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
        driver = self.station / "source/tapdata/tapdata-enterprise/tapdata-agent/node_modules/mongodb"
        driver.mkdir(parents=True)
        (driver / "package.json").write_text("{}")
        config = copy.deepcopy(self.config)
        config["build"] = [{"cwd": "tapdata/tapdata", "argv": ["python3", "-c", "raise SystemExit(7)"]}]
        real_call = REAL_CALL
        with mock.patch.object(mod, "call", side_effect=real_call):
            with self.assertRaisesRegex(mod.EnvironmentError, "宿主机构建失败"):
                mod.Environment(self.station).build(config)
        self.assertEqual(self.env.state(), original)
        self.assertTrue(list((self.env.root / "builds").glob("*/build-record.json")))

    def test_legacy_station_skill_wiring_discovers_new_skill(self):
        render_spec = importlib.util.spec_from_file_location("render_test", ROOT / "bootstrap/render.py")
        render = importlib.util.module_from_spec(render_spec)
        import sys
        with mock.patch.object(sys, "path", [str(ROOT / "bootstrap"), str(ROOT), *sys.path]):
            render_spec.loader.exec_module(render)
            skills = render.project_skill_sources(ROOT, "tapdata")
        self.assertIn(ROOT / "projects/tapdata/skills/tapdata-test-env", skills)
        self.assertEqual(mod.read_json(ROOT / "contracts/station-state-compatibility.json")["station_state_epoch"], 27)


@unittest.skipUnless(shutil.which("node"), "Node 未安装，容器健康脚本行为待核验")
class NodeHealthTests(unittest.TestCase):
    def test_configuration_emits_private_yaml_with_escaped_scalars(self):
        script = ROOT / "projects/tapdata/scripts/test-environment-node.js"
        harness = r'''
const fs = require('fs'), vm = require('vm');
const writes=[];
const settings={backend_url:'http://node1:3030/api/',engine_opts:'-Xmx2G',tm_opts:'-Xmx1G',uuid:'node-identity'};
const mockFs={mkdirSync:()=>{},readFileSync:p=>p==='/secret/mongo-uri' ? 'mongodb://user:p"ass@mongo/test' : JSON.stringify(settings),writeFileSync:(p,data,options)=>writes.push({p,data,mode:options.mode})};
const localProcess={argv:['','','configure'],exitCode:0};
const mocks={fs:mockFs,path:require('path'),child_process:{},net:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{require:n=>mocks[n],process:localProcess});
setImmediate(()=>console.log(JSON.stringify({writes,exitCode:localProcess.exitCode})));
'''
        result = subprocess.run(["node", "-e", harness, str(script)], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["exitCode"], 0)
        self.assertEqual({w["p"] for w in output["writes"]}, {"/tapdata/apps/application.yml", "/tapdata/apps/etc/application.yml", "/tapdata/work/application.yml", "/tapdata/work/etc/application.yml", "/tapdata/work/uuid.js"})
        for write in output["writes"]:
            self.assertEqual(write["mode"], 0o600)
            if write["p"].endswith("uuid.js"):
                self.assertEqual(write["data"], 'module.exports = "node-identity";\n')
                continue
            self.assertTrue(write["data"].startswith("spring:\n  data:\n    mongodb:\n"))
            self.assertIn('      mongoConnectionString: "mongodb://user:p\\"ass@mongo/test"\n', write["data"])
            self.assertIn('    tapdataPort: "3030"\n', write["data"])
            self.assertIn('    uuid: "node-identity"\n', write["data"])

    def health(self, roles, process_rows):
        script = ROOT / "projects/tapdata/scripts/test-environment-node.js"
        harness = r'''
const fs = require('fs'), vm = require('vm');
const settings = JSON.parse(process.argv[1]);
const rows = process.argv[2];
const localProcess = {pid: 99999, argv: ['', '', 'health'], exitCode: 0};
let observation;
const mockFs = {renameSync:()=>{},readFileSync: () => JSON.stringify(settings), writeFileSync: (_, data) => {observation=JSON.parse(data);}};
const mockNet = {connect: () => {const callbacks={}; const s={destroy:()=>{},setTimeout:()=>{},on:(event,cb)=>{callbacks[event]=cb;if(event==='error')setImmediate(()=>callbacks.connect());}};return s;}};
const mocks={fs:mockFs,child_process:{execFileSync:()=>rows},net:mockNet,path:require('path')};
vm.runInNewContext(fs.readFileSync(process.argv[3],'utf8'), {require:name=>mocks[name],process:localProcess,console,setTimeout});
setTimeout(()=>console.log(JSON.stringify({exitCode:localProcess.exitCode,observation})),30);
'''
        result = subprocess.run(["node", "-e", harness, json.dumps({"components": roles}), process_rows, str(script)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_tm_only_does_not_require_engine_or_api(self):
        result = self.health(["TM"], "100 /tapdata/apps/tapdata-agent agent --workDir /tapdata/work\n101 java -jar /tapdata/apps/components/tm.jar")
        self.assertEqual(result["exitCode"], 0)
        self.assertFalse(result["observation"]["components"]["engine"])

    def test_fe_only_does_not_require_local_tm(self):
        result = self.health(["FE"], "100 /tapdata/apps/tapdata-agent agent\n101 java -jar /tapdata/apps/components/tapdata-agent.jar")
        self.assertEqual(result["exitCode"], 0)

    def test_missing_launcher_fails_even_when_selected_java_exists(self):
        result = self.health(["TM"], "101 java -jar /tapdata/apps/components/tm.jar")
        self.assertEqual(result["exitCode"], 1)

    def test_unselected_process_is_reported_unhealthy(self):
        result = self.health(["TM"], "100 /tapdata/apps/tapdata-agent agent\n101 java -jar /tapdata/apps/components/tm.jar\n102 java -jar /tapdata/apps/components/tapdata-agent.jar")
        self.assertEqual(result["exitCode"], 1)


@unittest.skipUnless(shutil.which("node"), "Node 未安装，监控探测行为待核验")
class MonitorProbeTests(unittest.TestCase):
    def probe(self, timestamp, registered=True, reachable=True, preflight=False):
        script = ROOT / "projects/tapdata/scripts/test-environment-probe.js"
        harness = r"""
const vm = require('vm'), fs = require('fs');
const args = JSON.parse(process.argv[1]);
const callbacks = {}, output=[];
const localProcess = {exitCode:0,stdin:{setEncoding:()=>{},on:(event,cb)=>{callbacks[event]=cb;}}};
class MongoClient {
  async connect() {} async close() {}
  db() {return {databaseName:'test',listCollections:()=>({toArray:async()=>[{name:'Settings'},{name:'Users'}]}),command:async()=>({hosts:['mongo.internal:27017']}),collection:()=>({findOne:async()=>args.registered ? {systemInfo:{time:args.timestamp}} : null})};}
}
const net={connect:()=>{const cbs={};const socket={destroy:()=>{},setTimeout:()=>{},on:(event,cb)=>{cbs[event]=cb;if(event==='error')setImmediate(()=>cbs[args.reachable ? 'connect' : 'error']());}};return socket;}};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{require:name=>name==='net'?net:{MongoClient},process:localProcess,console:{log:value=>output.push(value)}});
callbacks.data(JSON.stringify({uri:'mongodb://private-password@mongo.internal/test',nodes:args.preflight ? [] : [{name:'node1',uuid:'identity'}],heartbeat:{collection:'ClusterState',uuid_field:'uuid',time_field:'systemInfo.time',max_age_seconds:60}}));
callbacks.end().then(()=>console.log(JSON.stringify({exitCode:localProcess.exitCode,output})));
"""
        run = subprocess.run(["node", "-e", harness, json.dumps({"timestamp": timestamp, "registered": registered, "reachable": reachable, "preflight": preflight}), str(script)], capture_output=True, text=True, check=True)
        self.assertNotIn("private-password", run.stdout + run.stderr)
        return json.loads(run.stdout)

    def test_preflight_reads_database_inventory_without_records_or_mutations(self):
        result = self.probe(0, preflight=True)
        self.assertEqual(result["exitCode"], 0)
        report = json.loads(result["output"][0])
        self.assertEqual(report["database"], "test")
        self.assertEqual(report["collection_count"], 2)
        self.assertEqual(report["nodes"], [])
        self.assertFalse(report["verified"])
        self.assertNotIn("Users", result["output"][0])

    def test_fresh_registered_heartbeat_passes(self):
        result = self.probe(mod.time.time() * 1000)
        self.assertEqual(result["exitCode"], 0)
        self.assertTrue(json.loads(result["output"][0])["verified"])

    def test_stale_or_missing_record_is_unverified(self):
        for timestamp, registered in ((1, True), (mod.time.time() * 1000, False)):
            result = self.probe(timestamp, registered=registered)
            self.assertFalse(json.loads(result["output"][0])["verified"])

    def test_unreachable_advertised_member_fails_closed(self):
        result = self.probe(mod.time.time() * 1000, reachable=False)
        self.assertEqual(result["exitCode"], 1)
        self.assertEqual(result["output"], [])


if __name__ == "__main__":
    unittest.main()
