#!/usr/bin/env bash
set -euo pipefail

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT

HOME="$tmp_home" bash scripts/init.sh > "$tmp_home/out.json"

grep '"ok":true' "$tmp_home/out.json"
test -x "$tmp_home/.agentic-ops/bin/agent-task-ops"
