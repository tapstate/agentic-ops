#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BRANCH="main"
REPO_URL="git@github.com:tapstate/agentic-ops.git"
GITHUB_REPOSITORY="tapstate/agentic-ops"
AUTHORIZATION_ARGS=()
AUTHORIZATION_REQUESTED=0
NEW_INSTALL=0

usage() {
  cat <<'USAGE'
用法：
  install.sh [授权参数]

可选授权参数（安装完成后原样转交 ao-work auth）：
  --agent-id <id>
  --jira-email <email>
  --git-name <name>
  --git-email <email>
  --github-login <login>
  --execution-auth-mode <global|installation>
  --confirm-replace-authorization <digest>
  --token-stdin
  --non-interactive

不传授权参数时，有终端则进入授权引导；无终端则完成安装并输出 ao-work auth 下一步。
首次 Bootstrap 下载和 clone 只能使用调用者已有的 Git/gh 启动账户；installation 授权完成后，后续更新使用 Runtime 固化的安装专属 SSH。
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent-id|--jira-email|--git-name|--git-email|--github-login|--execution-auth-mode|--confirm-replace-authorization)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        printf 'AgenticOps：授权参数缺少取值：%s\n' "$1" >&2
        exit 2
      fi
      AUTHORIZATION_ARGS+=("$1" "$2")
      AUTHORIZATION_REQUESTED=1
      shift 2
      ;;
    --token-stdin)
      AUTHORIZATION_ARGS+=("$1")
      AUTHORIZATION_REQUESTED=1
      shift
      ;;
    --non-interactive)
      AUTHORIZATION_ARGS+=("$1")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'AgenticOps：不支持的安装参数：%s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

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

printf 'AgenticOps：首次 Bootstrap 下载和 clone 使用调用者当前 Git/gh 启动账户；安装级授权尚未创建，不会静默替换机器已有授权\n' >&2

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
  NEW_INSTALL=1
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
if [ "$NEW_INSTALL" -eq 1 ]; then
  agentic_write_installation_metadata "$INSTALL_DIR"
fi
agentic_require_checkout_integrity "$INSTALL_DIR"
agentic_configure_path "$INSTALL_DIR"

# 研发员级配置目录（D-048）：确保 user/ 与 config.yaml 存在；source_pool_root
# 未配置时不强制引导（workspace init 会检测并阻断 source_pool_root_invalid）。
user_dir="$INSTALL_DIR/user"
if [ ! -e "$user_dir" ]; then
  mkdir -m 0700 "$user_dir"
elif [ ! -d "$user_dir" ] || [ -L "$user_dir" ]; then
  agentic_bootstrap_error \
    "install_user_dir_invalid" \
    "安装用户目录不是安全普通目录：$user_dir" \
    "请人工核对已有授权；Bootstrap 不会覆盖该路径"
fi
if [ ! -f "$user_dir/config.yaml" ]; then
  : > "$user_dir/config.yaml"
fi

authorization_status="pending"
if [ "$AUTHORIZATION_REQUESTED" -eq 1 ]; then
  if ! "$INSTALL_DIR/bin/ao-work" auth "${AUTHORIZATION_ARGS[@]}"; then
    printf 'AgenticOps：developer 安装已完成，但安装级授权失败；请修正输入后单独运行 %s/bin/ao-work auth\n' "$INSTALL_DIR" >&2
    exit 2
  fi
  authorization_status="configured"
elif [ -t 0 ] && [ -t 1 ]; then
  if ! "$INSTALL_DIR/bin/ao-work" auth; then
    printf 'AgenticOps：developer 安装已完成，但安装级授权尚未完成；稍后可单独运行 %s/bin/ao-work auth\n' "$INSTALL_DIR" >&2
    exit 2
  fi
  authorization_status="configured"
else
  printf 'AgenticOps：developer 安装已完成，当前无交互终端；请运行 %s/bin/ao-work auth 完成安装级授权\n' "$INSTALL_DIR" >&2
fi

agentic_bootstrap_json_success bootstrap_install \
  install_dir "$INSTALL_DIR" current_ref "$current_ref" python "3.12" \
  authorization_status "$authorization_status" \
  authorization_next_action "$INSTALL_DIR/bin/ao-work auth"
