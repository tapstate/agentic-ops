#!/usr/bin/env bash
set -euo pipefail

REPO_URL="git@github.com:tapstate/agentic-ops.git"
GITHUB_REPOSITORY="tapstate/agentic-ops"

bootstrap_source_branch="develop"
bootstrap_previous_argument=""
for bootstrap_argument in "$@"; do
  if [ "$bootstrap_previous_argument" = "--source-branch" ]; then
    bootstrap_source_branch="$bootstrap_argument"
    break
  fi
  bootstrap_previous_argument="$bootstrap_argument"
done
case "$bootstrap_source_branch" in
  ""|-*|*[!A-Za-z0-9._/-]*|*..*|/*|*/)
    printf 'AgenticOps：来源分支格式无效：%s\n' "$bootstrap_source_branch" >&2
    printf '{"ok":false,"operation":"bootstrap_verify","status":"failed","code":"source_branch_invalid","retry_safe":true,"message":"来源分支格式无效","required_human_action":"请提供合法的 --source-branch"}\n'
    exit 1
    ;;
esac

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/lib/common.sh" ]; then
  . "$SCRIPT_DIR/lib/common.sh"
else
  bootstrap_common="$({
    command gh api -H 'Accept: application/vnd.github.raw' \
      "/repos/$GITHUB_REPOSITORY/contents/developer/bootstrap/lib/common.sh?ref=$bootstrap_source_branch"
  } 2>/dev/null)" || {
    printf 'AgenticOps：无法读取 developer Bootstrap 公共库\n' >&2
    printf '{"ok":false,"operation":"bootstrap_verify","status":"failed","code":"bootstrap_common_unavailable","retry_safe":true,"message":"无法读取 developer Bootstrap 公共库","required_human_action":"请确认 gh 已登录、来源分支存在且有权读取 %s"}\n' "$GITHUB_REPOSITORY"
    exit 1
  }
  eval "$bootstrap_common"
  unset bootstrap_common
fi
unset bootstrap_argument bootstrap_previous_argument bootstrap_source_branch

usage() {
  cat <<'USAGE'
用法：
  install-verify-branch.sh [--source-worktree <path>] [--source-branch <branch>] \
    [--install-home <path>] [--log <path>] [--json] [--keep] [授权参数]

远程启动时，调用侧必须先确认 gh api 下载成功，再执行完整脚本：
  (
    set -e
    bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
      '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install-verify-branch.sh?ref=develop')"
    printf '%s\n' "$bootstrap" | bash -s -- --source-branch develop --json
  )

参数说明：
  --source-branch    安装来源分支（默认 develop）。默认从官方远端 tapstate/agentic-ops 克隆，
                     生成可运行的验证安装；提供 --source-worktree 时改为从本地源码目录克隆，
                     仅验证安装流程，不可运行。
  --source-worktree  本地 AgenticOps 源码目录（可选；提供后进入本地流程验证模式）
  --install-home     安装目录（默认 ~/test/agentic-ops-verify-<timestamp>）
  --log              日志文件（默认 <install-home>.log，与安装目录同级）
  --json             输出 machine-readable JSON
  --keep             失败时不清理，保留安装目录用于排障
  --agent-id         安装级研发员标识；与其余授权参数一起转交 ao-work auth
  --jira-email       安装级 Jira email
  --git-name         Git author/committer name
  --git-email        Git author/committer email
  --github-login     GitHub login
  --execution-auth-mode  Git/SSH/gh 授权模式：global 或 installation
  --confirm-replace-authorization  精确绑定当前脱敏授权差异的确认摘要
  --token-stdin      从安全标准输入读取 Jira API token
  --non-interactive  非交互授权；不传授权信息时无终端安装输出待授权下一步

首次远程验证下载和 clone 只能使用调用者已有的 Git/gh 启动账户；installation 授权完成后，后续 Git 操作使用 Runtime 固化的安装专属 SSH。
USAGE
}

require_non_empty() {
  local name="$1"
  local value="$2"
  if [ -z "$value" ]; then
    agentic_bootstrap_json_error "invalid_argument" "参数不能为空：$name" "请提供有效参数"
    return 1
  fi
}

fail_usage() {
  local code="$1"
  local message="$2"
  local action="$3"

  if [ "$JSON_OUTPUT" -eq 1 ]; then
    agentic_bootstrap_json_error "$code" "$message" "$action"
    exit 1
  fi
  printf 'AgenticOps：%s\n' "$message" >&2
  printf '{"ok":false,"operation":"bootstrap_verify","status":"failed","code":"%s","retry_safe":true,"message":"%s","required_human_action":"%s"}\n' \
    "$(agentic_json_escape "$code")" \
    "$(agentic_json_escape "$message")" \
    "$(agentic_json_escape "$action")"
  exit 1
}

write_json_success() {
  local operation="$1"
  local install_dir="$2"
  local log_path="$3"
  local authorization_status="$4"
  printf '{"ok":true,"operation":"%s","status":"completed","install_dir":"%s","log":"%s","verification_mode":"true","authorization_status":"%s","authorization_next_action":"%s/bin/ao-work auth"}\n' \
    "$(agentic_json_escape "$operation")" \
    "$(agentic_json_escape "$install_dir")" \
    "$(agentic_json_escape "$log_path")" \
    "$(agentic_json_escape "$authorization_status")" \
    "$(agentic_json_escape "$install_dir")"
}

log_info() {
  local message="$1"
  printf '[verify-branch] %s\n' "$message" | tee -a "$LOG_PATH"
}

log_error() {
  local message="$1"
  printf '[verify-branch] %s\n' "$message" >&2 | tee -a "$LOG_PATH"
}

cleanup_install_home() {
  local home="$1"
  local keep="$2"
  if [ "$keep" -eq 1 ]; then
    return 0
  fi
  if [ -n "${INSTALL_HOME_CREATED:-}" ] && [ -d "$home" ]; then
    rm -rf "$home"
  fi
}

JSON_OUTPUT=0
KEEP_HOME=0
SOURCE_WORKTREE=""
SOURCE_BRANCH="develop"
INSTALL_HOME="${HOME}/test/agentic-ops-verify-$(date +%Y%m%d%H%M%S)"
LOG_PATH=""
AUTHORIZATION_ARGS=()
AUTHORIZATION_REQUESTED=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-worktree)
      if [ "$#" -lt 2 ]; then
        fail_usage invalid_argument "缺少 --source-worktree 对应路径" "请补齐参数"
      fi
      SOURCE_WORKTREE="$2"
      shift 2
      ;;
    --source-branch)
      if [ "$#" -lt 2 ]; then
        fail_usage invalid_argument "缺少 --source-branch 对应分支" "请补齐参数"
      fi
      SOURCE_BRANCH="$2"
      shift 2
      ;;
    --install-home)
      if [ "$#" -lt 2 ]; then
        fail_usage invalid_argument "缺少 --install-home 对应目录" "请补齐参数"
      fi
      INSTALL_HOME="$2"
      shift 2
      ;;
    --log)
      if [ "$#" -lt 2 ]; then
        fail_usage invalid_argument "缺少 --log 对应文件" "请补齐参数"
      fi
      LOG_PATH="$2"
      shift 2
      ;;
    --json)
      JSON_OUTPUT=1
      shift
      ;;
    --keep)
      KEEP_HOME=1
      shift
      ;;
    --agent-id|--jira-email|--git-name|--git-email|--github-login|--execution-auth-mode|--confirm-replace-authorization)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        fail_usage invalid_argument "授权参数缺少取值：$1" "请补齐参数"
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
      fail_usage invalid_argument "不支持的参数：$1" "请使用 --help 查看用法"
      ;;
  esac
done

if [ -z "$SOURCE_BRANCH" ] || [ -z "$INSTALL_HOME" ]; then
  fail_usage invalid_argument "参数不能为空" "请检查 --source-branch/--install-home"
fi

if [ "$INSTALL_HOME" = "$HOME/.agentic-ops" ]; then
  fail_usage verification_home_forbidden "验证安装不能写入 ~/.agentic-ops" "请使用独立验证目录"
fi

if [ -n "$SOURCE_WORKTREE" ]; then
  if ! [ -e "$SOURCE_WORKTREE" ]; then
    fail_usage source_worktree_not_found "source-worktree 不存在：$SOURCE_WORKTREE" "请指向合法本地源码目录"
  fi
  if [ ! -d "$SOURCE_WORKTREE" ]; then
    fail_usage source_worktree_not_directory "source-worktree 不是目录：$SOURCE_WORKTREE" "请指向合法本地源码目录"
  fi
  if ! cd "$SOURCE_WORKTREE" >/dev/null 2>&1; then
    fail_usage source_worktree_not_accessible "source-worktree 不可读：$SOURCE_WORKTREE" "请检查路径权限"
  fi
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail_usage source_worktree_not_git "source-worktree 不是 Git 仓库：$SOURCE_WORKTREE" "请检查源码目录"
  fi
  if ! git show-ref --verify --quiet "refs/heads/$SOURCE_BRANCH"; then
    fail_usage source_branch_not_found "source-worktree 不存在分支：$SOURCE_BRANCH" "请先创建/切换到该本地分支"
  fi
  SOURCE_SPEC="$SOURCE_WORKTREE"
  SOURCE_KIND="local"
else
  agentic_require_unrewritten_url "$REPO_URL" "验证安装"
  if ! git ls-remote --exit-code --heads "$REPO_URL" "refs/heads/$SOURCE_BRANCH" >/dev/null 2>&1; then
    fail_usage source_branch_not_found "官方远端不存在分支：$SOURCE_BRANCH" "请先把该分支推送到 tapstate/agentic-ops"
  fi
  SOURCE_SPEC="$REPO_URL"
  SOURCE_KIND="remote"
fi

if [ "$SOURCE_KIND" = "local" ] && [ "$AUTHORIZATION_REQUESTED" -eq 1 ]; then
  fail_usage verification_authorization_unavailable \
    "本地 source-worktree 验证安装不可运行，不能配置授权" \
    "请使用远端验证安装，或安装后单独运行 ao-work auth"
fi

if [ -z "$LOG_PATH" ]; then
  LOG_PATH="${INSTALL_HOME}.log"
fi

if [ -e "$INSTALL_HOME" ]; then
  if [ -d "$INSTALL_HOME" ] && [ -z "$(find "$INSTALL_HOME" -mindepth 1 -print -quit 2>/dev/null || true)" ]; then
    rm -rf "$INSTALL_HOME"
  elif [ -f "$INSTALL_HOME/.agentic-ops/verification-only" ]; then
    rm -rf "$INSTALL_HOME"
  else
    fail_usage install_home_not_empty "验证目录已存在且不为空：$INSTALL_HOME" "请清空后重试或显式指定其它目录"
  fi
fi

mkdir -p "$(dirname "$LOG_PATH")"
: > "$LOG_PATH"

trap 'cleanup_install_home "$INSTALL_HOME" "$KEEP_HOME"' EXIT

log_info "开始验证安装"
log_info "来源：${SOURCE_KIND}（${SOURCE_SPEC}）"
log_info "来源分支：$SOURCE_BRANCH"
log_info "安装目录：$INSTALL_HOME"

if ! install_home_parent="$(dirname "$INSTALL_HOME")" 2>/dev/null; then
  fail_usage install_home_invalid "无法识别安装目录上层目录：$INSTALL_HOME" "请确认路径合法"
fi
mkdir -p "$install_home_parent" || fail_usage install_home_unwritable "验证目录上层不可写：$install_home_parent" "请确认路径权限"

agentic_reject_identity_overrides

git clone --no-checkout --filter=blob:none --single-branch --branch "$SOURCE_BRANCH" "$SOURCE_SPEC" "$INSTALL_HOME"
if [ ! -d "$INSTALL_HOME" ] || ! cd "$INSTALL_HOME" >/dev/null 2>&1; then
  fail_usage install_clone_failed "验证目录克隆失败：$INSTALL_HOME" "请检查路径和权限"
fi

if [ -L "$INSTALL_HOME" ]; then
  fail_usage install_home_symlink "验证目录不应为符号链接：$INSTALL_HOME" "请使用普通目录"
fi

if [ "$SOURCE_KIND" = "remote" ]; then
  git -C "$INSTALL_HOME" remote set-url origin "$REPO_URL"
fi

INSTALL_HOME_CREATED=1
agentic_require_directory_slot "$INSTALL_HOME" "install_home"

source_commit="$(git -C "$INSTALL_HOME" rev-parse "refs/remotes/origin/$SOURCE_BRANCH" 2>/dev/null || true)"
if [ -z "$source_commit" ]; then
  source_commit="$(git -C "$INSTALL_HOME" rev-parse "$SOURCE_BRANCH" 2>/dev/null || true)"
fi
if [ -z "$source_commit" ]; then
  fail_usage source_branch_invalid "无法读取来源分支提交：$SOURCE_BRANCH" "请检查来源分支"
fi

if [ "$SOURCE_KIND" = "remote" ]; then
  if ! agentic_configure_developer_checkout "$INSTALL_HOME"; then
    fail_usage verification_sparse_checkout_failed "配置 developer sparse checkout 失败" "请检查源码来源是否完整"
  fi
else
  if ! agentic_configure_developer_sparse_checkout "$INSTALL_HOME"; then
    fail_usage verification_sparse_checkout_failed "配置 developer sparse checkout 失败" "请检查源码来源是否完整"
  fi
fi

git -C "$INSTALL_HOME" checkout "$SOURCE_BRANCH"

if [ ! -f "$INSTALL_HOME/.agentic-ops/verification-only" ]; then
  mkdir -p "$INSTALL_HOME/.agentic-ops"
  cat <<EOF_INFO > "$INSTALL_HOME/.agentic-ops/verification-only"
{
  "operation": "bootstrap-verify",
  "verification_only": true,
  "source": "$SOURCE_KIND",
  "source_spec": "$(printf '%s\n' "$SOURCE_SPEC")",
  "source_branch": "$(printf '%s\n' "$SOURCE_BRANCH")",
  "installed_at": "$(date -Iseconds)"
}
EOF_INFO
fi

uv_bin="$(agentic_find_uv)"
if ! agentic_verify_developer_checkout_for_verification "$INSTALL_HOME" "$SOURCE_BRANCH"; then
  fail_usage install_verification_invalid "验证安装工作树校验失败" "请检查来源分支与工作树"
fi

if ! agentic_sync_runtime_for_verification "$INSTALL_HOME" "$uv_bin" "$SOURCE_BRANCH"; then
  fail_usage runtime_bootstrap_failed "验证安装运行时同步失败" "请检查 Python/uv 与依赖"
fi

if [ ! -e "$INSTALL_HOME/.agentic-ops/verification-only" ]; then
  fail_usage verification_marker_missing "未写入 verification-only 标记" "请检查初始化流程"
fi

authorization_status="pending"
if [ "$SOURCE_KIND" = "remote" ]; then
  if [ "$AUTHORIZATION_REQUESTED" -eq 1 ]; then
    if ! "$INSTALL_HOME/bin/ao-work" auth "${AUTHORIZATION_ARGS[@]}"; then
      fail_usage verification_authorization_failed \
        "验证安装已完成，但安装级授权失败" \
        "请修正输入后单独运行 $INSTALL_HOME/bin/ao-work auth"
    fi
    authorization_status="configured"
  elif [ -t 0 ] && [ -t 1 ]; then
    if ! "$INSTALL_HOME/bin/ao-work" auth; then
      fail_usage verification_authorization_failed \
        "验证安装已完成，但安装级授权尚未完成" \
        "请稍后单独运行 $INSTALL_HOME/bin/ao-work auth"
    fi
    authorization_status="configured"
  else
    log_info "当前无交互终端；请运行 $INSTALL_HOME/bin/ao-work auth 完成安装级授权"
  fi
fi

write_json_success "bootstrap_verify" "$INSTALL_HOME" "$LOG_PATH" "$authorization_status"
exit 0
