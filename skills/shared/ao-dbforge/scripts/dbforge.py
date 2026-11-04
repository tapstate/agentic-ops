"""下载式 DBForge 接线；仅用 token，复用原生 CLI 与 IT 配置入口。"""
from __future__ import annotations

if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[4] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_URL = "http://192.168.1.200:30880"
PUBLIC_KEY = "c94b68e5411aaf1877c2f984c042d364ac1aacfaa0d92d62227fe129af01fbbe"
COMMANDS = {"capabilities", "list", "create", "describe", "renewal", "destroy"}
FORBIDDEN = {"url", "token", "admin-key", "config", "o", "output", "insecure", "request-id", "idem-key"}


class Error(ValueError):
    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise Error("输出路径不能是符号链接")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dbforge-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configuration(path, override=None):
    path = Path(path).expanduser().absolute()
    if not path.exists() and not path.is_symlink():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                stream.write("# AgenticOps 只使用 token；请在本机填写，不在聊天中发送凭据。\n"
                             f"url = {DEFAULT_URL}\ntoken =\nadmin_key =\n")
        except FileExistsError:
            pass
    if not path.is_file() or path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise Error("DBForge 配置须为权限 0600 的普通文件；原文件保留")
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith(("#", ";")):
            key, value = line.split("=", 1)
            if key.strip() in ("url", "token"):
                values[key.strip()] = value.strip().strip("\"'")
    url = (override or values.get("url") or DEFAULT_URL).rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in url)):
        raise Error("DBForge 地址须为无凭据的 HTTP(S) 服务根地址")
    token = values.get("token", "")
    if any(ord(c) < 32 for c in token):
        raise Error("token 格式无效")
    return {"url": url, "token": token, "config": str(path)}


def clean_environment():
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("DBFORGE_CLI_", "CONNECTOR_IT_DBFORGE_"))
            and k != "CONNECTOR_IT_CONFIG_SOURCE"}


@contextlib.contextmanager
def tenant_config(config):
    with tempfile.TemporaryDirectory(prefix="ao-dbforge-") as directory:
        path = Path(directory) / "config"
        private_write(path, f"url = {config['url']}\ntoken = {config['token']}\n")
        yield path


def native(binary, config, arguments, timeout=960):
    with tenant_config(config) as path:
        try:
            result = subprocess.run([str(binary), "-config", str(path), *arguments],
                                    env=clean_environment(), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Error("DBForge 请求超时，结果未知；先按原请求回读，不能直接重建或回退") from None
    if result.returncode:
        raise Error(f"DBForge CLI 未成功（退出码 {result.returncode}）；保留原请求，先核验凭据、能力与实际结果",
                    {"stdout": result.stdout, "stderr": result.stderr})
    return result.stdout


def version(binary):
    result = subprocess.run([str(binary), "version"], capture_output=True, text=True,
                            env=clean_environment(), timeout=10)
    if result.returncode or not result.stdout.startswith("dbforge-cli "):
        raise Error("CLI 版本核验失败")
    return result.stdout.strip()


def install(directory, config, update=False, if_installed=False):
    directory = Path(directory)
    binary = directory / "dbforge-cli"
    if if_installed and not binary.exists():
        return {"status": "not_installed", "message": "首次使用 DBForge 时安装"}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or binary.is_symlink():
        raise Error("工具安装路径不能是符号链接")
    with (directory / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "message": "另一调用正在准备 CLI；本次保留现有版本"}
        if binary.exists() and not update:
            return {"status": "available", "version": version(binary)}
        before = binary.read_bytes() if binary.exists() else None
        try:
            if update and before:
                native(binary, config, ["update"], timeout=150)
            else:
                target_os = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
                arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "AMD64": "amd64"}.get(platform.machine())
                if not target_os or not arch:
                    raise Error("仅支持 macOS/Linux 的 arm64/amd64 CLI")
                openssl = next((p for p in (shutil.which("openssl"),
                    "/opt/homebrew/opt/openssl@3/bin/openssl", "/usr/local/opt/openssl@3/bin/openssl")
                    if p and Path(p).is_file() and "OpenSSL 3." in subprocess.run(
                        [p, "version"], capture_output=True, text=True, timeout=10).stdout), None)
                if not openssl:
                    raise Error("首次安装验签需要已安装的 OpenSSL 3；未安装任何依赖")
                with tempfile.TemporaryDirectory(dir=directory) as temporary:
                    stage = Path(temporary)
                    endpoint = f"{config['url']}/cli/{target_os}/{arch}"
                    for suffix, name, limit in (("", "binary", 128 << 20), ("/signature", "signature.hex", 1024)):
                        with urllib.request.urlopen(endpoint + suffix, timeout=30) as response:
                            data = response.read(limit + 1)
                        if not data or len(data) > limit:
                            raise Error("CLI 制品或签名大小无效")
                        (stage / name).write_bytes(data)
                    (stage / "signature").write_bytes(bytes.fromhex((stage / "signature.hex").read_text().strip()))
                    (stage / "key.der").write_bytes(bytes.fromhex("302a300506032b6570032100" + PUBLIC_KEY))
                    checked = subprocess.run([openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(stage / "key.der"),
                        "-keyform", "DER", "-rawin", "-in", str(stage / "binary"), "-sigfile", str(stage / "signature")],
                        capture_output=True, timeout=15)
                    if checked.returncode:
                        raise Error("CLI 签名验证失败；未安装下载内容")
                    (stage / "binary").chmod(0o700)
                    version(stage / "binary")
                    os.replace(stage / "binary", binary)
            return {"status": "updated" if before else "installed", "version": version(binary),
                    "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        except (OSError, ValueError, subprocess.SubprocessError):
            if before is None:
                raise Error("CLI 安装未完成；核验下载地址、签名和运行平台后重试") from None
            # 原生更新完成但后续核验失败时也恢复旧工具；配置从不参与替换。
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                stream.write(before)
                backup = Path(stream.name)
            backup.chmod(0o700)
            os.replace(backup, binary)
            return {"status": "update_failed", "version": version(binary), "message": "更新失败，已保留原可用 CLI；继续无依赖工作"}


def public(value):
    if isinstance(value, dict):
        return {k: ("<已隐藏>" if k.lower() in {"password", "token", "admin_key", "uri", "jdbc_url", "jdbcurl", "env_vars"}
                    else public(v)) for k, v in value.items()}
    return [public(v) for v in value] if isinstance(value, list) else value


def invoke(binary, config, arguments, record_path=None):
    if not record_path:
        return _invoke(binary, config, arguments)
    path = Path(record_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 锁文件不替换或删除，覆盖读取、首次写入和远端调用，避免并发生成不同幂等键。
    fd = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error("同一租约记录正在使用；保留原请求，等待该调用结束后回读") from None
        return _invoke(binary, config, arguments, record_path)


def _invoke(binary, config, arguments, record_path=None):
    if not config["token"]:
        raise Error(f"请在本机填写 {config['config']} 中的 token，再重试；不需要 admin_key")
    if not arguments or arguments[0] not in COMMANDS:
        raise Error("仅支持 capabilities/list/create/describe/renewal/destroy")
    if any(a.startswith("-") and a.lstrip("-").split("=", 1)[0] in FORBIDDEN for a in arguments):
        raise Error("调用参数不能覆盖服务、凭据、输出或恢复身份")
    action, flags = arguments[0], arguments[1:]
    record = json.loads(Path(record_path).read_text()) if record_path and Path(record_path).exists() else None
    if record and (record["url"] != config["url"] or record["config"] != config["config"]):
        raise Error("配置来源或服务地址已变化；先核实原租约，原记录保留")
    if record and not record.get("lease_id") and action in ("describe", "renewal", "destroy"):
        found = json.loads(native(binary, config, ["list", "-o", "json", "-idem-key", record["idempotency_key"]]))
        leases = found.get("leases", [])
        if len(leases) != 1:
            raise Error("原请求尚无唯一租约回读；保留线索，不能猜测释放对象")
        record.update(lease_id=leases[0]["lease_id"], status=leases[0]["status"])
        private_write(record_path, record)
    if action == "create":
        if not record_path:
            raise Error("create 需要 --record 保存原请求与租约恢复材料")
        if record:
            if record["arguments"] != flags:
                raise Error("已有请求与本次参数不同；先核实原请求")
            found = json.loads(native(binary, config, ["list", "-o", "json", "-idem-key", record["idempotency_key"]]))
            leases = found.get("leases", [])
            if len(leases) == 1:
                record.update(lease_id=leases[0]["lease_id"], status=leases[0]["status"])
                private_write(record_path, record)
            if len(leases) != 1 or leases[0].get("status") != "active":
                raise Error("原创建结果尚未确认有效；保留请求并回读，不能盲目重建")
            result = json.loads(native(binary, config, ["describe", "-o", "json", leases[0]["lease_id"]]))
        else:
            record = {"url": config["url"], "config": config["config"], "arguments": flags,
                      "idempotency_key": "ao-" + uuid.uuid4().hex, "status": "pending"}
            private_write(record_path, record)
            try:
                result = json.loads(native(binary, config, ["create", "-o", "json", "-idem-key", record["idempotency_key"], *flags]))
            except Error as error:
                if error.diagnostic:
                    record["last_error"] = error.diagnostic
                    private_write(record_path, record)
                raise
        record.update(lease_id=result["lease_id"], status=result["status"], response=result)
    else:
        if action in ("renewal", "destroy") and not record:
            raise Error("续期和释放需要 --record 指向本次创建记录")
        if action in ("describe", "renewal", "destroy") and record:
            valid_flags = (not flags if action != "renewal" else
                           len(flags) == 2 and flags[0] == "-extend" and flags[1].isdigit())
            if not valid_flags:
                raise Error("使用 record 时租约 ID 由记录提供；续期仅接受 -extend <分钟>")
            flags = [*flags, record["lease_id"]]
        if action == "destroy":
            flags = ["-yes", *flags]
        result = json.loads(native(binary, config, [action, "-o", "json", *flags]))
        if record:
            record["last_result"] = result
            if action == "destroy":
                record["status"] = result["status"]
    if record_path and record:
        private_write(record_path, record)
    return public(result)


def overrides_it_configuration(arguments):
    for index, argument in enumerate(arguments):
        if argument in ("-D", "--define"):
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
        elif argument.startswith("-D"):
            value = argument[2:].lstrip()
        elif argument.startswith("--define="):
            value = argument.partition("=")[2]
        else:
            continue
        name = value.partition("=")[0]
        if name == "connector.it.config.source" or name.startswith("connector.it.dbforge."):
            return True
    return False


def run_it(config, source, command, cwd, log):
    if not command:
        raise Error("IT 需要明确的原生测试命令")
    inherited_options = [os.environ.get(key, "") for key in
                         ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "MAVEN_OPTS", "MAVEN_ARGS")]
    if (overrides_it_configuration(command)
            or any(overrides_it_configuration(shlex.split(value)) for value in inherited_options)):
        raise Error("IT 命令不能覆盖已选配置来源或 DBForge 凭据；请使用 --source 与配置文件")
    env = clean_environment()
    env["CONNECTOR_IT_CONFIG_SOURCE"] = source
    if source == "dbforge":
        if not config["token"]:
            raise Error("显式选择 DBForge IT 时需要填写 token")
        env.update(CONNECTOR_IT_DBFORGE_URL=config["url"], CONNECTOR_IT_DBFORGE_TOKEN=config["token"],
                   CONNECTOR_IT_DBFORGE_ENDPOINT_SCOPE="external", CONNECTOR_IT_DBFORGE_TTL_MINUTES="60",
                   CONNECTOR_IT_DBFORGE_OWNER="agenticops-it-" + uuid.uuid4().hex)
    private_write(log, "")
    with Path(log).open("w") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    return {"source": source, "exit_code": result.returncode, "log": str(Path(log).absolute()),
            "message": "按实际报告核验用例；不自动回退或重跑"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path.home() / ".dbforge"))
    parser.add_argument("--url", help="显式服务地址；不修改默认配置")
    parser.add_argument("--cli-dir", default=str(ROOT / ".local/tools/dbforge"))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare", help="首次安装并准备默认配置")
    update = sub.add_parser("update", help="原生 CLI 更新，失败保留旧版")
    update.add_argument("--if-installed", action="store_true")
    cli = sub.add_parser("cli", help="仅使用 token 调用原生租约命令")
    cli.add_argument("--record", help="权限受限的创建请求与租约记录")
    cli.add_argument("arguments", nargs=argparse.REMAINDER)
    it = sub.add_parser("it", help="显式选择 IT 配置来源后执行原生命令")
    it.add_argument("--source", choices=("json", "dbforge"), default="json")
    it.add_argument("--cwd", required=True)
    it.add_argument("--log", required=True)
    it.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == "update" and args.if_installed and not (Path(args.cli_dir) / "dbforge-cli").exists():
            result = {"status": "not_installed", "message": "首次使用 DBForge 时安装"}
        else:
            config = ({"url": "", "token": ""} if args.action == "it" and args.source == "json"
                      else configuration(args.config, args.url))
            if args.action in ("prepare", "update"):
                result = install(args.cli_dir, config, update=args.action == "update")
                result.update(config=config["config"], token_ready=bool(config["token"]))
            else:
                command = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
                if args.action == "it":
                    result = run_it(config, args.source, command, args.cwd, args.log)
                else:
                    install(args.cli_dir, config)
                    result = invoke(Path(args.cli_dir) / "dbforge-cli", config, command, args.record)
        print(json.dumps(result, ensure_ascii=False))
        return result.get("exit_code", 0) if isinstance(result, dict) else 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, Error) else "DBForge 操作未完成；保留配置与恢复材料，核验输入后接续"
        print(json.dumps({"status": "incomplete", "message": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
