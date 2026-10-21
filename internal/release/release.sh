#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="${AGENTIC_OPS_RELEASE_SNAPSHOT:-$(cd "$script_dir/../.." && pwd -P)}"

# shellcheck source=internal/release/lib/development-workflow.sh
. "$script_dir/lib/development-workflow.sh"
# shellcheck source=internal/release/lib/release-common.sh
. "$script_dir/lib/release-common.sh"

release_enter_snapshot "$repo_root" release.sh "$@" || exit 1

command_name="${1:-}"
if [ -z "$command_name" ]; then
  release_fail "invalid_release_command" "argument_parsing" "缺少发布子命令" "请使用 inspect、prepare、verify、submit-for-review、publish 或 recover"
  exit 1
fi
shift

version=""
configure_workflow="false"
confirm_release="false"
allow_soft_gate="false"
wait_for_merge="true"
merged_pr=""
confirm_recovery=""
RELEASE_JIRA_ID=""
same_epoch_reason=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --jira)
      [ "$#" -ge 2 ] || exit 1
      RELEASE_JIRA_ID="$2"
      release_validate_jira_id "$RELEASE_JIRA_ID" || exit 1
      shift 2
      ;;
    --same-epoch-reason)
      [ "$#" -ge 2 ] || exit 1
      same_epoch_reason="$2"
      shift 2
      ;;
    --version)
      if [ "$#" -lt 2 ]; then
        release_fail "invalid_release_version" "argument_parsing" "--version 缺少值" "请提供例如 --version v0.3"
        exit 1
      fi
      version="$2"
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
    --no-wait-for-merge)
      wait_for_merge="false"
      shift
      ;;
    --merged-pr)
      if [ "$#" -lt 2 ]; then
        release_fail "invalid_release_merged_pr" "argument_parsing" "--merged-pr 缺少 PR 编号" "请提供已合并 PR 的数字编号"
        exit 1
      fi
      merged_pr="$2"
      shift 2
      ;;
    --confirm-recovery)
      if [ "$#" -lt 2 ]; then
        release_fail "invalid_release_confirmation" "argument_parsing" "--confirm-recovery 缺少绑定值" "请复制上一轮 recover 输出的完整继续命令"
        exit 1
      fi
      confirm_recovery="$2"
      shift 2
      ;;
    *)
      release_fail "invalid_release_argument" "argument_parsing" "不支持的参数 $1" "请检查发布命令帮助"
      exit 1
      ;;
  esac
done

if [ "$wait_for_merge" = "false" ] && { [ "$command_name" != "publish" ] || [ "$allow_soft_gate" != "true" ]; }; then
  release_fail "invalid_release_wait_option" "argument_parsing" "--no-wait-for-merge 只适用于软门禁 publish" "请与 publish --allow-soft-gate 一起使用，或移除此参数"
  exit 1
fi

RELEASE_IDENTITY="$version"
RELEASE_BASE_COMMIT=""
if [ "$command_name" != inspect ]; then release_lock_operation "$repo_root" || exit 1; fi

case "$command_name" in
  inspect)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_inspect_state "$repo_root" "$version" "$merged_pr" || exit 1
    ;;
  prepare)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_branch "$repo_root" develop || exit 1
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_synced_branch "$repo_root" develop || exit 1
    prepare_head="$(git -C "$repo_root" rev-parse HEAD)"
    prepare_in_main_status=0
    release_candidate_is_in_main "$repo_root" "$prepare_head" || prepare_in_main_status=$?
    if [ "$prepare_in_main_status" -eq 2 ]; then
      exit 1
    fi
    if [ "$prepare_in_main_status" -eq 0 ]; then
      release_print_confirmation_bundle "$repo_root" "发布候选已提前合入 main；不会准备或修改 Tag" "$version" "$prepare_head" develop main "需要通过 inspect 确认实际合并 PR，再进入受控 recover。"
      release_fail "release_candidate_already_in_main" "state_inspection" "当前 develop 候选已经包含在 origin/main，不应重新 prepare" "请运行 internal/release/release.sh inspect --version $version --allow-soft-gate 获取唯一下一命令"
      exit 1
    fi
    protection_mode="hard"
    if [ "$allow_soft_gate" = "true" ]; then protection_mode="soft"; fi
    release_require_version_tag_available "$repo_root" "$version" || exit 1
    release_preflight_candidate "$repo_root" "$prepare_head" || exit 1
    epoch_mode="$(release_verification epoch --root "$repo_root" --base "$RELEASE_BASE_COMMIT" --head "$prepare_head")" || exit 1
    if [ "$epoch_mode" = same ] && { [ -z "$same_epoch_reason" ] || [ -z "$RELEASE_JIRA_ID" ]; }; then
      release_fail release_same_epoch_reason_required preflight "同 epoch 默认 Hotfix" "正式版本需 --jira 与 --same-epoch-reason，说明已在 Jira 记录的版本发布理由及授权"
      exit 1
    fi
    release_verify_story_gate "$repo_root" origin/main "$prepare_head" true || exit 1
    release_run_full_verification "$repo_root" "$prepare_head" || exit 1
    release_branch="release/$version"
    release_prepare_fixed_branch "$repo_root" "$release_branch" "$prepare_head" || exit 1
    printf '{"schema_version":"step-result/v2","ok":true,"operation":"release_prepare","status":"completed","retry_safe":true,"result":{"status":"succeeded","summary":"发布准备已完成，等待人工审阅范围","facts":{},"evidence":[],"effects":[],"remaining":[]},"next_step":{"kind":"decision","scope":"flow","mode":"manual","executor":"reviewer","action":"review_release_scope","question":"请审阅发布范围后再继续发布","choices":[{"id":"review","label":"审阅发布范围","recommended":true}],"submit":{"operation":"submit_decision","effect":"record_only"},"call":{"operation":"submit_decision","argv":[]}},"version":"%s","head":"%s","release_branch":"%s","verified_at":"%s","tag_scope":"main_merge_commit","protection_mode":"%s","delivery":"product_source"}\n' \
      "$version" "$prepare_head" "$release_branch" "$RELEASE_VERIFIED_AT" "$protection_mode"
    ;;
  publish)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    protection_mode="hard"
    release_source_branch="release/$version"
    release_resolve_prepared_fixed_branch "$repo_root" "$release_source_branch" || exit 1
    release_head="$RELEASE_FIXED_HEAD"
    if [ "$allow_soft_gate" = "true" ]; then
      protection_mode="soft"
    fi
    candidate_in_main_status=0
    release_candidate_is_in_main "$repo_root" "$release_head" || candidate_in_main_status=$?
    if [ "$candidate_in_main_status" -eq 2 ]; then
      exit 1
    fi
    if [ "$candidate_in_main_status" -eq 0 ]; then
      release_print_confirmation_bundle "$repo_root" "发布候选已提前合入 main；不会创建 PR 或推送 Tag" "$version" "$release_head" "$release_source_branch" main "需要通过 inspect 确认状态，并使用受控 recover 绑定实际已合并 PR。"
      release_fail "release_candidate_already_in_main" "state_inspection" "固定发布候选已经包含在 origin/main，GitHub 无法创建无差异发布 PR" "请先运行 internal/release/release.sh inspect --version $version --allow-soft-gate；确认关联 PR 后运行 recover"
      exit 1
    fi
    release_require_version_tag_available "$repo_root" "$version" || exit 1
    release_preflight_candidate "$repo_root" "$release_head" || exit 1
    release_verify_story_gate "$repo_root" origin/main "$release_head" || exit 1
    release_run_full_verification "$repo_root" "$release_head" check || exit 1
    release_confirm_publish "$repo_root" "$version" "$release_head" "$confirm_release" "$release_source_branch" main || exit 1
    release_check_base_unchanged "$repo_root" || exit 1
    release_push_fixed_branch "$repo_root" "$release_source_branch" "$release_head" || exit 1
    release_repository="tapstate/agentic-ops"
    release_find_or_create_pr "$release_repository" "$release_source_branch" main "$release_head" "$version" release "$RELEASE_JIRA_ID" "$protection_mode" || exit 1
    release_validate_pr_binding "$RELEASE_PR_URL" "$version" "$release_head" || exit 1
    release_check_base_unchanged "$repo_root" || exit 1
    if [ "$allow_soft_gate" = "true" ]; then
      if [ "$wait_for_merge" = "true" ]; then
        release_wait_for_soft_merge "$release_repository" "$release_head" || exit 1
      else
        manual_status=0
        release_wait_for_manual_merge "$repo_root" "$release_repository" release_publish "$version" "$release_head" "$release_source_branch" || manual_status=$?
        if [ "$manual_status" -eq 2 ]; then
          exit 2
        fi
        if [ "$manual_status" -ne 0 ]; then
          exit 1
        fi
      fi
    else
      release_enable_auto_merge "$release_repository" || exit 1
      release_wait_for_merge "$release_repository" || exit 1
    fi
    release_validate_pr_binding "$RELEASE_PR_URL" "$version" "$release_head" || exit 1
    release_finish "$repo_root" "$version" "$release_head" "$RELEASE_MERGE_COMMIT" "$protection_mode" release_publish || exit 1
    ;;
  submit-for-review)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    [ "$allow_soft_gate" = "true" ] || {
      release_fail "release_independent_review_requires_soft_gate" "argument_parsing" "独立审查发布只适用于软门禁的固定 release 分支" "请显式传入 --allow-soft-gate"
      exit 1
    }
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_source_branch="release/$version"
    release_resolve_prepared_fixed_branch "$repo_root" "$release_source_branch" || exit 1
    release_head="$RELEASE_FIXED_HEAD"
    candidate_in_main_status=0
    release_candidate_is_in_main "$repo_root" "$release_head" || candidate_in_main_status=$?
    if [ "$candidate_in_main_status" -eq 2 ]; then
      exit 1
    fi
    if [ "$candidate_in_main_status" -eq 0 ]; then
      release_fail "release_candidate_already_in_main" "state_inspection" "固定发布候选已经包含在 origin/main，不能再创建独立审查 PR" "请运行 release.sh inspect，并按输出使用 recover"
      exit 1
    fi
    release_require_version_tag_available "$repo_root" "$version" || exit 1
    release_preflight_candidate "$repo_root" "$release_head" || exit 1
    release_submit_trust_root_review "$repo_root" tapstate/agentic-ops "$version" "$release_source_branch" "$release_head" soft "$confirm_release" || exit 1
    printf '{"schema_version":"step-result/v2","ok":true,"operation":"release_submit_for_review","status":"completed","retry_safe":true,"result":{"status":"succeeded","summary":"信任根发布候选已提交独立审查 PR，尚未合并或创建 Tag","facts":{},"evidence":[],"effects":[],"remaining":[]},"next_step":{"kind":"decision","scope":"flow","mode":"manual","executor":"reviewer","action":"review_and_merge_release_pr","question":"请在 GitHub 独立审查并使用 Merge commit 合入发布 PR；合入后运行 inspect 获取受控 recover 命令","choices":[{"id":"review","label":"审查并合并 PR","recommended":true}],"submit":{"operation":"submit_decision","effect":"record_only"},"call":{"operation":"submit_decision","argv":[]}},"version":"%s","head":"%s","pr_number":%s,"pr_url":"%s","release_branch":"%s","protection_mode":"soft"}\n' \
      "$version" "$release_head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$release_source_branch"
    ;;
  verify)
    release_validate_version "$version" || exit 1
    release_require_repo "$repo_root" || exit 1
    if [ -n "$merged_pr" ]; then
      release_read_merged_pr "$merged_pr" || exit 1
      [ "$RELEASE_RECOVERY_PR_STATE" = MERGED ] && [ "$RELEASE_RECOVERY_PR_BASE" = main ] || exit 1
      git -C "$repo_root" fetch origin main >/dev/null 2>&1 || exit 1
      verify_head="$RELEASE_RECOVERY_PR_HEAD"
      RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$RELEASE_RECOVERY_MERGE_COMMIT^1")"
      release_validate_pr_binding "$RELEASE_RECOVERY_PR_URL" "$version" "$verify_head" || exit 1
      release_verify_merge_commit "$repo_root" "$verify_head" "$RELEASE_RECOVERY_MERGE_COMMIT" || exit 1
    else
      release_resolve_prepared_fixed_branch "$repo_root" "release/$version" || exit 1
      verify_head="$RELEASE_FIXED_HEAD"
      release_preflight_candidate "$repo_root" "$verify_head" || exit 1
      release_verify_story_gate "$repo_root" origin/main "$verify_head" true || exit 1
    fi
    release_run_full_verification "$repo_root" "$verify_head" verify || exit 1
    printf '{"ok":true,"operation":"release_verify","head":"%s","verified_at":"%s"}\n' "$verify_head" "$RELEASE_VERIFIED_AT"
    ;;
  recover)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    [ "$allow_soft_gate" = "true" ] || {
      release_fail "release_recovery_requires_soft_gate" "recovery_validation" "恢复发布只适用于软门禁下已提前合入的候选" "请显式传入 --allow-soft-gate，并使用已合并 PR 编号"
      exit 1
    }
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_recover_merged_candidate "$repo_root" "$version" "$merged_pr" "$confirm_release" "$confirm_recovery" soft || exit 1
    ;;
  *)
    release_fail "invalid_release_command" "argument_parsing" "不支持的发布子命令 $command_name" "请使用 inspect、prepare、verify、submit-for-review、publish 或 recover"
    exit 1
    ;;
esac
