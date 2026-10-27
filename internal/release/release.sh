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
confirm_release="false"
merged_pr=""
confirm_recovery=""
RELEASE_JIRA_ID=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --jira)
      [ "$#" -ge 2 ] || exit 1
      RELEASE_JIRA_ID="$2"
      release_validate_jira_id "$RELEASE_JIRA_ID" || exit 1
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
      release_fail "release_configuration_separate" "argument_parsing" "发布入口不修改仓库治理设置" "请单独审查并授权仓库配置；本地 prepare 不需要此参数"
      exit 1
      ;;
    --confirm-release)
      confirm_release="true"
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

if [ -n "$merged_pr" ]; then
  case "$command_name" in
    inspect|verify|recover) ;;
    *) release_fail invalid_release_argument argument_parsing "该子命令不接受 --merged-pr" "指定已合并 PR 时请使用 inspect、verify 或 recover"; exit 1 ;;
  esac
fi
if [ -n "$confirm_recovery" ] && [ "$command_name" != recover ]; then
  release_fail invalid_release_argument argument_parsing "--confirm-recovery 只适用于 recover" "请使用原 recover 输出的继续命令"
  exit 1
fi

RELEASE_IDENTITY="${version:-$RELEASE_JIRA_ID}"
RELEASE_BASE_COMMIT=""
if [ "$command_name" != inspect ]; then release_lock_operation "$repo_root" || exit 1; fi

if { [ "$command_name" = prepare ] || [ "$command_name" = verify ]; } && [ -z "$version" ] && [ -n "$RELEASE_JIRA_ID" ]; then
  : # Hotfix 的本地验证复用同一入口，不要求虚构版本号。
else
  release_validate_version "$version" || exit 1
fi
release_require_repo "$repo_root" || exit 1
case "$command_name" in
  inspect)
    release_inspect_state "$repo_root" "$version" "$merged_pr" || exit 1
    ;;
  prepare|verify)
    if [ -n "$merged_pr" ]; then
      [ "$command_name" = verify ] || exit 1
      release_read_merged_pr "$merged_pr" || exit 1
      [ "$RELEASE_RECOVERY_PR_STATE" = MERGED ] && [ "$RELEASE_RECOVERY_PR_BASE" = main ] || exit 1
      git -C "$repo_root" fetch origin main >/dev/null 2>&1 || exit 1
      head="$RELEASE_RECOVERY_PR_HEAD"
      RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$RELEASE_RECOVERY_MERGE_COMMIT^1")"
      release_validate_pr_binding "$RELEASE_RECOVERY_PR_URL" "$RELEASE_IDENTITY" "$head" || exit 1
      release_verify_merge_commit "$repo_root" "$head" "$RELEASE_RECOVERY_MERGE_COMMIT" || exit 1
    else
      release_require_branch "$repo_root" develop || exit 1
      release_require_clean "$repo_root" || exit 1
      head="$(git -C "$repo_root" rev-parse develop)"
      # 本地验证不依赖远端同步、合并条件或版本引用是否可创建。
      RELEASE_BASE_COMMIT="$head"
    fi
    mode=ensure
    if [ "$command_name" = verify ]; then mode=verify; fi
    release_run_full_verification "$repo_root" "$head" "$mode" || exit 1
    printf '{"ok":true,"operation":"release_%s","head":"%s","source_branch":"develop","verified_at":"%s","delivery_checks":"pending"}\n' "$command_name" "$head" "$RELEASE_VERIFIED_AT"
    ;;
  submit-for-review|publish)
    release_find_delivery_pr "$version" || exit 1
    if [ "$RELEASE_PR_STATE" = MERGED ]; then
      if [ "$command_name" = submit-for-review ]; then
        release_fail release_candidate_already_in_main preflight "原 PR 已合并" "使用 publish --confirm-release 完成 Tag 和存档"
        exit 1
      fi
      head="$RELEASE_PR_HEAD"
      git -C "$repo_root" fetch origin main >/dev/null 2>&1 || exit 1
      RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$RELEASE_MERGE_COMMIT^1")"
      release_validate_pr_binding "$RELEASE_PR_URL" "$version" "$head" || exit 1
      release_verify_merge_commit "$repo_root" "$head" "$RELEASE_MERGE_COMMIT" || exit 1
      release_run_full_verification "$repo_root" "$head" check "$RELEASE_PR_NUMBER" || exit 1
      workflow_check_delivery tapstate/agentic-ops hard || exit 1
      release_check_review_objections "$RELEASE_PR_NUMBER" || exit 1
      release_confirm_publish "$repo_root" "$version" "$head" "$confirm_release" develop main "创建版本 Tag 与存档" || exit 1
      release_finish "$repo_root" "$version" "$head" "$RELEASE_MERGE_COMMIT" hard release_publish || exit 1
    else
      [ -z "$RELEASE_PR_STATE" ] || [ "$RELEASE_PR_STATE" = OPEN ] || {
        release_fail release_pr_closed preflight "原 PR 已关闭但未合并" "核对原 PR，不自动更换候选"; exit 1;
      }
      release_require_branch "$repo_root" develop || exit 1
      release_require_clean "$repo_root" || exit 1
      release_require_synced_branch "$repo_root" develop || exit 1
      head="$(git -C "$repo_root" rev-parse develop)"
      release_preflight_candidate "$repo_root" "$head" || exit 1
      release_submit_review "$repo_root" tapstate/agentic-ops "$version" develop "$head" hard "$confirm_release" || exit 1
      payload="$(printf '{"ok":true,"operation":"release_submit_for_review","status":"waiting_for_manual_merge","head":"%s","pr_number":%s,"pr_url":"%s","source_branch":"develop","protection_mode":"hard","delivery_checks":"pending","continue_command":"internal/release/release.sh publish --version %s --confirm-release"}\n' "$head" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$version")"
      release_result_json "$payload"
    fi
    ;;
  recover)
    release_recover_merged_candidate "$repo_root" "$version" "$merged_pr" "$confirm_release" "$confirm_recovery" hard || exit 1
    ;;
  *)
    release_fail invalid_release_command argument_parsing "不支持的发布子命令 $command_name" "使用 prepare、submit-for-review、publish、inspect、verify 或 recover"
    exit 1
    ;;
esac
