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
  release_fail "invalid_release_command" "argument_parsing" "缺少发布子命令" "请使用 prepare 或 publish"
  exit 1
fi
shift

version=""
configure_workflow="false"
confirm_release="false"
allow_soft_gate="false"
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
    *)
      release_fail "invalid_release_argument" "argument_parsing" "不支持的参数 $1" "请检查发布命令帮助"
      exit 1
      ;;
  esac
done

case "$command_name" in
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
    release_require_version_tag "$repo_root" "$version" || exit 1
    release_build_assets "$repo_root" || exit 1
    protection_mode="hard"
    if [ "$allow_soft_gate" = "true" ]; then protection_mode="soft"; fi
    printf '{"ok":true,"operation":"release_prepare","version":"%s","tag_scope":"local","protection_mode":"%s","agentic_next_action":"review_and_commit_generated_assets"}\n' "$version" "$protection_mode"
    ;;
  publish)
    release_validate_version "$version" || exit 1
    release_require_command git || exit 1
    release_require_command go || exit 1
    release_require_command "${AGENTIC_OPS_GH_BIN:-gh}" || exit 1
    release_require_repo "$repo_root" || exit 1
    release_require_branch "$repo_root" develop || exit 1
    release_require_clean "$repo_root" || exit 1
    workflow_mode="$(release_workflow_mode "$configure_workflow" "$allow_soft_gate")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_synced_branch "$repo_root" develop || exit 1
    release_require_existing_version_tag "$repo_root" "$version" || exit 1
    protection_mode="hard"
    release_source_branch="develop"
    release_head="$(git -C "$repo_root" rev-parse HEAD)"
    if [ "$allow_soft_gate" = "true" ]; then
      protection_mode="soft"
      release_source_branch="release/$version"
      release_resolve_fixed_branch "$repo_root" "$release_source_branch" "$release_head" || exit 1
      release_head="$RELEASE_FIXED_HEAD"
      if ! git -C "$repo_root" merge-base --is-ancestor "$RELEASE_TAG_COMMIT" "$release_head"; then
        release_fail "release_tag_conflict" "tag_validation" "本地 Tag $version 不是固定发布 HEAD 的祖先" "请人工核查版本基线与发布分支"
        exit 1
      fi
    fi
    release_run_full_verification "$repo_root" "$release_head" || exit 1
    release_confirm_publish "$repo_root" "$version" "$release_head" "$confirm_release" "$release_source_branch" main || exit 1
    if ! git -C "$repo_root" push origin develop; then
      release_fail "release_develop_push_failed" "develop_push" "无法推送 develop" "请检查远端权限和分支状态后重试"
      exit 1
    fi
    if [ "$allow_soft_gate" = "true" ]; then
      release_push_fixed_branch "$repo_root" "$release_source_branch" "$release_head" || exit 1
    fi
    release_repository="${AGENTIC_OPS_RELEASE_REPOSITORY:-tapstate/agentic-ops}"
    release_find_or_create_pr "$release_repository" "$release_source_branch" main "$release_head" "$version" release "" "$protection_mode" || exit 1
    if [ "$allow_soft_gate" = "true" ]; then
      manual_status=0
      release_wait_for_manual_merge "$repo_root" "$release_repository" release_publish "$version" "$release_head" "$release_source_branch" || manual_status=$?
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
    release_verify_remote_contains "$repo_root" "$release_head" || exit 1
    if [ "$allow_soft_gate" = "true" ]; then
      release_verify_merge_commit "$repo_root" "$release_head" "$RELEASE_MERGE_COMMIT" || exit 1
    fi
    release_push_tag_if_needed "$repo_root" "$version" || exit 1
    release_write_audit "$repo_root" "$version" "$release_head" "$protection_mode"
    printf '{"ok":true,"operation":"release_publish","version":"%s","head":"%s","pr_number":%s,"pr_url":"%s","merge_commit":"%s","tag":"%s","protection_mode":"%s","audit_file":"%s","agentic_next_action":"release_completed"}\n' \
      "$version" "$release_head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_MERGE_COMMIT" "$version" "$protection_mode" "$RELEASE_AUDIT_FILE"
    ;;
  *)
    release_fail "invalid_release_command" "argument_parsing" "不支持的发布子命令 $command_name" "请使用 prepare 或 publish"
    exit 1
    ;;
esac
