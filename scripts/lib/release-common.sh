#!/usr/bin/env bash

release_fail() {
  local code="$1"
  local stage="$2"
  local message="$3"
  local action="$4"
  printf '{"ok":false,"operation":"source_release","code":"%s","current_stage":"%s","message":"%s","required_human_action":"%s"}\n' \
    "$code" "$stage" "$message" "$action" >&2
  return 1
}

release_require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    release_fail "release_dependency_missing" "preflight" "缺少命令 $command_name" "请安装 $command_name 后重试"
    return 1
  fi
}

release_normalize_repository() {
  local remote_url="$1"
  case "$remote_url" in
    git@github.com:*)
      remote_url="${remote_url#git@github.com:}"
      ;;
    https://github.com/*)
      remote_url="${remote_url#https://github.com/}"
      ;;
    ssh://git@github.com/*)
      remote_url="${remote_url#ssh://git@github.com/}"
      ;;
    *)
      printf '%s\n' ""
      return 0
      ;;
  esac
  printf '%s\n' "${remote_url%.git}"
}

release_require_repo() {
  local repo_root="$1"
  local expected_repository="${AGENTIC_OPS_RELEASE_REPOSITORY:-tapstate/agentic-ops}"
  local actual_root
  local canonical_repo_root
  local remote_url
  local actual_repository

  actual_root="$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null)" || {
    release_fail "release_repository_required" "preflight" "当前目录不在 Git 仓库中" "请在 tapstate/agentic-ops 源头仓库执行"
    return 1
  }
  canonical_repo_root="$(cd "$repo_root" && pwd -P)"
  if [ "$actual_root" != "$canonical_repo_root" ]; then
    release_fail "release_repository_required" "preflight" "发布脚本必须从仓库根目录执行" "请切换到 $actual_root 后重试"
    return 1
  fi
  remote_url="$(git -C "$repo_root" config --get remote.origin.url 2>/dev/null || true)"
  actual_repository="$(release_normalize_repository "$remote_url")"
  if [ "$actual_repository" != "$expected_repository" ]; then
    release_fail "release_repository_mismatch" "preflight" "origin 不是 $expected_repository" "请检查当前仓库和 origin 配置"
    return 1
  fi
}

release_require_clean() {
  local repo_root="$1"
  if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
    release_fail "dirty_worktree" "preflight" "工作区存在未提交变更" "请审查并提交或处理全部变更后重试"
    return 1
  fi
}

release_require_branch() {
  local repo_root="$1"
  local expected_branch="$2"
  local actual_branch
  actual_branch="$(git -C "$repo_root" branch --show-current)"
  if [ "$actual_branch" != "$expected_branch" ]; then
    release_fail "wrong_release_branch" "preflight" "当前分支不是 $expected_branch" "请切换到 $expected_branch 后重试"
    return 1
  fi
}

release_validate_version() {
  local version="$1"
  if ! printf '%s\n' "$version" | grep -Eq '^v[0-9]+\.[0-9]+$'; then
    release_fail "invalid_release_version" "version_validation" "版本必须使用 vX.Y 二段式格式" "请提供例如 --version v0.3"
    return 1
  fi
}

release_require_synced_branch() {
  local repo_root="$1"
  local branch="$2"
  local local_head
  local remote_head

  if ! git -C "$repo_root" fetch origin "$branch" >/dev/null 2>&1; then
    release_fail "release_fetch_failed" "branch_sync" "无法读取 origin/$branch" "请检查网络和远端权限后重试"
    return 1
  fi
  local_head="$(git -C "$repo_root" rev-parse HEAD)"
  remote_head="$(git -C "$repo_root" rev-parse "refs/remotes/origin/$branch" 2>/dev/null || true)"
  if [ -z "$remote_head" ]; then
    release_fail "release_remote_branch_missing" "branch_sync" "远端分支 origin/$branch 不存在" "请先完成正式研发流程配置"
    return 1
  fi
  if [ "$local_head" = "$remote_head" ]; then
    return 0
  fi
  if git -C "$repo_root" merge-base --is-ancestor "$remote_head" "$local_head"; then
    return 0
  fi
  if git -C "$repo_root" merge-base --is-ancestor "$local_head" "$remote_head"; then
    release_fail "branch_behind_remote" "branch_sync" "本地 $branch 落后于 origin/$branch" "请人工同步远端变更并重新验证"
    return 1
  fi
  release_fail "branch_diverged" "branch_sync" "本地 $branch 与 origin/$branch 已分叉" "请人工处理分叉，不要由发布脚本自动 merge 或 rebase"
  return 1
}

release_workflow_mode() {
  local configure_workflow="$1"
  if [ "$configure_workflow" = "true" ]; then
    printf 'configure\n'
  elif [ -t 0 ]; then
    printf 'interactive\n'
  else
    printf 'check\n'
  fi
}

release_require_version_tag() {
  local repo_root="$1"
  local version="$2"
  local tag_commit

  if [ -n "$(git -C "$repo_root" ls-remote --tags --refs origin "refs/tags/$version" 2>/dev/null)" ]; then
    release_fail "release_tag_remote_exists" "tag_validation" "远端 Tag $version 已存在" "请使用新的二段式版本，禁止移动或覆盖远端 Tag"
    return 1
  fi

  if git -C "$repo_root" show-ref --verify --quiet "refs/tags/$version"; then
    if [ "$(git -C "$repo_root" cat-file -t "refs/tags/$version")" != "tag" ]; then
      release_fail "release_tag_conflict" "tag_validation" "本地 $version 不是 annotated tag" "请人工检查本地 Tag"
      return 1
    fi
    tag_commit="$(git -C "$repo_root" rev-list -n 1 "$version")"
    if ! git -C "$repo_root" merge-base --is-ancestor "$tag_commit" HEAD; then
      release_fail "release_tag_conflict" "tag_validation" "本地 Tag $version 不是当前分支祖先" "请人工检查版本线基线"
      return 1
    fi
    return 0
  fi

  if ! git -C "$repo_root" tag -a "$version" -m "AgenticOps $version version baseline"; then
    release_fail "release_tag_create_failed" "tag_creation" "无法创建本地 Tag $version" "请检查本地 Git 状态后重试"
    return 1
  fi
}

release_build_assets() {
  local repo_root="$1"
  if ! (cd "$repo_root" && bash scripts/build.sh); then
    release_fail "release_build_failed" "build" "四平台安装资源构建失败" "请保留本地 Tag，修复构建问题后重新执行 prepare"
    return 1
  fi
}
