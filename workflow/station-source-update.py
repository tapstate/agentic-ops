#!/usr/bin/env python3
"""空闲工位源码刷新入口。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.station_source_update import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print("错误：" + str(error), file=sys.stderr)
        sys.exit(2)
