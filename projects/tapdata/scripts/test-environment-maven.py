#!/usr/bin/env python3
"""执行项目 Maven 子步骤；隔离路径由 Agent 通过项目指引核验，不读写任务状态。"""

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[3] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))

import argparse
import os
from pathlib import Path
import subprocess


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maven", required=True)
    parser.add_argument("--java-home", required=True)
    parser.add_argument("--local-repository", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    values = (args.maven, args.java_home, args.local_repository)
    if not all(Path(value).is_absolute() for value in values):
        parser.error("Maven、Java Home 和本地仓库必须为核验后的绝对路径")
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    if not arguments or any(argument.startswith("-Dmaven.repo.local") for argument in arguments):
        parser.error("提供 Maven 子命令，隔离仓库只由 --local-repository 指定")
    env = os.environ.copy()
    env["JAVA_HOME"] = args.java_home
    return subprocess.call([args.maven, "-Dmaven.repo.local=" + args.local_repository] + arguments, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
