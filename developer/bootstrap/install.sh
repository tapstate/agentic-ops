#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BRANCH="main"
REPO_URL="git@github.com:tapstate/agentic-ops.git"
GITHUB_REPOSITORY="tapstate/agentic-ops"

if [ -n "${AGENTIC_OPS_TEST_MODE:-}" ] || \
  [ -n "${AGENTIC_OPS_TEST_LAUNCHER:-}" ] || \
  [ -n "${AGENTIC_OPS_TEST_EXPECTED_REPOSITORY:-}" ] || \
  [ -n "${AGENTIC_OPS_REPO_URL:-}" ] || \
  [ -n "${AGENTIC_OPS_GITHUB_REPOSITORY:-}" ] || \
  [ -n "${AGENTIC_OPS_BRANCH:-}" ]; then
  printf 'AgenticOps：安装身份固定为 tapstate/agentic-ops 的 main，不能通过环境变量覆盖\n' >&2
  printf '{"ok":false,"operation":"bootstrap","status":"failed","code":"install_identity_override_forbidden","retry_safe":true,"message":"安装身份固定为 tapstate/agentic-ops 的 main，不能通过环境变量覆盖","required_human_action":"请移除安装身份覆盖环境变量后重试"}\n'
  exit 1
fi

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/lib/common.sh" ]; then
  . "$SCRIPT_DIR/lib/common.sh"
else
  bootstrap_common="$({
    command gh api -H 'Accept: application/vnd.github.raw' \
      "/repos/$GITHUB_REPOSITORY/contents/developer/bootstrap/lib/common.sh?ref=$BRANCH"
  } 2>/dev/null)" || {
    printf 'AgenticOps：无法读取 developer Bootstrap 公共库\n' >&2
    printf '{"ok":false,"operation":"bootstrap","status":"failed","code":"bootstrap_common_unavailable","retry_safe":true,"message":"无法读取 developer Bootstrap 公共库","required_human_action":"请确认 gh 已登录且有权读取 %s"}\n' "$GITHUB_REPOSITORY"
    exit 1
  }
  eval "$bootstrap_common"
  unset bootstrap_common
fi

if [ -e "$INSTALL_DIR" ]; then
  agentic_reject_verification_mode "$INSTALL_DIR"
fi

if ! command -v git >/dev/null 2>&1; then
  agentic_bootstrap_error "git_not_found" "未找到 Git，无法安装 AgenticOps" "请先安装 Git"
fi

expected_repository="$(agentic_expected_repository)"
if ! agentic_repository_matches "$REPO_URL" "$expected_repository"; then
  agentic_bootstrap_error \
    "install_origin_mismatch" \
    "安装源不是受信 AgenticOps 仓库：${REPO_URL:-未配置}" \
    "请使用 $expected_repository"
fi
agentic_require_unrewritten_url "$REPO_URL" "安装"

if [ -L "$INSTALL_DIR" ]; then
  agentic_managed_path_error "install_root"
elif [ -e "$INSTALL_DIR/.agentic-ops-source" ] || [ -d "$INSTALL_DIR/maintainer" ]; then
  agentic_require_managed_clone "$INSTALL_DIR"
  agentic_bootstrap_error \
    "source_install_conflict" \
    "不能把包含 maintainer 资产的源头仓库原地转换为 developer 安装" \
    "请使用独立的 AGENTIC_OPS_HOME 安装目录"
elif [ ! -e "$INSTALL_DIR" ]; then
  git clone --filter=blob:none --no-checkout --branch "$BRANCH" --single-branch \
    "$REPO_URL" "$INSTALL_DIR"
  if ! git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"; then
    agentic_bootstrap_error \
      "install_origin_normalization_failed" \
      "无法把 managed clone origin 固定为受信 AgenticOps 仓库" \
      "请人工移除本次未完成安装目录后重试"
  fi
elif [ ! -e "$INSTALL_DIR/.git" ]; then
  agentic_bootstrap_error \
    "install_dir_conflict" \
    "安装目录已存在且不是 AgenticOps managed clone：$INSTALL_DIR" \
    "请更换 AGENTIC_OPS_HOME，或人工处理冲突目录"
else
  agentic_require_managed_clone "$INSTALL_DIR"
  agentic_require_checkout_integrity "$INSTALL_DIR"
  current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  target_ref="$(git -C "$INSTALL_DIR" rev-parse "origin/$BRANCH")"
  if [ "$current_ref" != "$target_ref" ]; then
    agentic_bootstrap_error \
      "install_update_required" \
      "现有 developer 安装不是 origin/$BRANCH 最新版本" \
      "请运行 $INSTALL_DIR/developer/bootstrap/update.sh 并确认目标 ref"
  fi
fi

agentic_configure_developer_checkout "$INSTALL_DIR"
git -C "$INSTALL_DIR" checkout "$BRANCH"
agentic_require_managed_paths_safe "$INSTALL_DIR"
agentic_verify_developer_checkout "$INSTALL_DIR"

uv_bin="$(agentic_find_uv)"
current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
previous_ref=""
if [ -f "$INSTALL_DIR/.local/current-ref" ]; then
  previous_ref="$(cat "$INSTALL_DIR/.local/current-ref")"
fi

agentic_sync_runtime "$INSTALL_DIR" "$uv_bin"
agentic_write_refs "$INSTALL_DIR" "$previous_ref" "$current_ref"
agentic_require_checkout_integrity "$INSTALL_DIR"
agentic_configure_path "$INSTALL_DIR"

agentic_bootstrap_json_success bootstrap_install \
  install_dir "$INSTALL_DIR" current_ref "$current_ref" python "3.12"
