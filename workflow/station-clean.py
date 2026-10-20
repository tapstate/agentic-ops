#!/usr/bin/env python3
"""工位清理命令。"""

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.station_clean import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        sys.exit(2)
