#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SOURCE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"

. "$SOURCE_ROOT/developer/bootstrap/lib/common.sh"
agentic_reject_verification_mode "$INSTALL_DIR"
agentic_reject_identity_overrides
agentic_require_managed_clone "$INSTALL_DIR"
agentic_configure_developer_checkout "$INSTALL_DIR"
agentic_verify_developer_checkout "$INSTALL_DIR"
agentic_require_checkout_integrity "$INSTALL_DIR" "allow-pending-rollback"
uv_bin="$(agentic_find_uv)"

ref_name="previous-ref"
if [ -f "$INSTALL_DIR/.local/pending-rollback-ref" ]; then
  ref_name="pending-rollback-ref"
fi
agentic_require_safe_ref_file "$INSTALL_DIR" "$ref_name"

current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
rollback_ref="$(cat "$INSTALL_DIR/.local/$ref_name")"
agentic_require_rollback_commit "$INSTALL_DIR" "$rollback_ref"
git -C "$INSTALL_DIR" checkout --detach "$rollback_ref"
[ "$(git -C "$INSTALL_DIR" rev-parse HEAD)" = "$rollback_ref" ] || \
  agentic_bootstrap_error \
    "rollback_ref_invalid" \
    "回滚 checkout 没有落到已验证的 commit" \
    "请停止使用该目录并重新安装"
agentic_sync_runtime "$INSTALL_DIR" "$uv_bin"
agentic_write_refs "$INSTALL_DIR" "$current_ref" "$rollback_ref"
agentic_remove_ref "$INSTALL_DIR" pending-rollback-ref
agentic_require_checkout_integrity "$INSTALL_DIR"

agentic_bootstrap_json_success bootstrap_rollback \
  previous_ref "$current_ref" current_ref "$rollback_ref"
