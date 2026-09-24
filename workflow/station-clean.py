#!/usr/bin/env python3
"""工位清理命令。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.station_clean import cli

if __name__ == "__main__":
    try:
        sys.exit(cli())
    except (ValueError, OSError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        sys.exit(2)
