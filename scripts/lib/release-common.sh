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

release_require_existing_version_tag() {
  local repo_root="$1"
  local version="$2"
  local tag_commit
  local remote_tag
  local remote_tag_commit

  if ! git -C "$repo_root" show-ref --verify --quiet "refs/tags/$version"; then
    release_fail "release_tag_missing" "tag_validation" "本地 Tag $version 不存在" "请先执行 scripts/release.sh prepare --version $version 并提交生成资源"
    return 1
  fi
  if [ "$(git -C "$repo_root" cat-file -t "refs/tags/$version")" != "tag" ]; then
    release_fail "release_tag_conflict" "tag_validation" "本地 $version 不是 annotated tag" "请人工检查本地 Tag"
    return 1
  fi
  tag_commit="$(git -C "$repo_root" rev-list -n 1 "$version")"
  if ! git -C "$repo_root" merge-base --is-ancestor "$tag_commit" HEAD; then
    release_fail "release_tag_conflict" "tag_validation" "本地 Tag $version 不是当前发布 HEAD 的祖先" "请人工检查版本线基线"
    return 1
  fi

  remote_tag="$(git -C "$repo_root" ls-remote --tags --refs origin "refs/tags/$version" 2>/dev/null | awk '{print $1}')"
  if [ -n "$remote_tag" ]; then
    remote_tag_commit="$(git -C "$repo_root" ls-remote --tags origin "refs/tags/$version^{}" 2>/dev/null | awk '{print $1}')"
    if [ -z "$remote_tag_commit" ] || [ "$remote_tag_commit" != "$tag_commit" ]; then
      release_fail "release_tag_remote_conflict" "tag_validation" "远端 Tag $version 已存在但目标不一致" "禁止移动或覆盖远端 Tag，请人工核查"
      return 1
    fi
    RELEASE_TAG_REMOTE_EXISTS="true"
  else
    RELEASE_TAG_REMOTE_EXISTS="false"
  fi
  RELEASE_TAG_COMMIT="$tag_commit"
}

release_run_full_verification() {
  local repo_root="$1"
  local head="$2"
  local temp_root
  local worktree_path
  local verification_status=0

  temp_root="$(mktemp -d)"
  worktree_path="$temp_root/worktree"
  if ! git -C "$repo_root" worktree add --detach "$worktree_path" "$head" >/dev/null 2>&1; then
    rm -rf "$temp_root"
    release_fail "release_worktree_failed" "verification" "无法创建发布验证 worktree" "请检查 Git worktree 状态后重试"
    return 1
  fi

  (
    cd "$worktree_path"
    go test ./... &&
      bash scripts/test-resources.sh &&
      bash scripts/test-build.sh &&
      bash scripts/test-install.sh &&
      bash tests/e2e/ao-profile-flow.sh &&
      bash tests/e2e/local-fake-flow.sh &&
      bash tests/e2e/local-install-flow.sh &&
      bash tests/e2e/problem-resolution-flow.sh
  ) || verification_status=$?

  git -C "$repo_root" worktree remove --force "$worktree_path" >/dev/null 2>&1 || true
  rm -rf "$temp_root"
  if [ "$verification_status" -ne 0 ]; then
    release_fail "release_verification_failed" "verification" "固定完整发布验证失败" "请修复失败项并从 publish 重新执行，当前尚未产生远端写入"
    return 1
  fi
  RELEASE_VERIFIED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}

release_confirm_publish() {
  local repo_root="$1"
  local version="$2"
  local head="$3"
  local confirmed="$4"
  local answer

  printf '即将发布 AgenticOps %s\n' "$version" >&2
  printf '仓库：%s\n' "${AGENTIC_OPS_RELEASE_REPOSITORY:-tapstate/agentic-ops}" >&2
  printf '发布 HEAD：%s\n' "$head" >&2
  printf '完整验证：通过（%s）\n' "$RELEASE_VERIFIED_AT" >&2
  printf '待发布提交：\n' >&2
  git -C "$repo_root" log --format='  %h %s' "refs/remotes/origin/main..$head" >&2 || true

  if [ "$confirmed" = "true" ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    release_fail "release_confirmation_required" "confirmation" "非交互发布缺少最终确认" "确认展示内容后显式传入 --confirm-release"
    return 1
  fi
  printf '确认执行 develop -> main 发布并在合并后推送 %s？[y/N] ' "$version" >&2
  if ! IFS= read -r answer; then
    answer=""
  fi
  case "$answer" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      release_fail "release_confirmation_rejected" "confirmation" "研发工程师取消了本次发布" "确认发布内容后重新执行 publish"
      return 1
      ;;
  esac
}

release_write_pr_body() {
  local body_file="$1"
  local version="$2"
  local source_branch="$3"
  local target_branch="$4"
  local head="$5"

  cat > "$body_file" <<EOF
## 发布证据

- 版本基线：\`$version\`
- 源分支：\`$source_branch\`
- 目标分支：\`$target_branch\`
- 待合并 HEAD：\`$head\`
- 本地验证完成时间（UTC）：\`$RELEASE_VERIFIED_AT\`

### 固定完整验证

- \`go test ./...\`
- \`bash scripts/test-resources.sh\`
- \`bash scripts/test-build.sh\`
- \`bash scripts/test-install.sh\`
- \`bash tests/e2e/ao-profile-flow.sh\`
- \`bash tests/e2e/local-fake-flow.sh\`
- \`bash tests/e2e/local-install-flow.sh\`
- \`bash tests/e2e/problem-resolution-flow.sh\`

以上命令全部通过。
EOF
}

release_find_or_create_pr() {
  local repository="$1"
  local source_branch="$2"
  local target_branch="$3"
  local head="$4"
  local version="$5"
  local existing
  local body_file

  existing="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list \
    --repo "$repository" \
    --base "$target_branch" \
    --head "$source_branch" \
    --state all \
    --json number,url,state,headRefOid \
    --jq ".[] | select(.headRefOid == \"$head\") | [.number, .url, .state] | @tsv" 2>/dev/null | head -n 1)"
  if [ -n "$existing" ]; then
    IFS=$'\t' read -r RELEASE_PR_NUMBER RELEASE_PR_URL RELEASE_PR_STATE <<< "$existing"
    return 0
  fi

  body_file="$(mktemp)"
  release_write_pr_body "$body_file" "$version" "$source_branch" "$target_branch" "$head"
  if ! RELEASE_PR_URL="$("${AGENTIC_OPS_GH_BIN:-gh}" pr create \
    --repo "$repository" \
    --base "$target_branch" \
    --head "$source_branch" \
    --title "Release: $version 合并 $source_branch 到 $target_branch" \
    --body-file "$body_file")"; then
    rm -f "$body_file"
    release_fail "release_pr_create_failed" "pull_request" "无法创建发布 PR" "请检查 GitHub 权限和现有 PR 后重试"
    return 1
  fi
  rm -f "$body_file"
  RELEASE_PR_NUMBER="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$RELEASE_PR_URL" --repo "$repository" --json number --jq .number)" || {
    release_fail "release_pr_read_failed" "pull_request" "发布 PR 已创建但无法读取编号" "请检查 PR 后重新执行 publish"
    return 1
  }
  RELEASE_PR_STATE="OPEN"
}

release_enable_auto_merge() {
  local repository="$1"
  if [ "$RELEASE_PR_STATE" = "MERGED" ]; then
    return 0
  fi
  if ! "${AGENTIC_OPS_GH_BIN:-gh}" pr merge "$RELEASE_PR_URL" --repo "$repository" --merge --auto; then
    release_fail "release_auto_merge_failed" "auto_merge" "无法为发布 PR 启用 Merge Auto-merge" "请检查 PR 合并条件后重新执行 publish"
    return 1
  fi
}

release_wait_for_merge() {
  local repository="$1"
  local attempt=0
  local result
  local state
  local merge_commit
  local url
  local number

  while [ "$attempt" -lt 120 ]; do
    result="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$RELEASE_PR_URL" \
      --repo "$repository" \
      --json state,mergeCommit,url,number \
      --jq '[.state, (.mergeCommit.oid // ""), .url, .number] | @tsv')" || {
      release_fail "release_pr_read_failed" "merge_wait" "无法读取发布 PR 状态" "请检查 GitHub 状态后重新执行 publish"
      return 1
    }
    IFS=$'\t' read -r state merge_commit url number <<< "$result"
    if [ "$state" = "MERGED" ]; then
      RELEASE_MERGE_COMMIT="$merge_commit"
      RELEASE_PR_URL="$url"
      RELEASE_PR_NUMBER="$number"
      return 0
    fi
    if [ "$state" = "CLOSED" ]; then
      release_fail "release_pr_closed" "merge_wait" "发布 PR 已关闭但未合并" "请恢复或重新创建 PR 后重试"
      return 1
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  release_fail "release_merge_timeout" "merge_wait" "等待发布 PR 合并超时" "请检查 PR 门禁，处理后重新执行 publish"
  return 1
}

release_verify_remote_contains() {
  local repo_root="$1"
  local commit="$2"
  if ! git -C "$repo_root" fetch origin main >/dev/null 2>&1; then
    release_fail "release_main_fetch_failed" "merge_verification" "无法刷新 origin/main" "请检查网络后重新执行 publish"
    return 1
  fi
  if ! git -C "$repo_root" merge-base --is-ancestor "$commit" refs/remotes/origin/main; then
    release_fail "release_main_missing_head" "merge_verification" "origin/main 尚未包含发布 HEAD" "请核查 PR 的实际合并结果后重试"
    return 1
  fi
}

release_push_tag_if_needed() {
  local repo_root="$1"
  local version="$2"
  if [ "$RELEASE_TAG_REMOTE_EXISTS" = "true" ]; then
    return 0
  fi
  if ! git -C "$repo_root" push origin "refs/tags/$version"; then
    release_fail "release_tag_push_failed" "tag_push" "main 已合并但 Tag $version 推送失败" "请修复远端权限后重新执行 publish，禁止强推 Tag"
    return 1
  fi
}

release_write_audit() {
  local repo_root="$1"
  local version="$2"
  local head="$3"
  local audit_dir="$repo_root/.local/release-runs"
  local audit_file="$audit_dir/release-$version-$head.json"
  mkdir -p "$audit_dir"
  printf '{"operation":"release_publish","status":"completed","version":"%s","head":"%s","verified_at":"%s","pr_number":%s,"pr_url":"%s","merge_commit":"%s","tag":"%s","tag_commit":"%s"}\n' \
    "$version" "$head" "$RELEASE_VERIFIED_AT" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_MERGE_COMMIT" "$version" "$RELEASE_TAG_COMMIT" > "$audit_file"
  RELEASE_AUDIT_FILE="$audit_file"
}

release_validate_jira_id() {
  local jira_id="$1"
  if ! printf '%s\n' "$jira_id" | grep -Eq '^[A-Z][A-Z0-9]+-[1-9][0-9]*$'; then
    release_fail "invalid_jira_id" "hotfix_branch" "Jira ID 格式无效" "请使用例如 AO-123 或 TAP-12371 的大写任务编号"
    return 1
  fi
}

release_normalize_git_user() {
  local raw_user="$1"
  local normalized_user
  normalized_user="$(printf '%s' "$raw_user" |
    LC_ALL=C tr '[:upper:]' '[:lower:]' |
    sed -E 's/[[:space:]_.]+/-/g; s/-+/-/g; s/^-//; s/-$//')"
  if ! printf '%s\n' "$normalized_user" | grep -Eq '^[a-z0-9][a-z0-9-]*$'; then
    release_fail "invalid_git_user" "hotfix_branch" "Git 用户名无法转换为安全分支片段" "请通过 --user 提供小写字母、数字和连字符组成的用户名"
    return 1
  fi
  printf '%s\n' "$normalized_user"
}

release_parse_hotfix_branch() {
  local branch="$1"
  local user_part
  local remainder
  local jira_part

  case "$branch" in
    */*/fix-main)
      user_part="${branch%%/*}"
      remainder="${branch#*/}"
      jira_part="${remainder%%/*}"
      ;;
    *)
      release_fail "invalid_hotfix_branch" "hotfix_branch" "当前分支不符合 <user>/<jira-id>/fix-main" "请使用 scripts/hotfix.sh create 创建修复分支"
      return 1
      ;;
  esac
  if [ "$branch" != "$user_part/$jira_part/fix-main" ] ||
    ! printf '%s\n' "$user_part" | grep -Eq '^[a-z0-9][a-z0-9-]*$' ||
    ! printf '%s\n' "$jira_part" | grep -Eq '^[A-Z][A-Z0-9]+-[1-9][0-9]*$'; then
    release_fail "invalid_hotfix_branch" "hotfix_branch" "当前分支不符合 <user>/<jira-id>/fix-main" "请使用 scripts/hotfix.sh create 创建修复分支"
    return 1
  fi
  HOTFIX_USER="$user_part"
  HOTFIX_JIRA_ID="$jira_part"
  HOTFIX_BRANCH="$branch"
}

release_require_main_base() {
  local repo_root="$1"
  if ! git -C "$repo_root" fetch origin main >/dev/null 2>&1; then
    release_fail "hotfix_main_fetch_failed" "hotfix_base" "无法刷新 origin/main" "请检查网络和远端权限后重试"
    return 1
  fi
  if ! git -C "$repo_root" merge-base --is-ancestor refs/remotes/origin/main HEAD; then
    release_fail "hotfix_main_not_current" "hotfix_base" "修复分支未包含最新 origin/main" "请重新从最新 main 创建修复分支或人工处理基线"
    return 1
  fi
}

release_find_iteration_tag() {
  local repo_root="$1"
  local candidate
  if ! git -C "$repo_root" fetch origin main --tags >/dev/null 2>&1; then
    release_fail "hotfix_main_fetch_failed" "version_baseline" "无法刷新 main 和远端 Tag" "请检查网络后重试"
    return 1
  fi
  for candidate in $(git -C "$repo_root" tag --merged refs/remotes/origin/main --sort=-version:refname); do
    if printf '%s\n' "$candidate" | grep -Eq '^v[0-9]+\.[0-9]+$' &&
      [ "$(git -C "$repo_root" cat-file -t "refs/tags/$candidate" 2>/dev/null || true)" = "tag" ] &&
      [ -n "$(git -C "$repo_root" ls-remote --tags --refs origin "refs/tags/$candidate" 2>/dev/null)" ]; then
      HOTFIX_VERSION="$candidate"
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  release_fail "iteration_tag_missing" "version_baseline" "origin/main 历史中没有可复用的 annotated vX.Y Tag" "请先完成一个正常版本发布"
  return 1
}
