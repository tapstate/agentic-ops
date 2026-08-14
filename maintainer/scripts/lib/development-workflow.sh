#!/usr/bin/env bash

workflow_fail() {
  workflow_code="$1"
  workflow_message="$2"
  workflow_action="$3"
  printf '{"ok":false,"operation":"development_workflow","code":"%s","message":"%s","required_human_action":"%s"}\n' \
    "$workflow_code" "$workflow_message" "$workflow_action" >&2
  return 1
}

workflow_trusted_hooks_dir() {
  workflow_repo_root="$1"
  workflow_common_dir="$(git -C "$workflow_repo_root" rev-parse --git-common-dir 2>/dev/null)" ||
    return 1
  case "$workflow_common_dir" in
    /*)
      ;;
    *)
      workflow_common_dir="$workflow_repo_root/$workflow_common_dir"
      ;;
  esac
  workflow_common_dir="$(cd "$workflow_common_dir" 2>/dev/null && pwd -P)" || return 1
  printf '%s\n' "$workflow_common_dir/agentic-ops-hooks"
}

workflow_write_trusted_hook_launcher() {
  workflow_output="$1"
  workflow_versioned_hook="$2"
  cat > "$workflow_output" <<EOF
#!/usr/bin/env bash
# AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1
set -euo pipefail

trusted_hook_temp="\$(mktemp)"
cleanup_trusted_hook() {
  rm -f -- "\$trusted_hook_temp"
}
trap cleanup_trusted_hook EXIT HUP INT TERM
if ! git show "HEAD:$workflow_versioned_hook" > "\$trusted_hook_temp" 2>/dev/null; then
  printf 'AgenticOps trusted hook baseline is missing: $workflow_versioned_hook\n' >&2
  exit 1
fi
chmod 0700 "\$trusted_hook_temp"
trusted_hook_status=0
"\$trusted_hook_temp" "\$@" || trusted_hook_status=\$?
exit "\$trusted_hook_status"
EOF
}

workflow_install_trusted_hooks() {
  workflow_repo_root="$1"
  workflow_hook_dir="$(workflow_trusted_hooks_dir "$workflow_repo_root")" || {
    workflow_fail "workflow_trusted_hooks_failed" \
      "无法解析 Git common directory" \
      "请检查 AgenticOps 源头仓库和 worktree 状态"
    return 1
  }
  if [ -L "$workflow_hook_dir" ]; then
    workflow_fail "workflow_trusted_hooks_failed" \
      "可信 Hook 目录不能是符号链接" \
      "请人工移除 Git common directory 中异常的 agentic-ops-hooks 链接"
    return 1
  fi
  mkdir -p "$workflow_hook_dir" || return 1
  chmod 0700 "$workflow_hook_dir" || return 1
  for workflow_hook_name in pre-commit pre-push; do
    workflow_hook_target="$workflow_hook_dir/$workflow_hook_name"
    workflow_hook_pending="$workflow_hook_dir/.$workflow_hook_name.pending.$$"
    workflow_write_trusted_hook_launcher \
      "$workflow_hook_pending" ".githooks/$workflow_hook_name" || return 1
    chmod 0700 "$workflow_hook_pending" || return 1
    mv -f "$workflow_hook_pending" "$workflow_hook_target" || return 1
  done
  git -C "$workflow_repo_root" config core.hooksPath "$workflow_hook_dir" || {
    workflow_fail "workflow_trusted_hooks_failed" \
      "无法启用 Git common directory 中的可信 Hook launcher" \
      "请检查仓库 Git 配置权限"
    return 1
  }
}

workflow_confirm_change() {
  workflow_mode="$1"
  workflow_description="$2"
  case "$workflow_mode" in
    configure)
      return 0
      ;;
    check)
      workflow_fail \
        "workflow_configuration_required" \
        "$workflow_description" \
        "请确认后重新执行配置，或在非交互环境显式传入 --configure-workflow"
      return 1
      ;;
    interactive)
      printf '%s，是否现在配置？[y/N] ' "$workflow_description" >&2
      if ! IFS= read -r workflow_answer; then
        workflow_answer=""
      fi
      case "$workflow_answer" in
        y|Y|yes|YES)
          return 0
          ;;
        *)
          workflow_fail \
            "workflow_configuration_rejected" \
            "$workflow_description" \
            "请完成正式研发流程配置后重试"
          return 1
          ;;
      esac
      ;;
    *)
      workflow_fail "invalid_workflow_mode" "不支持的研发流程配置模式" "请使用 check、interactive 或 configure"
      return 1
      ;;
  esac
}

workflow_check_hooks() {
  workflow_repo_root="$1"
  workflow_hooks_path="$(git -C "$workflow_repo_root" config --get core.hooksPath 2>/dev/null || true)"
  workflow_expected_hooks_path="$(workflow_trusted_hooks_dir "$workflow_repo_root" || true)"
  [ -n "$workflow_expected_hooks_path" ] &&
    [ "$workflow_hooks_path" = "$workflow_expected_hooks_path" ] &&
    [ ! -L "$workflow_hooks_path" ] &&
    [ -x "$workflow_hooks_path/pre-commit" ] &&
    [ -x "$workflow_hooks_path/pre-push" ] &&
    grep -q 'AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1' \
      "$workflow_hooks_path/pre-commit" &&
    grep -q 'AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1' \
      "$workflow_hooks_path/pre-push"
}

workflow_check_develop() {
  workflow_repo_root="$1"
  git -C "$workflow_repo_root" ls-remote --exit-code --heads origin develop >/dev/null 2>&1
}

workflow_gh_value() {
  workflow_endpoint="$1"
  workflow_query="$2"
  "${AGENTIC_OPS_GH_BIN:-gh}" api "$workflow_endpoint" --jq "$workflow_query"
}

workflow_check_repository_settings() {
  workflow_repository="$1"
  workflow_default_branch="$(workflow_gh_value "repos/$workflow_repository" '.default_branch' 2>/dev/null)" || return 1
  workflow_auto_merge="$(workflow_gh_value "repos/$workflow_repository" '.allow_auto_merge' 2>/dev/null)" || return 1
  workflow_merge_commit="$(workflow_gh_value "repos/$workflow_repository" '.allow_merge_commit' 2>/dev/null)" || return 1
  [ "$workflow_default_branch" = "main" ] &&
    [ "$workflow_auto_merge" = "true" ] &&
    [ "$workflow_merge_commit" = "true" ]
}

workflow_check_default_main() {
  workflow_repository="$1"
  workflow_default_branch="$(workflow_gh_value "repos/$workflow_repository" '.default_branch' 2>/dev/null)" || return 1
  [ "$workflow_default_branch" = "main" ]
}

workflow_check_merge_commit() {
  workflow_repository="$1"
  workflow_merge_commit="$(workflow_gh_value "repos/$workflow_repository" '.allow_merge_commit' 2>/dev/null)" || return 1
  [ "$workflow_merge_commit" = "true" ]
}

workflow_ruleset_id() {
  workflow_repository="$1"
  workflow_gh_value \
    "repos/$workflow_repository/rulesets" \
    '.[] | select(.name == "agentic-ops-main-pull-request-only") | .id' 2>/dev/null
}

workflow_check_main_ruleset() {
  workflow_repository="$1"
  workflow_id="$(workflow_ruleset_id "$workflow_repository")" || return 1
  [ -n "$workflow_id" ] || return 1
  workflow_status="$(workflow_gh_value \
    "repos/$workflow_repository/rulesets/$workflow_id" \
    '[.enforcement, .target, (.conditions.ref_name.include == ["refs/heads/main"]), (.conditions.ref_name.exclude == []), (.bypass_actors | length), (any(.rules[]; .type == "deletion")), (any(.rules[]; .type == "non_fast_forward")), (any(.rules[]; .type == "pull_request")), ((.rules[] | select(.type == "pull_request") | .parameters.allowed_merge_methods) == ["merge"]), (.rules[] | select(.type == "pull_request") | .parameters.required_approving_review_count), (.rules[] | select(.type == "pull_request") | .parameters.require_code_owner_review), (.rules[] | select(.type == "pull_request") | .parameters.dismiss_stale_reviews_on_push), (.rules[] | select(.type == "pull_request") | .parameters.require_last_push_approval), (.rules[] | select(.type == "pull_request") | .parameters.required_review_thread_resolution)] | @tsv' 2>/dev/null)" || return 1
  [ "$workflow_status" = $'active\tbranch\ttrue\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\ttrue\ttrue\ttrue' ]
}

workflow_configure_repository_settings() {
  workflow_repository="$1"
  "${AGENTIC_OPS_GH_BIN:-gh}" api \
    --method PATCH \
    "repos/$workflow_repository" \
    -f default_branch=main \
    -F allow_auto_merge=true \
    -F allow_merge_commit=true >/dev/null
}

workflow_write_ruleset_payload() {
  workflow_payload="$1"
  cat > "$workflow_payload" <<'JSON'
{
  "name": "agentic-ops-main-pull-request-only",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {
      "type": "pull_request",
      "parameters": {
        "allowed_merge_methods": ["merge"],
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": true,
        "required_approving_review_count": 1,
        "required_review_thread_resolution": true
      }
    }
  ]
}
JSON
}

workflow_configure_main_ruleset() {
  workflow_repository="$1"
  workflow_id="$(workflow_ruleset_id "$workflow_repository" || true)"
  workflow_payload="$(mktemp)"
  workflow_write_ruleset_payload "$workflow_payload"
  if [ -n "$workflow_id" ]; then
    if ! "${AGENTIC_OPS_GH_BIN:-gh}" api \
      --method PUT \
      "repos/$workflow_repository/rulesets/$workflow_id" \
      --input "$workflow_payload" >/dev/null; then
      rm -f "$workflow_payload"
      return 1
    fi
  else
    if ! "${AGENTIC_OPS_GH_BIN:-gh}" api \
      --method POST \
      "repos/$workflow_repository/rulesets" \
      --input "$workflow_payload" >/dev/null; then
      rm -f "$workflow_payload"
      return 1
    fi
  fi
  rm -f "$workflow_payload"
}

workflow_check_github_auth() {
  workflow_gh_bin="${AGENTIC_OPS_GH_BIN:-gh}"
  if "$workflow_gh_bin" auth status -h github.com >/dev/null 2>&1; then
    return 0
  fi
  "$workflow_gh_bin" api user >/dev/null 2>&1
}

workflow_check_soft_gate() {
  workflow_repo_root="$1"
  workflow_repository="tapstate/agentic-ops"

  if ! workflow_check_hooks "$workflow_repo_root"; then
    workflow_fail "workflow_soft_gate_required" \
      "软门禁要求当前 clone 启用 Git common directory trusted Hook launcher" \
      "请先用硬门禁配置流程安装 trusted launcher，或显式执行 workflow_install_trusted_hooks 后重试"
    return 1
  fi
  if ! workflow_check_develop "$workflow_repo_root"; then
    workflow_fail "workflow_soft_gate_required" "软门禁要求远端 develop 分支存在" "请先创建并推送 develop 分支"
    return 1
  fi
  if ! workflow_check_github_auth; then
    workflow_fail "workflow_github_auth_required" "GitHub CLI 未登录或凭证无效" "请执行 gh auth login -h github.com"
    return 1
  fi
  if ! workflow_check_default_main "$workflow_repository"; then
    workflow_fail "workflow_soft_gate_required" "软门禁要求 GitHub 默认分支为 main" "请由仓库管理员把默认分支设置为 main"
    return 1
  fi
  if ! workflow_check_merge_commit "$workflow_repository"; then
    workflow_fail "workflow_soft_gate_required" "软门禁要求仓库允许 Merge commit" "请由仓库管理员启用 Merge commit"
    return 1
  fi

  printf '警告：protection_mode=soft，GitHub Free 私有仓库无法从服务器端阻止 main 直接推送。\n' >&2
  printf '{"ok":true,"operation":"development_workflow","repository":"%s","default_branch":"main","development_branch":"develop","protection_mode":"soft"}\n' \
    "$workflow_repository"
}

workflow_check_or_configure() {
  workflow_mode="$1"
  workflow_repo_root="${2:-$(pwd)}"
  workflow_repository="tapstate/agentic-ops"

  if [ "$workflow_mode" = "soft" ]; then
    workflow_check_soft_gate "$workflow_repo_root"
    return $?
  fi

  if ! workflow_check_hooks "$workflow_repo_root"; then
    workflow_confirm_change "$workflow_mode" \
      "Git common directory 中的可信 Hook launcher 尚未启用" || return 1
    workflow_install_trusted_hooks "$workflow_repo_root" || return 1
  fi

  if ! workflow_check_develop "$workflow_repo_root"; then
    workflow_confirm_change "$workflow_mode" "远端 develop 分支不存在" || return 1
    workflow_branch="$(git -C "$workflow_repo_root" branch --show-current)"
    if [ "$workflow_branch" != "develop" ]; then
      workflow_fail "workflow_develop_source_required" "创建远端 develop 时当前分支必须是 develop" "请切换到 develop 后重试"
      return 1
    fi
    if ! git -C "$workflow_repo_root" push -u origin develop >/dev/null; then
      workflow_fail "workflow_configuration_failed" "无法创建远端 develop" "请检查远端权限和网络后重试"
      return 1
    fi
  fi

  if ! workflow_check_github_auth; then
    workflow_fail "workflow_github_auth_required" "GitHub CLI 未登录或凭证无效" "请执行 gh auth login -h github.com"
    return 1
  fi

  if ! workflow_check_repository_settings "$workflow_repository"; then
    workflow_confirm_change "$workflow_mode" "GitHub 默认分支或 Auto-merge 设置不符合正式发布要求" || return 1
    if ! workflow_configure_repository_settings "$workflow_repository"; then
      workflow_fail "workflow_configuration_permission_denied" "无法更新 GitHub 仓库设置" "请使用具备 Administration 写权限的账号重试"
      return 1
    fi
  fi

  if ! workflow_check_main_ruleset "$workflow_repository"; then
    workflow_confirm_change "$workflow_mode" "main 的 PR-only ruleset 缺失或配置漂移" || return 1
    if ! workflow_configure_main_ruleset "$workflow_repository"; then
      workflow_fail "workflow_configuration_permission_denied" "无法配置 main PR-only ruleset" "请使用具备 Administration 写权限的账号重试"
      return 1
    fi
  fi

  if ! workflow_check_hooks "$workflow_repo_root" ||
    ! workflow_check_develop "$workflow_repo_root" ||
    ! workflow_check_repository_settings "$workflow_repository" ||
    ! workflow_check_main_ruleset "$workflow_repository"; then
    workflow_fail "workflow_configuration_verification_failed" "正式研发流程配置复检失败" "请检查 Hooks、develop、仓库设置和 ruleset"
    return 1
  fi

  printf '{"ok":true,"operation":"development_workflow","repository":"%s","default_branch":"main","development_branch":"develop"}\n' \
    "$workflow_repository"
}
