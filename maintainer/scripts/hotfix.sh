#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/../.." && pwd -P)"

# shellcheck source=maintainer/scripts/lib/release-common.sh
. "$script_dir/lib/release-common.sh"

command_name="${1:-}"
if [ "$command_name" != "publish" ]; then
  release_fail "invalid_hotfix_command" "argument_parsing" \
    "Hotfix 只支持 publish" \
    "请使用 maintainer/scripts/hotfix.sh publish --jira-id AO-123"
  exit 1
fi
shift

jira_id=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --jira-id)
      if [ "$#" -lt 2 ]; then
        release_fail "invalid_jira_id" "argument_parsing" \
          "--jira-id 缺少值" "请提供例如 --jira-id AO-123"
        exit 1
      fi
      jira_id="$2"
      shift 2
      ;;
    *)
      release_fail "invalid_hotfix_argument" "argument_parsing" \
        "不支持的参数 $1" \
        "Hotfix 只接受 publish --jira-id <KEY>"
      exit 1
      ;;
  esac
done

release_validate_jira_id "$jira_id" || exit 1
release_require_command git || exit 1
release_require_repo "$repo_root" || exit 1
release_require_clean "$repo_root" || exit 1

current_branch="$(git -C "$repo_root" branch --show-current)"
if [ "$current_branch" != "develop" ]; then
  release_fail "hotfix_requires_develop" "hotfix_publish" \
    "Hotfix 必须从 develop 执行" \
    "请切换到 develop，并确保工作区干净后重试"
  exit 1
fi

if ! git -C "$repo_root" fetch origin main develop >/dev/null 2>&1; then
  release_fail "hotfix_base_fetch_failed" "hotfix_publish" \
    "无法刷新 origin/main 或 origin/develop" \
    "请检查网络和远端权限后重试"
  exit 1
fi

local_develop="$(git -C "$repo_root" rev-parse refs/heads/develop)"
remote_develop="$(git -C "$repo_root" rev-parse refs/remotes/origin/develop)"
remote_main="$(git -C "$repo_root" rev-parse refs/remotes/origin/main)"

if [ "$local_develop" != "$remote_develop" ]; then
  release_fail "hotfix_develop_not_synced" "hotfix_publish" \
    "本地 develop 与 origin/develop 不一致" \
    "请先同步 develop；Hotfix 不会猜测、改写或遗漏尚未推送的提交"
  exit 1
fi

if [ "$remote_main" = "$remote_develop" ]; then
  printf '{"ok":true,"operation":"hotfix_publish","status":"completed","jira_id":"%s","merge_commit":"%s","main_commit":"%s","develop_commit":"%s","changed":false,"branch_created":false,"jira_interaction":false,"gate":"none","agentic_next_action":"hotfix_completed"}\n' \
    "$jira_id" "$remote_main" "$remote_main" "$remote_develop"
  exit 0
fi

merge_subject="Hotfix: $jira_id 合并 develop 到 main"
merge_body="将 origin/develop 的已提交变更直接合入 main。\n\nJira: $jira_id\n流程: direct-develop-to-main\nJira 交互: none"
merge_tree_output=""
if ! merge_tree_output="$(
  git -C "$repo_root" merge-tree --write-tree "$remote_main" "$remote_develop"
)"; then
  release_fail "hotfix_merge_conflict" "hotfix_publish" \
    "origin/main 与 origin/develop 无法自动合并" \
    "请先在 develop 解决冲突并推送；Hotfix 不执行交互式冲突处理、rebase、cherry-pick 或强推"
  exit 1
fi
merge_tree="$(printf '%s\n' "$merge_tree_output" | sed -n '1p')"
if ! git -C "$repo_root" cat-file -e "$merge_tree^{tree}" 2>/dev/null; then
  release_fail "hotfix_merge_tree_invalid" "hotfix_publish" \
    "无法生成 main 与 develop 的合并结果" \
    "请检查 Git 版本和仓库对象完整性后重试"
  exit 1
fi
merge_commit="$(
  printf '%s\n\n%b\n' "$merge_subject" "$merge_body" |
    git -C "$repo_root" commit-tree "$merge_tree" \
      -p "$remote_main" \
      -p "$remote_develop"
)"

if ! AGENTIC_OPS_SPECIAL_PUSH=hotfix \
  git -C "$repo_root" push --atomic origin \
    "$merge_commit:refs/heads/main" \
    "$merge_commit:refs/heads/develop"; then
  release_fail "hotfix_direct_push_failed" "hotfix_publish" \
    "无法将 Hotfix Merge commit 原子推送到 main 和 develop" \
    "远端引用未部分更新；请检查权限、分支保护和网络后重试"
  exit 1
fi

if ! git -C "$repo_root" update-ref refs/heads/develop "$merge_commit" "$local_develop"; then
  release_fail "hotfix_local_develop_sync_failed" "hotfix_publish" \
    "远端已完成合并，但本地 develop 未能快进" \
    "请执行 git fetch origin develop 后快进本地 develop；不要重复推送"
  exit 1
fi

if ! git -C "$repo_root" fetch origin main develop >/dev/null 2>&1 ||
  [ "$(git -C "$repo_root" rev-parse refs/remotes/origin/main)" != "$merge_commit" ] ||
  [ "$(git -C "$repo_root" rev-parse refs/remotes/origin/develop)" != "$merge_commit" ]; then
  release_fail "hotfix_remote_readback_failed" "hotfix_publish" \
    "Hotfix 已推送，但远端 main/develop 回读不一致" \
    "请检查远端引用；在确认事实前不要重复执行 Hotfix"
  exit 1
fi

printf '{"ok":true,"operation":"hotfix_publish","status":"completed","jira_id":"%s","merge_commit":"%s","main_commit":"%s","develop_commit":"%s","changed":true,"branch_created":false,"jira_interaction":false,"gate":"none","merge_subject":"%s","agentic_next_action":"hotfix_completed"}\n' \
  "$jira_id" "$merge_commit" "$merge_commit" "$merge_commit" "$merge_subject"
