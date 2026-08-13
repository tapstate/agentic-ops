#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BRANCH="${AGENTIC_OPS_BRANCH:-main}"

. "$SCRIPT_DIR/lib/common.sh"
agentic_require_managed_clone "$INSTALL_DIR"
uv_bin="$(agentic_find_uv)"
previous_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"

git -C "$INSTALL_DIR" fetch origin "$BRANCH"
git -C "$INSTALL_DIR" merge --ff-only "origin/$BRANCH"
current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"

if ! agentic_sync_runtime "$INSTALL_DIR" "$uv_bin"; then
  printf '%s\n' "$previous_ref" > "$INSTALL_DIR/.local/pending-rollback-ref"
  agentic_bootstrap_error \
    "runtime_update_failed" \
    "代码已更新但 Python Runtime 自检失败" \
    "请执行 bootstrap/rollback.sh 回退到上一提交"
fi
agentic_write_refs "$INSTALL_DIR" "$previous_ref" "$current_ref"
rm -f "$INSTALL_DIR/.local/pending-rollback-ref"

printf '{"ok":true,"operation":"bootstrap_update","status":"completed","retry_safe":true,"previous_ref":"%s","current_ref":"%s"}\n' \
  "$previous_ref" "$current_ref"
