#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/../.." && pwd -P)"

# shellcheck source=internal/release/lib/development-workflow.sh
. "$script_dir/lib/development-workflow.sh"
# shellcheck source=internal/release/lib/release-common.sh
. "$script_dir/lib/release-common.sh"

command_name="${1:-}"
if [ -z "$command_name" ]; then
  release_fail "invalid_release_command" "argument_parsing" "缺少发布子命令" "请使用 inspect、prepare、submit-for-review、publish 或 recover"
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
while [ "$#" -gt 0 ]; do
  case "$1" in
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

case "$command_name" in
  inspect)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_inspect_state "$repo_root" "$version" || exit 1
    ;;
  prepare)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_branch "$repo_root" develop || exit 1
    release_require_clean "$repo_root" || exit 1
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
    release_run_full_verification "$repo_root" "$prepare_head" || exit 1
    release_require_version_tag_available "$repo_root" "$version" || exit 1
    release_branch=""
    if [ "$allow_soft_gate" = "true" ]; then
      release_branch="release/$version"
      release_prepare_fixed_branch "$repo_root" "$release_branch" "$prepare_head" || exit 1
    fi
    printf '{"schema_version":"step-result/v2","ok":true,"operation":"release_prepare","status":"completed","retry_safe":true,"result":{"status":"succeeded","summary":"发布准备已完成，等待人工审阅范围","facts":{},"evidence":[],"effects":[],"remaining":[]},"next_step":{"kind":"decision","scope":"flow","mode":"manual","executor":"reviewer","action":"review_release_scope","question":"请审阅发布范围后再继续发布","choices":[{"id":"review","label":"审阅发布范围","recommended":true}],"submit":{"operation":"submit_decision","effect":"record_only"},"call":{"operation":"submit_decision","argv":[]}},"version":"%s","head":"%s","release_branch":"%s","verified_at":"%s","tag_scope":"main_merge_commit","protection_mode":"%s","delivery":"product_source"}\n' \
      "$version" "$prepare_head" "$release_branch" "$RELEASE_VERIFIED_AT" "$protection_mode"
    ;;
  publish)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_branch "$repo_root" develop || exit 1
    release_require_clean "$repo_root" || exit 1
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_synced_branch "$repo_root" develop || exit 1
    protection_mode="hard"
    release_source_branch="develop"
    release_head="$(git -C "$repo_root" rev-parse HEAD)"
    if [ "$allow_soft_gate" = "true" ]; then
      protection_mode="soft"
      release_source_branch="release/$version"
      release_resolve_prepared_fixed_branch "$repo_root" "$release_source_branch" || exit 1
      release_head="$RELEASE_FIXED_HEAD"
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
    release_verify_story_gate "$repo_root" origin/main "$release_head" || exit 1
    release_run_full_verification "$repo_root" "$release_head" || exit 1
    release_confirm_publish "$repo_root" "$version" "$release_head" "$confirm_release" "$release_source_branch" main || exit 1
    if ! git -C "$repo_root" push origin develop; then
      release_fail "release_develop_push_failed" "develop_push" "无法推送 develop" "请检查远端权限和分支状态后重试"
      exit 1
    fi
    if [ "$allow_soft_gate" = "true" ]; then
      release_push_fixed_branch "$repo_root" "$release_source_branch" "$release_head" || exit 1
    fi
    release_repository="tapstate/agentic-ops"
    release_find_or_create_pr "$release_repository" "$release_source_branch" main "$release_head" "$version" release "" "$protection_mode" || exit 1
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
    release_verify_remote_contains "$repo_root" "$release_head" || exit 1
    release_verify_merge_commit "$repo_root" "$release_head" "$RELEASE_MERGE_COMMIT" || exit 1
    release_sync_develop_to_main "$repo_root" "$RELEASE_MERGE_COMMIT" || exit 1
    release_create_and_push_version_tag "$repo_root" "$version" "$RELEASE_MERGE_COMMIT" || exit 1
    release_write_audit "$repo_root" "$version" "$release_head" "$protection_mode" || exit 1
    printf '{"schema_version":"step-result/v2","ok":true,"operation":"release_publish","status":"completed","retry_safe":true,"result":{"status":"succeeded","summary":"发布已完成","facts":{},"evidence":[],"effects":[],"remaining":[]},"next_step":{"kind":"none","scope":"flow","mode":"manual","executor":"stop","action":"release_completed","call":null},"version":"%s","head":"%s","pr_number":%s,"pr_url":"%s","merge_commit":"%s","develop_commit":"%s","tag":"%s","protection_mode":"%s","audit_file":"%s"}\n' \
      "$version" "$release_head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_MERGE_COMMIT" "$RELEASE_DEVELOP_COMMIT" "$version" "$protection_mode" "$RELEASE_AUDIT_FILE"
    ;;
  submit-for-review)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_branch "$repo_root" develop || exit 1
    release_require_clean "$repo_root" || exit 1
    [ "$allow_soft_gate" = "true" ] || {
      release_fail "release_independent_review_requires_soft_gate" "argument_parsing" "独立审查发布只适用于软门禁的固定 release 分支" "请显式传入 --allow-soft-gate"
      exit 1
    }
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_synced_branch "$repo_root" develop || exit 1
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
    release_submit_trust_root_review "$repo_root" tapstate/agentic-ops "$version" "$release_source_branch" "$release_head" soft "$confirm_release" || exit 1
    printf '{"schema_version":"step-result/v2","ok":true,"operation":"release_submit_for_review","status":"completed","retry_safe":true,"result":{"status":"succeeded","summary":"信任根发布候选已提交独立审查 PR，尚未合并或创建 Tag","facts":{},"evidence":[],"effects":[],"remaining":[]},"next_step":{"kind":"decision","scope":"flow","mode":"manual","executor":"reviewer","action":"review_and_merge_release_pr","question":"请在 GitHub 独立审查并使用 Merge commit 合入发布 PR；合入后运行 inspect 获取受控 recover 命令","choices":[{"id":"review","label":"审查并合并 PR","recommended":true}],"submit":{"operation":"submit_decision","effect":"record_only"},"call":{"operation":"submit_decision","argv":[]}},"version":"%s","head":"%s","pr_number":%s,"pr_url":"%s","release_branch":"%s","protection_mode":"soft"}\n' \
      "$version" "$release_head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$release_source_branch"
    ;;
  recover)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_branch "$repo_root" develop || exit 1
    release_require_clean "$repo_root" || exit 1
    [ "$allow_soft_gate" = "true" ] || {
      release_fail "release_recovery_requires_soft_gate" "recovery_validation" "恢复发布只适用于软门禁下已提前合入的候选" "请显式传入 --allow-soft-gate，并使用已合并 PR 编号"
      exit 1
    }
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_synced_branch "$repo_root" develop || exit 1
    release_recover_merged_candidate "$repo_root" "$version" "$merged_pr" "$confirm_release" "$confirm_recovery" soft || exit 1
    ;;
  *)
    release_fail "invalid_release_command" "argument_parsing" "不支持的发布子命令 $command_name" "请使用 inspect、prepare、submit-for-review、publish 或 recover"
    exit 1
    ;;
esac
