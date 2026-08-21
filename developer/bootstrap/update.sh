#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SOURCE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BRANCH="main"

. "$SOURCE_ROOT/developer/bootstrap/lib/common.sh"
agentic_reject_verification_mode "$INSTALL_DIR"
agentic_reject_identity_overrides
agentic_require_managed_clone "$INSTALL_DIR"
agentic_configure_developer_checkout "$INSTALL_DIR"
agentic_verify_developer_checkout "$INSTALL_DIR"
agentic_require_checkout_integrity "$INSTALL_DIR"
uv_bin="$(agentic_find_uv)"
previous_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"

# ao-work auth 的 installation 模式会把安装专属 SSH 固化为本仓库本地
# core.sshCommand。这里遵循该受管配置执行 fetch，不解析、不覆盖或回退授权。

git -C "$INSTALL_DIR" fetch origin "$BRANCH"
target_ref="$(git -C "$INSTALL_DIR" rev-parse "origin/$BRANCH")"
agentic_confirm_update "$previous_ref" "$target_ref"
git -C "$INSTALL_DIR" merge --ff-only "origin/$BRANCH"
current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"

if ! agentic_sync_runtime "$INSTALL_DIR" "$uv_bin"; then
  agentic_write_ref_atomic "$INSTALL_DIR" pending-rollback-ref "$previous_ref"
  agentic_bootstrap_error \
    "runtime_update_failed" \
    "代码已更新但 Python Runtime 自检失败" \
    "请执行 developer/bootstrap/rollback.sh 回退到上一提交"
fi
agentic_write_refs "$INSTALL_DIR" "$previous_ref" "$current_ref"
agentic_remove_ref "$INSTALL_DIR" pending-rollback-ref
agentic_require_checkout_integrity "$INSTALL_DIR"

agentic_bootstrap_json_success bootstrap_update \
  previous_ref "$previous_ref" current_ref "$current_ref"
