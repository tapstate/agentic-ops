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
  branch="codex/$jira_id/fix-main"
  git -C "$repo_root" fetch origin main develop >/dev/null 2>&1 || exit 1

  # 原 Jira 的 PR 是恢复事实；即使源分支删除或 develop 前进也不更换候选。
  pr_rows="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list --repo tapstate/agentic-ops --head "$branch" --base main --state all \
    --json number,url,state,headRefOid,mergeCommit \
    --jq '.[] | [.number,.url,.state,.headRefOid,(.mergeCommit.oid // "-")] | @tsv')" || exit 1
  if [ -n "$pr_rows" ]; then
    [ "$(printf '%s\n' "$pr_rows" | wc -l | tr -d ' ')" = 1 ] || {
      release_fail hotfix_pr_ambiguous preflight "同一 Hotfix 有多个 PR" "核对具体 PR，不自动挑选候选"
      exit 1
    }
    IFS=$'\t' read -r RELEASE_PR_NUMBER RELEASE_PR_URL RELEASE_PR_STATE head RELEASE_MERGE_COMMIT <<< "$pr_rows"
    if [ "$RELEASE_PR_STATE" = MERGED ]; then
      RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$RELEASE_MERGE_COMMIT^1")"
      release_validate_pr_binding "$RELEASE_PR_URL" "$jira_id" "$head" || exit 1
      release_verify_merge_commit "$repo_root" "$head" "$RELEASE_MERGE_COMMIT" || exit 1
      release_run_full_verification "$repo_root" "$head" ensure || exit 1
      release_finish "$repo_root" "" "$head" "$RELEASE_MERGE_COMMIT" manual hotfix_publish
      exit $?
    fi
    [ "$RELEASE_PR_STATE" = OPEN ] || {
      release_fail hotfix_pr_closed preflight "Hotfix PR 已关闭但未合并" "请人工决定恢复该 PR，不自动换候选"
      exit 1
    }
  else
    # 先按任务恢复已交付对象，不能让已推进的 develop 替换原候选。
    prior="$(git -C "$repo_root" log origin/main --first-parent --merges --format='%H%x09%s' |
      awk -F '\t' -v subject="Hotfix: $jira_id 合并 develop 到 main" '$2 == subject {print $1}')" || exit 1
    if [ -n "$prior" ]; then
      [ "$(printf '%s\n' "$prior" | wc -l | tr -d ' ')" = 1 ] || {
        release_fail hotfix_merge_ambiguous preflight "同一 Hotfix 有多个合并记录" "先核对实际发布对象，不自动选择候选"
        exit 1
      }
      RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$prior^1")"
      head="$(git -C "$repo_root" rev-parse "$prior^2")"
      release_verify_merge_commit "$repo_root" "$head" "$prior" || exit 1
      release_run_full_verification "$repo_root" "$head" ensure || exit 1
      release_finish "$repo_root" "" "$head" "$prior" direct hotfix_publish
      exit $?
    fi
    head="$(git -C "$repo_root" rev-parse develop)"
    remote_candidate="$(git -C "$repo_root" rev-parse origin/develop)"
    if git -C "$repo_root" merge-base --is-ancestor "$head" "$remote_candidate"; then
      head="$remote_candidate"
    elif ! git -C "$repo_root" merge-base --is-ancestor "$remote_candidate" "$head"; then
      release_fail hotfix_develop_diverged preflight "本地与远端 develop 分叉" "先核对候选，不自动集成"
      exit 1
    fi
  fi
  remote_main="$(git -C "$repo_root" rev-parse origin/main)"
  remote_develop="$(git -C "$repo_root" rev-parse origin/develop)"
  if [ "$remote_main" = "$head" ] && [ "$remote_develop" = "$head" ]; then
    release_sync_develop_to_main "$repo_root" "$head" || RELEASE_DEVELOP_SYNC=unknown
    status=completed
    if [ "$RELEASE_DEVELOP_SYNC" != synced ]; then status=completed_with_pending_sync; fi
    payload="$(printf '{"ok":true,"operation":"hotfix_publish","status":"%s","delivery_status":"published","develop_sync_status":"%s","jira_id":"%s","merge_commit":"%s","changed":false}\n' "$status" "$RELEASE_DEVELOP_SYNC" "$jira_id" "$head")"
    release_result_json "$payload"
    exit 0
  fi
  release_preflight_candidate "$repo_root" "$head" || exit 1
  release_verify_story_gate "$repo_root" origin/main "$head" true || exit 1
  release_run_full_verification "$repo_root" "$head" ensure || exit 1
  release_check_base_unchanged "$repo_root" || exit 1
  if [ "$RELEASE_TRUST_ROOT_CHANGED" = true ] || [ -n "$pr_rows" ]; then
    release_push_fixed_branch "$repo_root" "$branch" "$head" || exit 1
    release_find_or_create_pr tapstate/agentic-ops "$branch" main "$head" "" hotfix "$jira_id" soft || exit 1
    release_validate_pr_binding "$RELEASE_PR_URL" "$jira_id" "$head" || exit 1
    payload="$(printf '{"ok":false,"operation":"hotfix_publish","status":"waiting_for_manual_merge","jira_id":"%s","head":"%s","pr_number":%s,"pr_url":"%s","tag":"","continue_command":"internal/release/hotfix.sh %s"}\n' \
      "$jira_id" "$head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$jira_id")"
    release_result_json "$payload"
    exit 2
  fi
  merge_subject="Hotfix: $jira_id 合并 develop 到 main"
  merge_body="$(
    printf '%s\n' "将固定候选的已提交变更合入 main。" "" "Jira: $jira_id" "流程: direct-develop-to-main" "Jira 交互: none"
  )"
  merge_tree="$(git -C "$repo_root" rev-parse "$head^{tree}")"
  merge_commit="$(
    printf '%s\n\n%s\n' "$merge_subject" "$merge_body" |
      git -C "$repo_root" commit-tree "$merge_tree" -p "$RELEASE_BASE_COMMIT" -p "$head"
  )"
  # 只允许快进；并行 develop 已产生新提交时原子推送失败，不覆盖。
  AGENTIC_OPS_SPECIAL_PUSH=hotfix git -C "$repo_root" push --atomic origin \
    "$merge_commit:refs/heads/main" "$merge_commit:refs/heads/develop" || true
  # 无论传输返回什么都先回读；未知状态绝不盲目重建合并。
  git -C "$repo_root" fetch origin main develop >/dev/null 2>&1 || {
    release_fail hotfix_remote_readback_failed hotfix_publish "Hotfix 写入结果未知" "保留候选，先核对 main/develop 远端引用"
    exit 1
  }
  git -C "$repo_root" merge-base --is-ancestor "$merge_commit" origin/main || {
    release_fail hotfix_direct_push_failed hotfix_publish "远端 main 未包含本次合并" "检查权限及并行提交，不强推或自动集成"
    exit 1
  }
  release_finish "$repo_root" "" "$head" "$merge_commit" direct hotfix_publish
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
