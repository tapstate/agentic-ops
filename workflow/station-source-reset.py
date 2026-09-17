#!/usr/bin/env python3
"""受同一清理操作约束的源码复位入口。"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.station_source_reset import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--operation-id", required=True)
    args = parser.parse_args()
    try:
        run(args.dir, args.issue_key, args.expected_run_id, args.expected_revision, args.operation_id)
    except (ValueError, OSError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
