#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"

. "$SCRIPT_DIR/lib/common.sh"
agentic_require_managed_clone "$INSTALL_DIR"
uv_bin="$(agentic_find_uv)"

ref_file="$INSTALL_DIR/.local/previous-ref"
if [ -f "$INSTALL_DIR/.local/pending-rollback-ref" ]; then
  ref_file="$INSTALL_DIR/.local/pending-rollback-ref"
fi
if [ ! -s "$ref_file" ]; then
  agentic_bootstrap_error \
    "rollback_ref_missing" \
    "没有可用的上一版本引用" \
    "请检查 .local 安装状态或重新安装"
fi

current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
rollback_ref="$(sed -n '1p' "$ref_file")"
git -C "$INSTALL_DIR" checkout --detach "$rollback_ref"
agentic_sync_runtime "$INSTALL_DIR" "$uv_bin"
agentic_write_refs "$INSTALL_DIR" "$current_ref" "$rollback_ref"
rm -f "$INSTALL_DIR/.local/pending-rollback-ref"

printf '{"ok":true,"operation":"bootstrap_rollback","status":"completed","retry_safe":true,"previous_ref":"%s","current_ref":"%s"}\n' \
  "$current_ref" "$rollback_ref"
