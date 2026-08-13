#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SOURCE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
REPO_URL="${AGENTIC_OPS_REPO_URL:-git@github.com:tapstate/agentic-ops.git}"
BRANCH="${AGENTIC_OPS_BRANCH:-main}"

. "$SCRIPT_DIR/lib/common.sh"

if ! command -v git >/dev/null 2>&1; then
  agentic_bootstrap_error "git_not_found" "未找到 Git，无法安装 AgenticOps" "请先安装 Git"
fi

if [ "$INSTALL_DIR" = "$SOURCE_ROOT" ]; then
  agentic_require_managed_clone "$INSTALL_DIR"
elif [ ! -e "$INSTALL_DIR" ]; then
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"
elif [ ! -e "$INSTALL_DIR/.git" ]; then
  agentic_bootstrap_error \
    "install_dir_conflict" \
    "安装目录已存在且不是 AgenticOps managed clone：$INSTALL_DIR" \
    "请更换 AGENTIC_OPS_HOME，或人工处理冲突目录"
fi

uv_bin="$(agentic_find_uv)"
current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
previous_ref=""
if [ -f "$INSTALL_DIR/.local/current-ref" ]; then
  previous_ref="$(sed -n '1p' "$INSTALL_DIR/.local/current-ref")"
fi

agentic_sync_runtime "$INSTALL_DIR" "$uv_bin"
agentic_write_refs "$INSTALL_DIR" "$previous_ref" "$current_ref"

printf '{"ok":true,"operation":"bootstrap_install","status":"completed","retry_safe":true,"install_dir":"%s","current_ref":"%s","python":"3.12"}\n' \
  "$INSTALL_DIR" "$current_ref"
