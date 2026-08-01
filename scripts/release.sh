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
    workflow_mode="$(release_workflow_mode "$configure_workflow")"
    workflow_check_or_configure "$workflow_mode" "$repo_root" >/dev/null || exit 1
    release_require_synced_branch "$repo_root" develop || exit 1
    release_require_version_tag "$repo_root" "$version" || exit 1
    release_build_assets "$repo_root" || exit 1
    printf '{"ok":true,"operation":"release_prepare","version":"%s","tag_scope":"local","agentic_next_action":"review_and_commit_generated_assets"}\n' "$version"
    ;;
  publish)
    release_fail "release_publish_not_implemented" "argument_parsing" "publish 尚未实现" "请先完成发布脚本实施计划 Task 4"
    exit 1
    ;;
  *)
    release_fail "invalid_release_command" "argument_parsing" "不支持的发布子命令 $command_name" "请使用 prepare 或 publish"
    exit 1
    ;;
esac
