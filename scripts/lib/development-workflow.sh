#!/usr/bin/env bash

workflow_fail() {
  workflow_code="$1"
  workflow_message="$2"
  workflow_action="$3"
  printf '{"ok":false,"operation":"development_workflow","code":"%s","message":"%s","required_human_action":"%s"}\n' \
    "$workflow_code" "$workflow_message" "$workflow_action" >&2
  return 1
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
  [ "$workflow_hooks_path" = ".githooks" ] &&
    [ -x "$workflow_repo_root/.githooks/pre-commit" ] &&
    [ -x "$workflow_repo_root/.githooks/pre-push" ]
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
    '[.enforcement, .target, (.conditions.ref_name.include | index("refs/heads/main") != null), (.bypass_actors | length), (any(.rules[]; .type == "deletion")), (any(.rules[]; .type == "non_fast_forward")), (any(.rules[]; .type == "pull_request")), ((.rules[] | select(.type == "pull_request") | .parameters.allowed_merge_methods) == ["merge"]), (.rules[] | select(.type == "pull_request") | .parameters.required_approving_review_count), (.rules[] | select(.type == "pull_request") | .parameters.require_code_owner_review), (.rules[] | select(.type == "pull_request") | .parameters.require_last_push_approval), (.rules[] | select(.type == "pull_request") | .parameters.required_review_thread_resolution)] | @tsv' 2>/dev/null)" || return 1
  [ "$workflow_status" = $'active\tbranch\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t0\tfalse\tfalse\tfalse' ]
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
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": false
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
  workflow_repository="${AGENTIC_OPS_RELEASE_REPOSITORY:-tapstate/agentic-ops}"

  if ! workflow_check_hooks "$workflow_repo_root"; then
    workflow_fail "workflow_soft_gate_required" "软门禁要求当前 clone 启用版本化 Git Hooks" "请执行 git config core.hooksPath .githooks 后重试"
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
  workflow_repository="${AGENTIC_OPS_RELEASE_REPOSITORY:-tapstate/agentic-ops}"

  if [ "$workflow_mode" = "soft" ]; then
    workflow_check_soft_gate "$workflow_repo_root"
    return $?
  fi

  if ! workflow_check_hooks "$workflow_repo_root"; then
    workflow_confirm_change "$workflow_mode" "本地 Git Hooks 尚未启用" || return 1
    git -C "$workflow_repo_root" config core.hooksPath .githooks ||
      workflow_fail "workflow_configuration_failed" "无法启用本地 Git Hooks" "请检查仓库配置权限"
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
