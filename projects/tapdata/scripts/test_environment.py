#!/usr/bin/env python3
"""TapData 工位 Docker 环境生命周期；仅使用 Python 3.9 标准库。"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LABEL = "io.agenticops.station"


class EnvironmentError(ValueError):
    pass


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        os.chmod(tmp, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    tmp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def contained(root, value):
    result = (root / value).resolve()
    if result == root.resolve() or root.resolve() not in result.parents:
        raise EnvironmentError("路径必须位于指定目录内")
    return result


def call(argv, input_text=None, timeout=120):
    result = subprocess.run(argv, input=input_text, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Docker/驱动错误可能含 URI，不将 stderr 或展开参数送入用户输出。
        raise EnvironmentError("外部命令失败，请核验工具、网络及授权（退出码 %s）" % result.returncode)
    return result.stdout


def validate(config):
    required = {"schema_version", "nodes", "image", "platform", "mongo_uri_file", "mongo_driver", "build", "assets", "node_binary", "ports", "java", "heartbeat"}
    if not isinstance(config, dict) or set(config) != required or type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise EnvironmentError("配置字段或 schema_version 无效")
    if not isinstance(config["nodes"], dict) or not 1 <= len(config["nodes"]) <= 3:
        raise EnvironmentError("nodes 必须提供 1～3 个节点及组件列表")
    for name, components in config["nodes"].items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(components, list) or not components or any(not isinstance(c, str) for c in components) or len(set(components)) != len(components) or any(c not in ("TM", "FE", "APIServer") for c in components):
            raise EnvironmentError("节点必须提供唯一的 TM/FE/APIServer 组件列表")
    if not any("TM" in components for components in config["nodes"].values()):
        raise EnvironmentError("环境至少需要一个 TM，供 Launcher 与 FE 连接")
    if not isinstance(config["image"], str) or not config["image"] or any(c.isspace() for c in config["image"]):
        raise EnvironmentError("image 无效")
    if config["platform"] not in ("linux/amd64", "linux/arm64"):
        raise EnvironmentError("platform 必须为 linux/amd64 或 linux/arm64")
    for field in ("mongo_uri_file", "mongo_driver", "node_binary"):
        if not isinstance(config[field], str) or not config[field]:
            raise EnvironmentError(field + " 必须为文件路径")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", config["node_binary"]) or Path(config["node_binary"]).is_absolute() or ".." in Path(config["node_binary"]).parts:
        raise EnvironmentError("node_binary 必须为安全的包内相对路径")
    if not isinstance(config["java"], dict) or set(config["java"]) != {"tm", "engine"} or not all(isinstance(v, str) and v for v in config["java"].values()):
        raise EnvironmentError("java 必须提供 tm 与 engine 参数")
    hb = config["heartbeat"]
    if not isinstance(hb, dict) or set(hb) != {"collection", "uuid_field", "time_field", "max_age_seconds", "startup_timeout_seconds"}:
        raise EnvironmentError("heartbeat 字段无效")
    for key in ("collection", "uuid_field", "time_field"):
        if not isinstance(hb[key], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", hb[key]):
            raise EnvironmentError("监控字段名无效")
    for key in ("max_age_seconds", "startup_timeout_seconds"):
        if type(hb[key]) is not int or not 20 <= hb[key] <= 900:
            raise EnvironmentError("监控超时必须为 20～900 秒")
    if not isinstance(config["ports"], dict) or set(config["ports"]) != set(config["nodes"]):
        raise EnvironmentError("ports 必须逐节点配置")
    used = []
    for node_name, item in config["ports"].items():
        if not isinstance(item, dict) or not {"tm", "api", "license"} == set(item):
            raise EnvironmentError("节点必须提供 tm/api/license")
        for key in ("tm", "api"):
            port = item[key]
            component = "TM" if key == "tm" else "APIServer"
            if component not in config["nodes"][node_name]:
                if port is not None:
                    raise EnvironmentError("未部署组件的宿主机端口必须为 null")
                continue
            if type(port) is not int or not 1024 <= port <= 65535 or port in used:
                raise EnvironmentError("宿主机端口无效或重复")
            used.append(port)
        if not isinstance(item["license"], str):
            raise EnvironmentError("license 必须为路径或空字符串")
    if not isinstance(config["build"], list) or not isinstance(config["assets"], list):
        raise EnvironmentError("build/assets 必须为数组")
    for step in config["build"]:
        if not isinstance(step, dict) or set(step) != {"cwd", "argv"} or not isinstance(step["cwd"], str) or not step["cwd"]:
            raise EnvironmentError("构建步骤必须提供 cwd/argv")
        if not isinstance(step["argv"], list) or not step["argv"] or not all(isinstance(a, str) and a for a in step["argv"]):
            raise EnvironmentError("构建命令必须为非空 argv 数组")
        if any("mongodb://" in a or "mongodb+srv://" in a for a in step["argv"]):
            raise EnvironmentError("构建参数不得包含 MongoDB URI")
    for asset in config["assets"]:
        if not isinstance(asset, dict) or set(asset) != {"source", "target"} or not all(isinstance(v, str) and v for v in asset.values()):
            raise EnvironmentError("装配项必须提供 source/target")
        if Path(asset["target"]).is_absolute() or ".." in Path(asset["target"]).parts:
            raise EnvironmentError("装配目标不得越界")
    return config


class Environment:
    def __init__(self, station):
        self.station = Path(station).resolve()
        binding = read_json(self.station / ".agenticops/station.json")
        if binding.get("project") != "tapdata" or not re.fullmatch(r"[a-f0-9]{32}", binding.get("station_id", "")):
            raise EnvironmentError("需要已绑定 TapData 的工位")
        self.identity = binding["station_id"]
        self.project = "ao-tapdata-" + self.identity
        self.config_dir = self.station / "config/tapdata-test-env"
        self.root = self.station / "runtime/tapdata-test-env"
        # 不允许目录链接将受管写入/清理引向其它材料。
        for path in (self.config_dir, self.root):
            if path.resolve() != path or any(p.is_symlink() for p in path.parents if p != self.station.parent):
                raise EnvironmentError("工位配置和运行目录不得经过符号链接")
        self.source = self.station / "source"
        self.network_ids = []

    @contextlib.contextmanager
    def lock(self):
        self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.config_dir / ".lock").open("a") as stream:
            os.chmod(stream.name, 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EnvironmentError("另一个环境操作仍在进行")
            yield

    def names(self):
        return sorted(p.stem for p in self.config_dir.glob("*.json") if NAME.fullmatch(p.stem))

    def config(self, name):
        if not name:
            names = self.names()
            if len(names) != 1:
                raise EnvironmentError("请通过 --env 选择配置：" + ", ".join(names))
            name = names[0]
        if not NAME.fullmatch(name):
            raise EnvironmentError("环境名称必须为小写字母、数字和连字符")
        path = contained(self.config_dir, name + ".json")
        return name, validate(read_json(path))

    def state(self):
        path = self.root / "active.json"
        if not path.exists():
            return None
        state = read_json(path)
        if not isinstance(state, dict) or state.get("station_id") != self.identity or not NAME.fullmatch(state.get("environment", "")):
            raise EnvironmentError("运行记录归属不明")
        validate(state["config"])
        if not re.fullmatch(r"[a-f0-9]{32}", state.get("build", "")):
            raise EnvironmentError("构建记录无效")
        if not isinstance(state.get("nodes"), list) or any(not isinstance(n, dict) for n in state["nodes"]) or {n["name"] for n in state["nodes"]} != set(state["config"]["nodes"]):
            raise EnvironmentError("节点记录无效")
        for node in state["nodes"]:
            if not NAME.fullmatch(node["name"]) or str(uuid.UUID(node["uuid"])) != node["uuid"]:
                raise EnvironmentError("节点身份无效")
        return state

    def docker_resources(self):
        rows = call(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + self.project, "--format", "{{json .}}"])
        containers = []
        for line in rows.splitlines():
            row = json.loads(line)
            info = json.loads(call(["docker", "inspect", row["ID"]]))[0]
            if info["Config"].get("Labels", {}).get(LABEL) != self.identity:
                raise EnvironmentError("Compose 容器归属不明，停止操作")
            containers.append(info)
        networks = call(["docker", "network", "ls", "--filter", "label=com.docker.compose.project=" + self.project, "--format", "{{.ID}}"])
        self.network_ids = networks.splitlines()
        for ident in self.network_ids:
            info = json.loads(call(["docker", "network", "inspect", ident]))[0]
            if info.get("Labels", {}).get(LABEL) != self.identity:
                raise EnvironmentError("Compose 网络归属不明，停止操作")
        return containers

    def compose(self, *args, timeout=300):
        return call(["docker", "compose", "-p", self.project, "-f", str(self.root / "compose.json"), *args], timeout=timeout)

    def secret(self, config):
        path = contained(self.station / "config/secrets", config["mongo_uri_file"])
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise EnvironmentError("Mongo URI 文件必须存在且仅当前用户可读")
        value = path.read_text().strip()
        if not value.startswith(("mongodb://", "mongodb+srv://")) or "\n" in value:
            raise EnvironmentError("MongoDB URI 无效")
        # 不猜测修改 URI；localhost 在临时探测容器里也不是宿主机。
        authority = value.split("://", 1)[1].split("/", 1)[0].rsplit("@", 1)[-1]
        if re.search(r"(^|,)(localhost|127\.0\.0\.1|\[::1\])(?=:|,|$)", authority):
            raise EnvironmentError("容器内 localhost 不是宿主机；请配置 host.docker.internal 或容器可达地址")
        return path, value

    def source_path(self, value):
        path = contained(self.source, value)
        if not path.exists():
            raise EnvironmentError("工位源码或构建产物缺失")
        return path

    def snapshot(self):
        result = []
        for git in sorted(self.source.glob("*/*/.git")):
            repo = git.parent
            head = call(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
            branch = call(["git", "-C", str(repo), "branch", "--show-current"]).strip()
            diff = call(["git", "-C", str(repo), "diff", "HEAD", "--binary"])
            untracked = call(["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"])
            hashes = []
            for name in untracked.split("\0"):
                if name:
                    file = repo / name
                    hashes.append((name, hashlib.sha256(file.read_bytes() if not file.is_symlink() else os.readlink(file).encode()).hexdigest()))
            result.append({"repository": str(repo.relative_to(self.source)), "head": head, "branch": branch, "working_tree": digest([diff, hashes])})
        if not result:
            raise EnvironmentError("工位没有可核验的 Git 源码")
        return result

    def build(self, config):
        if not config["build"] or not config["assets"]:
            raise EnvironmentError("请先根据目标分支配置 build 步骤与完整 Launcher 装配 assets")
        self.secret(config)
        driver = self.source_path(config["mongo_driver"])
        if not (driver / "mongodb/package.json").is_file():
            raise EnvironmentError("需要目标 Launcher 已有的 mongodb Node 模块，不自动安装依赖")
        before = self.snapshot()
        owner = self.root / "owner.json"
        if self.root.exists() and not self.state() and (not owner.is_file() or read_json(owner).get("station_id") != self.identity):
            raise EnvironmentError("构建现场归属不明，不覆盖已有目录")
        write_json(owner, {"station_id": self.identity})
        candidate = self.root / "builds" / uuid.uuid4().hex
        if candidate.resolve() != candidate:
            raise EnvironmentError("构建目录不得经过符号链接")
        candidate.mkdir(parents=True, mode=0o700)
        env = os.environ.copy()
        env["TAPDATA_TEST_ENV_BUILD_DIR"] = str(candidate)
        commands = []
        for index, step in enumerate(config["build"]):
            cwd = self.source_path(step["cwd"])
            with (candidate / ("build-%s.log" % index)).open("wb") as log:
                os.chmod(log.name, 0o600)
                result = subprocess.run(step["argv"], cwd=cwd, env=env, stdout=log, stderr=log)
            commands.append({"cwd": step["cwd"], "argv": step["argv"], "exit_code": result.returncode})
            write_json(candidate / "build-record.json", {"source": before, "commands": commands})
            if result.returncode:
                raise EnvironmentError("宿主机构建失败，原环境未变；私有日志已保留")
        if before != self.snapshot():
            raise EnvironmentError("构建期间源码发生变化，保留候选但不部署")
        bundle = candidate / "bundle"
        bundle.mkdir()
        for asset in config["assets"]:
            source = self.source_path(asset["source"])
            target = bundle if asset["target"] == "." else contained(bundle, asset["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(source, target)
        for file in bundle.rglob("*"):
            if file.is_symlink() and bundle.resolve() not in file.resolve().parents:
                raise EnvironmentError("装配含越界链接，可能引用宿主机二进制")
        required = ["tapdata", "tapdata-agent", config["node_binary"], "components/tm.jar", "components/tapdata-agent.jar"]
        for name in required:
            path = contained(bundle, name)
            if not path.is_file() or not path.stat().st_size:
                raise EnvironmentError("完整 Launcher 装配缺失：" + name)
        for name in ("tapdata", "tapdata-agent", config["node_binary"], "lib/java/bin/java", "lib/jdk/bin/java"):
            binary = contained(bundle, name)
            if name in ("lib/java/bin/java", "lib/jdk/bin/java") and not binary.exists():
                continue
            if not os.access(binary, os.X_OK):
                raise EnvironmentError("Launcher/Node 产物不可执行")
            with binary.open("rb") as stream:
                header = stream.read(20)
            if header.startswith(b"\x7fELF"):
                endian = "little" if header[5] == 1 else "big"
                machine = int.from_bytes(header[18:20], endian)
                if machine != (183 if config["platform"] == "linux/arm64" else 62):
                    raise EnvironmentError("装配二进制架构与目标平台不一致")
            elif not header.startswith(b"#!"):
                raise EnvironmentError("Launcher/Node 必须为 Linux ELF 或可核验的脚本入口")
        for unpacked, packed in (("connectors/dist", "connectors/dist.tar.gz"), ("components/apiserver", "components/apiserver.tar.gz")):
            if not (bundle / unpacked).is_dir() and not (bundle / packed).is_file():
                raise EnvironmentError("装配缺失：" + unpacked)
        if not any((bundle / "components/webroot").glob("*")):
            raise EnvironmentError("装配缺失 WebUI")
        hashes = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob("*")) if p.is_file()}
        record = {"source": before, "commands": commands, "artifact_digest": digest(hashes), "files": hashes}
        write_json(candidate / "build-record.json", record)
        # 真实执行目标 Linux Node 和 Java，Mach-O/错误架构在停止原环境前失败。
        java_entries = ["/bundle/" + name for name in ("lib/jdk/bin/java", "lib/java/bin/java") if (bundle / name).exists()] or ["java"]
        for java_entry in java_entries:
            call(["docker", "run", "--rm", "--platform", config["platform"], "--mount", "type=bind,src=%s,dst=/bundle,readonly" % bundle, "--entrypoint", java_entry, config["image"], "-version"], timeout=180)
        call(["docker", "run", "--rm", "--platform", config["platform"], "--mount", "type=bind,src=%s,dst=/bundle,readonly" % bundle, "--entrypoint", "/bundle/" + config["node_binary"], config["image"], "--version"], timeout=180)
        self.probe(config, bundle, [])
        return candidate, record

    def probe(self, config, bundle, nodes):
        _, uri = self.secret(config)
        driver = self.source_path(config["mongo_driver"])
        script = HERE / "test-environment-probe.js"
        output = call(["docker", "run", "--rm", "-i", "--platform", config["platform"], "--add-host", "host.docker.internal:host-gateway", "--mount", "type=bind,src=%s,dst=/bundle,readonly" % bundle, "--mount", "type=bind,src=%s,dst=/driver,readonly" % driver, "--mount", "type=bind,src=%s,dst=/probe.js,readonly" % script, "--entrypoint", "/bundle/" + config["node_binary"], config["image"], "/probe.js"], input_text=json.dumps({"uri": uri, "heartbeat": config["heartbeat"], "nodes": nodes}), timeout=60)
        return json.loads(output)

    def generate(self, name, config, candidate, record, previous=None):
        nodes = []
        old_nodes = {n["name"]: n for n in previous.get("nodes", [])} if previous else {}
        for node_name, components in config["nodes"].items():
            nodes.append({"name": node_name, "uuid": old_nodes.get(node_name, {}).get("uuid", str(uuid.uuid4())), "components": components})
        tm_names = [n["name"] for n in nodes if "TM" in n["components"]]
        secret, _ = self.secret(config)
        services = {}
        for node in nodes:
            directory = self.root / "nodes" / node["name"]
            if directory.resolve() != directory:
                raise EnvironmentError("节点目录不得经过符号链接")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            backend_urls = ["http://%s:3030/api/" % ("127.0.0.1" if n == node["name"] else n) for n in tm_names]
            settings = {"components": node["components"], "backend_url": ",".join(backend_urls), "uuid": node["uuid"], "tm_opts": config["java"]["tm"], "engine_opts": config["java"]["engine"], "node_binary": config["node_binary"], "startup_timeout_seconds": config["heartbeat"]["startup_timeout_seconds"]}
            settings_file = candidate / (node["name"] + "-settings.json")
            write_json(settings_file, settings)
            volumes = [str(candidate / "bundle") + ":/bundle:ro", str(directory) + ":/tapdata", str(settings_file) + ":/tapdata/settings.json:ro", str(secret) + ":/secret/mongo-uri:ro", str(HERE / "test-environment-entrypoint.sh") + ":/bootstrap/entrypoint.sh:ro", str(HERE / "test-environment-node.js") + ":/bootstrap/node.js:ro"]
            if config["ports"][node["name"]]["license"]:
                license_path = contained(self.station / "config", config["ports"][node["name"]]["license"])
                if not license_path.is_file():
                    raise EnvironmentError("许可证文件缺失")
                volumes.append(str(license_path) + ":/license/license.txt:ro")
            services[node["name"]] = {"image": config["image"], "platform": config["platform"], "hostname": self.project + "-" + node["name"], "user": "%s:%s" % (os.getuid(), os.getgid()), "environment": {"TAPDATA_ARCH": config["platform"].split("/")[1], "HOME": "/tapdata/home", "JAVA_TOOL_OPTIONS": "-Duser.home=/tapdata/home"}, "init": True, "restart": "unless-stopped", "labels": {LABEL: self.identity}, "extra_hosts": ["host.docker.internal:host-gateway"], "entrypoint": ["/bin/bash", "/bootstrap/entrypoint.sh"], "volumes": volumes, "ports": ["127.0.0.1:%s:%s" % (config["ports"][node["name"]][key], internal) for key, internal in (("tm", 3030), ("api", 3080)) if config["ports"][node["name"]][key] is not None], "stop_grace_period": "45s", "healthcheck": {"test": ["CMD", "/bundle/" + config["node_binary"], "/bootstrap/node.js", "health"], "interval": "10s", "timeout": "5s", "start_period": "%ss" % config["heartbeat"]["startup_timeout_seconds"], "retries": 3}}
        for node in nodes:
            if "TM" not in node["components"]:
                services[node["name"]]["depends_on"] = {n: {"condition": "service_healthy"} for n in tm_names}
        write_json(candidate / "compose.json", {"services": services, "networks": {"default": {"labels": {LABEL: self.identity}}}})
        state = {"station_id": self.identity, "environment": name, "config_digest": digest(config), "secret_digest": digest(self.secret(config)[1]), "config": config, "build": candidate.name, "artifact_digest": record["artifact_digest"], "nodes": nodes, "phase": "prepared"}
        return state

    def status(self):
        state = self.state()
        containers = self.docker_resources()
        if not state:
            if containers or self.network_ids:
                raise EnvironmentError("存在未登记的工位容器，停止自动操作")
            return {"active": None, "ready": False, "containers": []}
        names = [i["Config"]["Labels"].get("com.docker.compose.service") for i in containers]
        expected = [n["name"] for n in state["nodes"]]
        if len(names) != len(set(names)) or any(n not in expected for n in names):
            raise EnvironmentError("节点资源与运行记录不一致")
        reports = []
        for info in containers:
            service = info["Config"]["Labels"]["com.docker.compose.service"]
            report = {"node": service, "expected_components": state["config"]["nodes"][service], "running": info["State"]["Running"], "health": info["State"].get("Health", {}).get("Status", "unknown")}
            if report["running"]:
                try:
                    observation = json.loads(call(["docker", "exec", info["Id"], "cat", "/tapdata/health.json"]))
                    report["components"] = observation["components"]
                    observed = observation["components"]
                    selected = state["config"]["nodes"][service]
                    mapping = {"TM": "tm", "FE": "engine", "APIServer": "api"}
                    report["components_ready"] = observed.get("launcher") is True and all(observed.get(field) is (role in selected) for role, field in mapping.items()) and all(observed.get(field) is True for role, field in (("TM", "tm_port"), ("APIServer", "api_port")) if role in selected)
                    report["observation_fresh"] = 0 <= time.time() * 1000 - observation["time"] <= 30000
                except (EnvironmentError, ValueError, KeyError):
                    report["observation_fresh"] = False
            reports.append(report)
        monitoring = {"verified": False, "nodes": []}
        if len(containers) == len(expected) and all(r["running"] for r in reports):
            try:
                monitoring = self.probe(state["config"], self.root / "builds" / state["build"] / "bundle", state["nodes"])
            except (EnvironmentError, subprocess.TimeoutExpired):
                monitoring = {"verified": False, "nodes": [], "reason": "MongoDB/监控事实未核验"}
        ready = len(reports) == len(expected) and all(r["running"] and r["health"] == "healthy" and r.get("observation_fresh", False) and r.get("components_ready", False) for r in reports) and monitoring.get("verified", False)
        result = {"active": state["environment"], "phase": state["phase"], "ready": ready, "containers": reports, "monitoring": monitoring, "artifact_digest": state["artifact_digest"]}
        try:
            _, config = self.config(state["environment"])
            result["configuration_changed"] = (digest(config) != state["config_digest"] or digest(self.secret(config)[1]) != state["secret_digest"])
        except (ValueError, OSError):
            result["configuration_changed"] = True
        return result

    def start(self, state):
        state["phase"] = "starting"
        write_json(self.root / "active.json", state)
        try:
            self.compose("up", "-d", "--remove-orphans", timeout=state["config"]["heartbeat"]["startup_timeout_seconds"] + 300)
            deadline = time.monotonic() + state["config"]["heartbeat"]["startup_timeout_seconds"]
            while time.monotonic() < deadline:
                result = self.status()
                if result["ready"]:
                    state["phase"] = "ready"
                    write_json(self.root / "active.json", state)
                    result["phase"] = "ready"
                    return result
                time.sleep(5)
            raise EnvironmentError("启动尚未通过组件与监控验收，现场保留，可用 status 回读")
        except BaseException:
            state["phase"] = "needs-attention"
            write_json(self.root / "active.json", state)
            raise

    def remove(self, erase=True):
        state = self.state()
        containers = self.docker_resources()
        if not state:
            if containers or self.network_ids:
                raise EnvironmentError("未登记现场需先核验，不自动删除")
            if self.root.exists():
                owner = self.root / "owner.json"
                if not owner.is_file() or read_json(owner).get("station_id") != self.identity:
                    raise EnvironmentError("未登记现场需先核验，不自动删除")
                archive = self.station / "runtime/tapdata-test-env-diagnostics" / uuid.uuid4().hex
                archive.mkdir(parents=True, mode=0o700)
                for record in self.root.glob("builds/*/build-record.json"):
                    shutil.copy2(record, archive / (record.parent.name + "-build-record.json"))
                shutil.rmtree(self.root)
            return
        # 日志只保存为私有诊断文件，不输出或提交。
        archive = self.station / "runtime/tapdata-test-env-diagnostics" / uuid.uuid4().hex
        archive.mkdir(parents=True, mode=0o700)
        write_json(archive / "summary.json", {"environment": state["environment"], "artifact_digest": state["artifact_digest"], "nodes": state["nodes"], "phase": state["phase"]})
        try:
            log = self.compose("logs", "--no-color", "--tail", "200")
            path = archive / "containers.log"
            path.write_text(log)
            path.chmod(0o600)
        except EnvironmentError:
            pass
        for node in state["nodes"]:
            work = self.root / "nodes" / node["name"] / "work"
            for source in (work / "logs", work / "launcher-start.log"):
                if not source.exists():
                    continue
                if source.is_symlink() or (source.is_dir() and any(p.is_symlink() for p in source.rglob("*"))):
                    raise EnvironmentError("诊断日志含链接，保全范围需核验")
                target = archive / node["name"] / source.name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
                for item in [target, *target.rglob("*")] if target.is_dir() else [target]:
                    item.chmod(0o700 if item.is_dir() else 0o600)
        state["phase"] = "stopping"
        write_json(self.root / "active.json", state)
        self.compose("down", "--remove-orphans")
        if self.docker_resources():
            raise EnvironmentError("容器清理未完成，保留运行现场")
        networks = call(["docker", "network", "ls", "--filter", "label=com.docker.compose.project=" + self.project, "--format", "{{.ID}}"])
        if networks.strip():
            raise EnvironmentError("网络清理未完成，保留运行现场")
        state["phase"] = "stopped"
        write_json(self.root / "active.json", state)
        if erase:
            shutil.rmtree(self.root)

    def deploy(self, name=None, update=False, switch=False):
        state = self.state()
        resources = self.docker_resources()
        if (resources or self.network_ids) and not state:
            raise EnvironmentError("存在未登记容器，不能部署第二个环境")
        if update and not state:
            raise EnvironmentError("没有当前环境可更新")
        name, config = self.config(name or (state["environment"] if update and state else None))
        if state and state["environment"] != name and not switch:
            raise EnvironmentError("当前运行 " + state["environment"] + "；切换需显式 --switch")
        if update and state["environment"] != name:
            raise EnvironmentError("update 只更新当前环境；换配置请 deploy --switch")
        if state and state["environment"] == name and not update:
            result = self.status()
            if (digest(config) != state["config_digest"] or digest(self.secret(config)[1]) != state["secret_digest"]):
                raise EnvironmentError("配置已变化，请使用 update")
            if not result["ready"]:
                raise EnvironmentError("已有环境未就绪；请检查现场或 update，不自动重建")
            return result
        # 许可证也在停止旧环境之前检查。
        for port in config["ports"].values():
            if port["license"] and not contained(self.station / "config", port["license"]).is_file():
                raise EnvironmentError("许可证文件缺失")
        candidate, record = self.build(config)
        generated = self.generate(name, config, candidate, record, previous=state if update else None)
        call(["docker", "compose", "-p", self.project, "-f", str(candidate / "compose.json"), "config", "--quiet"])
        owned_ports = set()
        for info in self.docker_resources():
            for bindings in info.get("NetworkSettings", {}).get("Ports", {}).values():
                for binding in bindings or []:
                    owned_ports.add(int(binding["HostPort"]))
        for node_ports in config["ports"].values():
            for port in (node_ports["tm"], node_ports["api"]):
                if port is not None and port not in owned_ports:
                    try:
                        with socket.socket() as listener:
                            listener.bind(("127.0.0.1", port))
                    except PermissionError:
                        raise EnvironmentError("缺少宿主机端口探测权限，原环境未变")
                    except OSError:
                        raise EnvironmentError("目标宿主机端口已被占用，原环境未变")
        self.docker_resources()  # 构建耗时后再次核验归属。
        if state:
            self.remove(erase=False)
            if switch and state["environment"] != name:
                shutil.rmtree(self.root / "nodes")
        if update and state:
            for node in state["nodes"]:
                if node["name"] not in config["nodes"]:
                    path = self.root / "nodes" / node["name"]
                    if path.exists():
                        shutil.rmtree(path)
        for node in generated["nodes"]:
            (self.root / "nodes" / node["name"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(candidate / "compose.json", self.root / "compose.json")
        write_json(self.root / "active.json", generated)
        return self.start(generated)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["configure", "list", "deploy", "update", "status", "uninstall"])
    parser.add_argument("--station", required=True)
    parser.add_argument("--env")
    parser.add_argument("--config", help="configure 使用的完整 JSON 文件")
    parser.add_argument("--switch", action="store_true", help="明确切换并清理旧节点现场")
    args = parser.parse_args(argv)
    try:
        if args.switch and args.operation != "deploy":
            raise EnvironmentError("--switch 只适用于 deploy")
        if args.config and args.operation != "configure":
            raise EnvironmentError("--config 只适用于 configure")
        env = Environment(args.station)
        with env.lock():
            if args.operation == "configure":
                if not args.env or not NAME.fullmatch(args.env) or not args.config:
                    raise EnvironmentError("configure 需要 --env 名称与 --config 文件")
                config = validate(read_json(Path(args.config)))
                write_json(contained(env.config_dir, args.env + ".json"), config)
                result = {"configured": args.env, "applied": False, "build_configured": bool(config["build"] and config["assets"])}
            elif args.operation == "list":
                try:
                    runtime = env.status()
                except (EnvironmentError, OSError, subprocess.TimeoutExpired):
                    runtime = {"verified": False, "ready": False, "reason": "Docker 或环境状态无法核验"}
                result = {"configurations": env.names(), "runtime": runtime}
            elif args.operation == "status":
                result = env.status()
            elif args.operation == "uninstall":
                state = env.state()
                if args.env and state and state["environment"] != args.env:
                    raise EnvironmentError("--env 与当前环境不一致")
                env.remove()
                result = {"uninstalled": True, "configurations": env.names(), "external_mongo": "preserved"}
            else:
                result = env.deploy(args.env, update=args.operation == "update", switch=args.switch)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError, subprocess.TimeoutExpired):
        # 路径和低层异常也可能包含秘密；只输出已审查的业务异常文本。
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, EnvironmentError) else "配置、状态或工具读取失败；现场保留，请核验输入"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
