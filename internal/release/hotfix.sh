#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="${AGENTIC_OPS_RELEASE_SNAPSHOT:-$(cd "$script_dir/../.." && pwd -P)}"
. "$script_dir/lib/release-common.sh"
. "$script_dir/lib/development-workflow.sh"
# 可由固定入口与真实 Git 回归共用；仓库身份和授权仍由入口先核验。
release_hotfix_candidate() {
  local repo_root="$1" jira_id="$2" status
  RELEASE_IDENTITY="$jira_id"
  RELEASE_JIRA_ID="$jira_id"
  RELEASE_BASE_COMMIT=""
  git -C "$repo_root" fetch origin main develop >/dev/null 2>&1 || exit 1
  release_find_delivery_pr "$jira_id" || exit 1
  pr_rows="$RELEASE_PR_NUMBER"
  if [ -n "$pr_rows" ]; then
    head="$RELEASE_PR_HEAD"
    if [ "$RELEASE_PR_STATE" = MERGED ]; then
      RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$RELEASE_MERGE_COMMIT^1")"
      release_validate_pr_binding "$RELEASE_PR_URL" "$jira_id" "$head" || exit 1
      release_verify_merge_commit "$repo_root" "$head" "$RELEASE_MERGE_COMMIT" || exit 1
      release_run_full_verification "$repo_root" "$head" ensure || exit 1
      workflow_check_delivery tapstate/agentic-ops hard || exit 1
      release_check_review_objections "$RELEASE_PR_NUMBER" || exit 1
      release_finish "$repo_root" "" "$head" "$RELEASE_MERGE_COMMIT" manual hotfix_publish
      exit $?
    fi
    [ "$RELEASE_PR_STATE" = OPEN ] || {
      release_fail hotfix_pr_closed preflight "Hotfix PR 已关闭但未合并" "请人工决定恢复该 PR，不自动换候选"
      exit 1
    }
  fi
  release_require_branch "$repo_root" develop || exit 1
  release_require_clean "$repo_root" || exit 1
  release_require_synced_branch "$repo_root" develop || exit 1
  head="$(git -C "$repo_root" rev-parse develop)"
  release_preflight_candidate "$repo_root" "$head" || exit 1
  if git -C "$repo_root" merge-base --is-ancestor "$head" origin/main; then
    release_fail release_candidate_already_in_main preflight "develop 没有待交付变更" "核对原 PR；不创建空 PR 或重复合并"
    exit 1
  fi
  release_verify_story_gate "$repo_root" origin/main "$head" true "" || exit 1
  release_run_full_verification "$repo_root" "$head" ensure || exit 1
  release_check_base_unchanged "$repo_root" || exit 1
  release_push_develop "$repo_root" "$head" || exit 1
  release_find_or_create_pr tapstate/agentic-ops develop main "$head" "" hotfix "$jira_id" hard || exit 1
  release_validate_pr_binding "$RELEASE_PR_URL" "$jira_id" "$head" || exit 1
  payload="$(printf '{"ok":false,"operation":"hotfix_publish","status":"waiting_for_manual_merge","jira_id":"%s","head":"%s","pr_number":%s,"pr_url":"%s","tag":"","continue_command":"internal/release/hotfix.sh %s"}\n' "$jira_id" "$head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$jira_id")"
  release_result_json "$payload"
  exit 2
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  release_enter_snapshot "$repo_root" hotfix.sh "$@" || exit 1

  if [ "$#" -ne 1 ]; then
    release_fail invalid_hotfix_arguments argument_parsing "Hotfix 只接受一个 Jira 编号" "请使用 internal/release/hotfix.sh AO-123"
    exit 1
  fi
  jira_id="$1"
  release_validate_jira_id "$jira_id" || exit 1
  release_require_repo "$repo_root" || exit 1
  release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
  release_lock_operation "$repo_root" || exit 1
  release_hotfix_candidate "$repo_root" "$jira_id"
fi
