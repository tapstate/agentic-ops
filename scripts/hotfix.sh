#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/.." && pwd -P)"

# shellcheck source=scripts/lib/development-workflow.sh
. "$script_dir/lib/development-workflow.sh"
# shellcheck source=scripts/lib/release-common.sh
. "$script_dir/lib/release-common.sh"

command_name="${1:-}"
if [ -z "$command_name" ]; then
  release_fail "invalid_hotfix_command" "argument_parsing" "缺少 Hotfix 子命令" "请使用 create、prepare 或 publish"
  exit 1
fi
shift

jira_id=""
requested_user=""
configure_workflow="false"
confirm_release="false"
allow_soft_gate="false"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --jira-id)
      if [ "$#" -lt 2 ]; then
        release_fail "invalid_jira_id" "argument_parsing" "--jira-id 缺少值" "请提供例如 --jira-id AO-123"
        exit 1
      fi
      jira_id="$2"
      shift 2
      ;;
    --user)
      if [ "$#" -lt 2 ]; then
        release_fail "invalid_git_user" "argument_parsing" "--user 缺少值" "请提供 Git 用户名"
        exit 1
      fi
      requested_user="$2"
      shift 2
      ;;
    --configure-workflow)
      configure_workflow="true"
      shift
      ;;
    --confirm-release)
      confirm_release="true"
      shift
      ;;
    --allow-soft-gate)
      allow_soft_gate="true"
      shift
      ;;
    *)
      release_fail "invalid_hotfix_argument" "argument_parsing" "不支持的参数 $1" "请检查 Hotfix 命令"
      exit 1
      ;;
  esac
done

case "$command_name" in
  create)
    release_validate_jira_id "$jira_id" || exit 1
    release_require_command git || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_clean "$repo_root" || exit 1
    if ! git -C "$repo_root" fetch origin main >/dev/null 2>&1; then
      release_fail "hotfix_main_fetch_failed" "hotfix_create" "无法刷新 origin/main" "请检查网络和远端权限后重试"
      exit 1
    fi
    if [ -z "$requested_user" ]; then
      requested_user="$(git -C "$repo_root" config --get user.name 2>/dev/null || true)"
    fi
    hotfix_user="$(release_normalize_git_user "$requested_user")" || exit 1
    hotfix_branch="$hotfix_user/$jira_id/fix-main"
    if git -C "$repo_root" show-ref --verify --quiet "refs/heads/$hotfix_branch" ||
      [ -n "$(git -C "$repo_root" ls-remote --heads origin "refs/heads/$hotfix_branch" 2>/dev/null)" ]; then
      release_fail "hotfix_branch_exists" "hotfix_create" "本地或远端已存在 $hotfix_branch" "请继续已有修复分支或使用新的 Jira 任务"
      exit 1
    fi
    if ! git -C "$repo_root" switch -c "$hotfix_branch" refs/remotes/origin/main; then
      release_fail "hotfix_branch_create_failed" "hotfix_create" "无法从最新 origin/main 创建修复分支" "请检查本地分支状态后重试"
      exit 1
    fi
    printf '{"ok":true,"operation":"hotfix_create","jira_id":"%s","branch":"%s","base":"origin/main","agentic_next_action":"implement_and_test_hotfix"}\n' \
      "$jira_id" "$hotfix_branch"
    ;;
  prepare)
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    hotfix_branch="$(git -C "$repo_root" branch --show-current)"
    release_parse_hotfix_branch "$hotfix_branch" || exit 1
    release_require_clean "$repo_root" || exit 1
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_main_base "$repo_root" || exit 1
    hotfix_version="$(release_find_iteration_tag "$repo_root")" || exit 1
    release_build_assets "$repo_root" || exit 1
    protection_mode="hard"
    if [ "$allow_soft_gate" = "true" ]; then protection_mode="soft"; fi
    printf '{"ok":true,"operation":"hotfix_prepare","jira_id":"%s","branch":"%s","version":"%s","tag_action":"reuse_only","protection_mode":"%s","agentic_next_action":"review_and_commit_generated_assets"}\n' \
      "$HOTFIX_JIRA_ID" "$HOTFIX_BRANCH" "$hotfix_version" "$protection_mode"
    ;;
  publish)
    release_require_command git || exit 1
    release_require_command go || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    hotfix_branch="$(git -C "$repo_root" branch --show-current)"
    release_parse_hotfix_branch "$hotfix_branch" || exit 1
    release_require_clean "$repo_root" || exit 1
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_main_base "$repo_root" || exit 1
    release_require_synced_hotfix_branch "$repo_root" "$hotfix_branch" || exit 1
    hotfix_version="$(release_find_iteration_tag "$repo_root")" || exit 1
    hotfix_head="$(git -C "$repo_root" rev-parse HEAD)"
    protection_mode="hard"
    if [ "$allow_soft_gate" = "true" ]; then protection_mode="soft"; fi
    release_run_full_verification "$repo_root" "$hotfix_head" || exit 1
    release_confirm_publish "$repo_root" "$hotfix_version" "$hotfix_head" "$confirm_release" "$hotfix_branch" main || exit 1
    if ! git -C "$repo_root" push -u origin "$hotfix_branch"; then
      release_fail "hotfix_branch_push_failed" "branch_push" "无法推送修复分支" "请检查远端权限和分支状态后重试"
      exit 1
    fi
    release_repository="${AGENTIC_OPS_RELEASE_REPOSITORY:-tapstate/agentic-ops}"
    release_find_or_create_pr "$release_repository" "$hotfix_branch" main "$hotfix_head" "$hotfix_version" hotfix "$HOTFIX_JIRA_ID" "$protection_mode" || exit 1
    if [ "$allow_soft_gate" = "true" ]; then
      manual_status=0
      release_wait_for_manual_merge "$repo_root" "$release_repository" hotfix_publish "$hotfix_version" "$hotfix_head" "$hotfix_branch" "$HOTFIX_JIRA_ID" || manual_status=$?
      if [ "$manual_status" -eq 2 ]; then
        exit 2
      fi
      if [ "$manual_status" -ne 0 ]; then
        exit 1
      fi
    else
      release_enable_auto_merge "$release_repository" || exit 1
      release_wait_for_merge "$release_repository" || exit 1
    fi
    release_verify_remote_contains "$repo_root" "$hotfix_head" || exit 1
    if [ "$allow_soft_gate" = "true" ]; then
      release_verify_merge_commit "$repo_root" "$hotfix_head" "$RELEASE_MERGE_COMMIT" || exit 1
    fi
    release_write_hotfix_audit "$repo_root" "$HOTFIX_JIRA_ID" "$hotfix_version" "$hotfix_head" "$hotfix_branch" "$protection_mode"
    printf '{"ok":true,"operation":"hotfix_publish","jira_id":"%s","version":"%s","branch":"%s","head":"%s","pr_number":%s,"pr_url":"%s","merge_commit":"%s","protection_mode":"%s","audit_file":"%s","agentic_next_action":"sync_hotfix_to_develop"}\n' \
      "$HOTFIX_JIRA_ID" "$hotfix_version" "$hotfix_branch" "$hotfix_head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_MERGE_COMMIT" "$protection_mode" "$RELEASE_AUDIT_FILE"
    ;;
  *)
    release_fail "invalid_hotfix_command" "argument_parsing" "不支持的 Hotfix 子命令 $command_name" "请使用 create、prepare 或 publish"
    exit 1
    ;;
esac
