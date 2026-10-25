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

# story approval/evidence 来自发布 worktree 外部。不得让 `mkdir -p` 或 `cp`
# 跟随 source/candidate 快照中的祖先链接；逐级检查文件类型并校验物理路径
# containment 后，才允许把普通 JSON 复制进固定 candidate。
RELEASE_STORY_STATE_ERROR=""
RELEASE_TRUST_ROOT_CHANGED="false"

release_story_require_plain_directory_chain() {
  local root="$1"
  local relative="$2"
  local label="$3"
  local root_real
  local current
  local current_real
  local component
  local components=()

  case "$relative" in
    ""|/*|*..*)
      RELEASE_STORY_STATE_ERROR="$label 使用了非法相对路径"
      return 1
      ;;
  esac
  if [ -L "$root" ] || [ ! -d "$root" ]; then
    RELEASE_STORY_STATE_ERROR="$label 的信任根不是普通目录"
    return 1
  fi
  root_real="$(cd "$root" 2>/dev/null && pwd -P)" || {
    RELEASE_STORY_STATE_ERROR="$label 的信任根无法解析真实路径"
    return 1
  }
  current="$root"
  IFS='/' read -r -a components <<< "$relative"
  for component in "${components[@]}"; do
    [ -n "$component" ] || {
      RELEASE_STORY_STATE_ERROR="$label 包含空路径段"
      return 1
    }
    current="$current/$component"
    if [ -L "$current" ]; then
      RELEASE_STORY_STATE_ERROR="$label 的路径包含符号链接"
      return 1
    fi
    if [ -e "$current" ] && [ ! -d "$current" ]; then
      RELEASE_STORY_STATE_ERROR="$label 的目录位置被特殊文件占用"
      return 1
    fi
    if [ -d "$current" ]; then
      current_real="$(cd "$current" 2>/dev/null && pwd -P)" || {
        RELEASE_STORY_STATE_ERROR="$label 无法解析目录真实路径"
        return 1
      }
      case "$current_real" in
        "$root_real"|"$root_real"/*)
          ;;
        *)
          RELEASE_STORY_STATE_ERROR="$label 的真实路径逃出信任根"
          return 1
          ;;
      esac
    fi
  done
}

release_story_list_directory_entries() {
  local directory="$1"
  local entries=()
  shopt -s nullglob dotglob
  entries=("$directory"/*)
  shopt -u nullglob dotglob
  if [ "${#entries[@]}" -gt 0 ]; then
    printf '%s\n' "${entries[@]}"
  fi
}

release_story_validate_record_source() {
  local root="$1"
  local record_kind
  local record_source
  local record
  local record_name

  release_story_require_plain_directory_chain "$root" "internal" \
    "发布故事状态源" || return 1
  release_story_require_plain_directory_chain "$root" ".local/story-gate" \
    "发布故事状态源" || return 1
  for record_kind in approvals evidence; do
    record_source="$root/.local/story-gate/$record_kind"
    if [ ! -e "$record_source" ] && [ ! -L "$record_source" ]; then
      continue
    fi
    release_story_require_plain_directory_chain \
      "$root" ".local/story-gate/$record_kind" \
      "发布故事状态源" || return 1
    while IFS= read -r record; do
      [ -n "$record" ] || continue
      record_name="${record##*/}"
      if [ -L "$record" ] || [ ! -f "$record" ]; then
        RELEASE_STORY_STATE_ERROR="发布故事状态源只能包含普通 JSON 文件"
        return 1
      fi
      case "$record_name" in
        *.json)
          ;;
        *)
          RELEASE_STORY_STATE_ERROR="发布故事状态源包含非 JSON 文件"
          return 1
          ;;
      esac
    done < <(release_story_list_directory_entries "$record_source")
  done
}

release_story_prepare_record_target() {
  local root="$1"
  local record_kind="$2"
  local target="$root/.local/story-gate/$record_kind"
  local existing

  release_story_require_plain_directory_chain "$root" "internal" \
    "发布候选故事状态目标" || return 1
  release_story_require_plain_directory_chain "$root" ".local/story-gate" \
    "发布候选故事状态目标" || return 1
  release_story_require_plain_directory_chain \
    "$root" ".local/story-gate/$record_kind" \
    "发布候选故事状态目标" || return 1
  mkdir -p "$target" || {
    RELEASE_STORY_STATE_ERROR="无法创建发布候选故事状态目录"
    return 1
  }
  release_story_require_plain_directory_chain \
    "$root" ".local/story-gate/$record_kind" \
    "发布候选故事状态目标" || return 1
  while IFS= read -r existing; do
    [ -n "$existing" ] || continue
    RELEASE_STORY_STATE_ERROR="发布候选故事状态目标不是空目录"
    return 1
  done < <(release_story_list_directory_entries "$target")
}

release_story_copy_record_kind() {
  local source_root="$1"
  local target_root="$2"
  local record_kind="$3"
  local record_source="$source_root/.local/story-gate/$record_kind"
  local record_target="$target_root/.local/story-gate/$record_kind"
  local record
  local record_name
  local pending

  release_story_prepare_record_target "$target_root" "$record_kind" || return 1
  if [ ! -d "$record_source" ]; then
    return 0
  fi
  while IFS= read -r record; do
    [ -n "$record" ] || continue
    record_name="${record##*/}"
    if [ -L "$record" ] || [ ! -f "$record" ]; then
      RELEASE_STORY_STATE_ERROR="复制前发布故事状态源已变为非普通文件"
      return 1
    fi
    pending="$(mktemp "$record_target/.record.XXXXXX")" || {
      RELEASE_STORY_STATE_ERROR="无法在发布候选快照中创建状态临时文件"
      return 1
    }
    if ! cp "$record" "$pending" || ! chmod 0600 "$pending" || \
      ! mv -f "$pending" "$record_target/$record_name"; then
      rm -f "$pending"
      RELEASE_STORY_STATE_ERROR="无法安全复制发布故事状态"
      return 1
    fi
    if [ -L "$record_target/$record_name" ] || \
      [ ! -f "$record_target/$record_name" ]; then
      RELEASE_STORY_STATE_ERROR="发布候选快照中的故事状态不是普通文件"
      return 1
    fi
    release_story_require_plain_directory_chain \
      "$target_root" ".local/story-gate/$record_kind" \
      "发布候选故事状态目标" || return 1
  done < <(release_story_list_directory_entries "$record_source")
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
  local expected_repository="tapstate/agentic-ops"
  local actual_root
  local canonical_repo_root
  local remote_url
  local effective_fetch
  local effective_push
  local raw_count
  local fetch_count
  local push_count
  local actual_repository

  if [ -n "${AGENTIC_OPS_RELEASE_REPOSITORY:-}" ]; then
    release_fail "release_identity_override_forbidden" "preflight" \
      "发布仓库固定为 ${expected_repository}，不能通过环境变量覆盖" \
      "请移除 AGENTIC_OPS_RELEASE_REPOSITORY 后重试"
    return 1
  fi

  actual_root="$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null)" || {
    release_fail "release_repository_required" "preflight" "当前目录不在 Git 仓库中" "请在 tapstate/agentic-ops 源头仓库执行"
    return 1
  }
  canonical_repo_root="$(cd "$repo_root" && pwd -P)"
  if [ "$actual_root" != "$canonical_repo_root" ]; then
    release_fail "release_repository_required" "preflight" "发布脚本必须从仓库根目录执行" "请切换到 $actual_root 后重试"
    return 1
  fi
  remote_url="$(git -C "$repo_root" config --get-all remote.origin.url 2>/dev/null || true)"
  effective_fetch="$(git -C "$repo_root" remote get-url --all origin 2>/dev/null || true)"
  effective_push="$(git -C "$repo_root" remote get-url --push --all origin 2>/dev/null || true)"
  raw_count="$(printf '%s\n' "$remote_url" | sed '/^$/d' | wc -l | tr -d ' ')"
  fetch_count="$(printf '%s\n' "$effective_fetch" | sed '/^$/d' | wc -l | tr -d ' ')"
  push_count="$(printf '%s\n' "$effective_push" | sed '/^$/d' | wc -l | tr -d ' ')"
  actual_repository="$(release_normalize_repository "$remote_url")"
  if [ "$raw_count" != "1" ] || [ "$fetch_count" != "1" ] || \
    [ "$push_count" != "1" ] || [ "$actual_repository" != "$expected_repository" ]; then
    release_fail "release_repository_mismatch" "preflight" "origin 不是 $expected_repository" "请检查当前仓库和 origin 配置"
    return 1
  fi
  if [ "$(release_normalize_repository "$effective_fetch")" != "$expected_repository" ] || \
    [ "$(release_normalize_repository "$effective_push")" != "$expected_repository" ] || \
    [ "${remote_url%.git}" != "${effective_fetch%.git}" ] || \
    [ "${remote_url%.git}" != "${effective_push%.git}" ]; then
    release_fail "release_transport_rewrite_forbidden" "preflight" \
      "发布仓库的实际 fetch 或 push 地址被 Git 配置改写" \
      "请移除 url.*.insteadOf、pushInsteadOf 或 remote pushurl 后重试"
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

release_require_version_tag_available() {
  local repo_root="$1"
  local version="$2"

  local remote_tag
  remote_tag="$(git -C "$repo_root" ls-remote --tags --refs origin "refs/tags/$version")" || return 1
  if [ -n "$remote_tag" ]; then
    release_fail "release_tag_remote_exists" "tag_validation" "远端 Tag $version 已存在" "请使用新的二段式版本，禁止移动或覆盖远端 Tag"
    return 1
  fi
  if git -C "$repo_root" show-ref --verify --quiet "refs/tags/$version"; then
    release_fail "release_local_tag_exists" "tag_validation" "本地 Tag $version 已存在，但正常发布只会在 main Merge commit 后创建 Tag" "请先确认该 Tag 未发布且可安全删除，或改用新的二段式版本"
    return 1
  fi
}

# 发布证据与开发证据分开；prepare/verify 执行，后续入口仅核验。
release_verification() {
  local code_root
  code_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
  (
    # python -m 优先搜索 cwd；--project 只选择 uv 项目，不隔离模块来源。
    cd "$code_root" || exit 1
    PYTHONPATH="$code_root" UV_PROJECT_ENVIRONMENT="$code_root/.local/venv/internal" \
      "${AGENTIC_OPS_UV:-uv}" run --locked --project "$code_root/internal" --python 3.12 \
      python -m internal.release.verification "$@"
  )
}

release_run_full_verification() {
  local repo_root="$1" head="$2" mode="${3:-ensure}"
  RELEASE_BASE_COMMIT="${RELEASE_BASE_COMMIT:-$(git -C "$repo_root" rev-parse origin/main)}"
  RELEASE_VERIFIED_AT="$(release_verification "$mode" --root "$repo_root" \
    --base "$RELEASE_BASE_COMMIT" --head "$head" --identity "$RELEASE_IDENTITY")" || {
    release_fail release_verification_required verification "发布证据缺失、失效或验证失败" "请查看诊断；使用 prepare 或 verify 补齐发布隔离四项，恢复时带 --merged-pr"
    return 1
  }
}

# 发布编排从固定快照执行；原目录的用户修改不进入候选，也不被清理。
release_enter_snapshot() {
  local root="$1" entry="$2" snapshot_root status=0
  shift 2
  if [ "${AGENTIC_OPS_RELEASE_SNAPSHOT:-}" = "$root" ]; then return 0; fi
  if ! git -C "$root" diff --quiet HEAD -- internal/release internal/story_gate internal/test_selection.py; then
    release_fail release_uncommitted_runner preflight "发布实现含未提交修改" "先审查固定发布实现；用户其它文件修改可以保留"
    return 1
  fi
  snapshot_root="$(mktemp -d)" || return 1
  git -C "$root" worktree add --detach "$snapshot_root/source" HEAD >/dev/null 2>&1 || return 1
  AGENTIC_OPS_RELEASE_SNAPSHOT="$root" bash "$snapshot_root/source/internal/release/$entry" "$@" || status=$?
  git -C "$root" worktree remove --force "$snapshot_root/source" >/dev/null 2>&1 || true
  rmdir "$snapshot_root" 2>/dev/null || true
  exit "$status"
}

release_lock_operation() {
  local root="$1"
  release_story_require_plain_directory_chain "$root" .local/release "发布操作记录" || return 1
  mkdir -p "$root/.local/release" || return 1
  if ! mkdir "$root/.local/release/.operation.lock" 2>/dev/null; then
    release_fail release_operation_busy preflight "已有发布操作或遗留锁" "等待在途操作；异常退出时先核验进程再清理锁"
    return 1
  fi
  RELEASE_OPERATION_LOCK="$root/.local/release/.operation.lock"
  trap 'rmdir "$RELEASE_OPERATION_LOCK" 2>/dev/null || true' EXIT
}

release_preflight_candidate() {
  local root="$1" head="$2"
  git -C "$root" fetch origin main >/dev/null 2>&1 || return 1
  RELEASE_BASE_COMMIT="$(git -C "$root" rev-parse origin/main)"
  if ! git -C "$root" merge-base --is-ancestor "$RELEASE_BASE_COMMIT" "$head"; then
    release_fail release_base_not_integrated preflight "候选尚未包含当前 main" "请先在开发候选集成 main 并验收，发布不会自动改变候选"
    return 1
  fi
}

release_check_base_unchanged() {
  local root="$1" current
  current="$(git -C "$root" ls-remote --heads origin refs/heads/main)" || return 1
  current="${current%%[[:space:]]*}"
  [ "$current" = "$RELEASE_BASE_COMMIT" ] || {
    release_fail release_base_drift preflight "main 基线在验证后变化" "停止合并，重新评估候选与审查范围"
    return 1
  }
}

release_story_gate_trust_root_changes() {
  local repo_root="$1"
  local base="$2"
  local head="$3"

  GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" diff \
    --name-only "$base" "$head" -- \
    .githooks \
    adapters/agents \
    adapters/runtime.py \
    adapters/tools \
    internal/bin/story-gate \
    internal/pyproject.toml \
    internal/uv.lock \
    internal/story_gate \
    internal/release \
    internal/story_gate/review-policy.yaml \
    internal/story_gate/stories.yaml
}

release_verify_story_gate() {
  local repo_root="$1"
  local base="$2"
  local head="$3"
  local allow_independent_review="${4:-false}"
  # 正式版本使用普通影响分析及发布证据；未传阶段的 Hotfix 保留开发材料检查。
  local gate_stage="${5-release}"
  local trusted_base_commit
  local range_base_commit
  local head_commit
  local temp_root
  local baseline_snapshot
  local candidate_snapshot
  local baseline_cli
  local uv_bin="${AGENTIC_OPS_UV:-}"
  local gate_status=0
  local required_path
  local record_kind
  local record_source
  local record_target
  local record
  local trust_root_changes=""
  local story_branch

  RELEASE_TRUST_ROOT_CHANGED="false"

  if [ "$base" != "origin/main" ]; then
    release_fail "release_story_gate_baseline_invalid" "story_gate" \
      "发布故事门禁只接受刷新后的 origin/main 可信基线" \
      "请使用固定 origin/main 作为发布范围基线"
    return 1
  fi
  if ! GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" fetch origin main >/dev/null 2>&1; then
    release_fail "release_story_gate_baseline_fetch_failed" "story_gate" \
      "无法刷新可信 origin/main 故事门禁基线" \
      "请检查官方远端网络与读取权限后重试"
    return 1
  fi
  trusted_base_commit="$(
    GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" rev-parse \
      'refs/remotes/origin/main^{commit}' 2>/dev/null || true
  )"
  head_commit="$(
    GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" rev-parse \
      "$head^{commit}" 2>/dev/null || true
  )"
  story_branch="$(GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" branch --show-current)"
  if [ -z "$story_branch" ]; then
    release_fail "release_story_gate_branch_unknown" "story_gate" \
      "无法确定固定发布 HEAD 所属分支" \
      "请在版本化发布或 Hotfix 分支上重新执行"
    return 1
  fi
  if [ -z "$trusted_base_commit" ] || [ -z "$head_commit" ]; then
    release_fail "release_story_gate_range_invalid" "story_gate" \
      "无法解析 origin/main 或固定发布 HEAD" \
      "请刷新远端引用并重新准备发布分支"
    return 1
  fi
  range_base_commit="$(GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" merge-base "$trusted_base_commit" "$head_commit" 2>/dev/null || true)"
  if [ -z "$range_base_commit" ]; then
    release_fail "release_story_gate_range_invalid" "story_gate" \
      "固定发布 HEAD 与 origin/main 没有共同基线" \
      "请从当前开发历史重新准备发布分支"
    return 1
  fi
  if [ "$gate_stage" = release ] && ! release_story_validate_record_source "$repo_root"; then
    release_fail "release_story_gate_local_state_unsafe" "story_gate" \
      "$RELEASE_STORY_STATE_ERROR" \
      "请移除 .local/story-gate 故事记录路径中的符号链接或特殊文件后重试"
    return 1
  fi

  temp_root="$(mktemp -d)"
  baseline_snapshot="$temp_root/baseline"
  candidate_snapshot="$temp_root/candidate"
  if ! GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" worktree add \
    --detach "$baseline_snapshot" "$trusted_base_commit" >/dev/null 2>&1; then
    rm -rf -- "$temp_root"
    release_fail "release_story_gate_baseline_unavailable" "story_gate" \
      "无法创建 origin/main 可信故事门禁快照" \
      "请检查 Git worktree 状态后重试"
    return 1
  fi
  if ! GIT_NO_REPLACE_OBJECTS=1 git -C "$repo_root" worktree add \
    --detach "$candidate_snapshot" "$head_commit" >/dev/null 2>&1; then
    git -C "$repo_root" worktree remove --force "$baseline_snapshot" >/dev/null 2>&1 || true
    rm -rf -- "$temp_root"
    release_fail "release_story_gate_candidate_unavailable" "story_gate" \
      "无法创建固定发布 HEAD 候选快照" \
      "请检查 Git worktree 状态后重试"
    return 1
  fi

  for required_path in \
    .agentic-ops-source \
    AGENTS.md \
    internal/bin/story-gate \
    internal/pyproject.toml \
    internal/uv.lock \
    internal/story_gate/service.py \
    internal/story_gate/review-policy.yaml \
    internal/story_gate/stories.yaml; do
    if [ ! -f "$baseline_snapshot/$required_path" ] || \
      [ -L "$baseline_snapshot/$required_path" ]; then
      git -C "$repo_root" worktree remove --force "$candidate_snapshot" >/dev/null 2>&1 || true
      git -C "$repo_root" worktree remove --force "$baseline_snapshot" >/dev/null 2>&1 || true
      rm -rf -- "$temp_root"
      release_fail "release_story_gate_baseline_upgrade_required" "story_gate" \
        "origin/main 尚未安装可独立执行的新故事门禁基线" \
        "请先通过受保护 main 的人工审查 PR 完成一次基线升级；基线进入 main 后，再用 release publish 发布后续变更"
      return 1
    fi
  done
  if [ "$(sed -n '1p' "$baseline_snapshot/.agentic-ops-source")" != "source" ] || \
    [ ! -x "$baseline_snapshot/internal/bin/story-gate" ] || \
    { find "$baseline_snapshot/internal/story_gate" \
        -type l -print -quit | grep -q .; }; then
    git -C "$repo_root" worktree remove --force "$candidate_snapshot" >/dev/null 2>&1 || true
    git -C "$repo_root" worktree remove --force "$baseline_snapshot" >/dev/null 2>&1 || true
    rm -rf -- "$temp_root"
    release_fail "release_story_gate_baseline_upgrade_required" "story_gate" \
      "origin/main 故事门禁基线无效或包含跨快照符号链接" \
      "请先通过受保护 main 的人工审查 PR 修复基线，再发布其它变更"
    return 1
  fi

  for required_path in \
    .agentic-ops-source \
    AGENTS.md \
    internal/bin/story-gate \
    internal/story_gate/stories.yaml; do
    if [ ! -f "$candidate_snapshot/$required_path" ] || \
      [ -L "$candidate_snapshot/$required_path" ]; then
      gate_status=2
      break
    fi
  done
  if [ "$gate_status" -eq 0 ] && \
    { [ "$(sed -n '1p' "$candidate_snapshot/.agentic-ops-source")" != "source" ] || \
      [ ! -x "$candidate_snapshot/internal/bin/story-gate" ]; }; then
    gate_status=2
  fi
  if [ "$gate_status" -eq 2 ]; then
    git -C "$repo_root" worktree remove --force "$candidate_snapshot" >/dev/null 2>&1 || true
    git -C "$repo_root" worktree remove --force "$baseline_snapshot" >/dev/null 2>&1 || true
    rm -rf -- "$temp_root"
    release_fail "release_story_gate_missing" "story_gate" \
      "固定发布 HEAD 缺少 AgenticOps 故事门禁资产" \
      "请恢复源头标记、故事注册表和 story-gate 后重新发布"
    return 1
  fi

  # 只有消费开发材料的入口才读取这些记录，正式版本不依赖无关本地台账。
  if [ "$gate_stage" = release ]; then
    for record_kind in approvals evidence; do
      if ! release_story_copy_record_kind \
        "$repo_root" "$candidate_snapshot" "$record_kind"; then
        git -C "$repo_root" worktree remove --force "$candidate_snapshot" >/dev/null 2>&1 || true
        git -C "$repo_root" worktree remove --force "$baseline_snapshot" >/dev/null 2>&1 || true
        rm -rf -- "$temp_root"
        release_fail "release_story_gate_local_state_unsafe" "story_gate" \
          "$RELEASE_STORY_STATE_ERROR" \
          "请移除故事记录源或候选路径中的符号链接或特殊文件后重试"
        return 1
      fi
    done
  fi

  if [ -z "$uv_bin" ]; then
    uv_bin="$(command -v uv 2>/dev/null || true)"
  fi
  if [ -z "$uv_bin" ] || [ ! -x "$uv_bin" ]; then
    gate_status=125
  elif ! UV_CACHE_DIR="$baseline_snapshot/.local/cache/uv" \
    UV_PROJECT_ENVIRONMENT="$baseline_snapshot/.local/venv/internal" \
    "$uv_bin" sync --locked --project "$baseline_snapshot/internal" \
    --python 3.12 >/dev/null 2>&1; then
    gate_status=125
  else
    baseline_cli="$baseline_snapshot/internal/bin/story-gate"
    GIT_NO_REPLACE_OBJECTS=1 \
      AGENTIC_OPS_STORY_BRANCH="$story_branch" \
      AGENTIC_OPS_STORY_GATE_STAGE="$gate_stage" \
      PYTHONPYCACHEPREFIX="$temp_root/pycache" \
      "$baseline_cli" \
        --source-root "$candidate_snapshot" \
        impact \
        --change-source range \
        --base "$range_base_commit" \
        --head "$head_commit" >/dev/null || gate_status=$?
  fi

  if [ "$gate_status" -eq 0 ]; then
    trust_root_changes="$(release_story_gate_trust_root_changes \
      "$repo_root" "$range_base_commit" "$head_commit")"
    if [ -n "$trust_root_changes" ]; then
      gate_status=126
    fi
  fi

  git -C "$repo_root" worktree remove --force "$candidate_snapshot" >/dev/null 2>&1 || true
  git -C "$repo_root" worktree remove --force "$baseline_snapshot" >/dev/null 2>&1 || true
  rm -rf -- "$temp_root"
  if [ "$gate_status" -eq 125 ]; then
    release_fail "release_story_gate_baseline_runtime_missing" "story_gate" \
      "无法按 origin/main 锁文件准备可信故事门禁 Runtime" \
      "请安装可信 uv，或修复 main 基线锁文件后重试"
    return 1
  fi
  if [ "$gate_status" -eq 126 ]; then
    if [ "$allow_independent_review" = "true" ]; then
      RELEASE_TRUST_ROOT_CHANGED="true"
      return 0
    fi
    release_fail "release_story_gate_trust_root_changed" "story_gate" \
      "固定发布范围修改了 Hook、故事门禁或发布信任根，禁止自动发布" \
      "请通过受保护 main 的独立人工审查 PR 合入该信任根变更；合入后由新的 origin/main 基线验证后续发布"
    return 1
  fi
  if [ "$gate_status" -ne 0 ]; then
    release_fail "release_story_gate_blocked" "story_gate" \
      "origin/main 可信门禁拒绝固定发布范围，候选代码不能自证" \
      "请对固定发布范围完成 story-gate impact、人工确认与 story-gate verify 后重试"
    return 1
  fi
}

release_submit_review() {
  local repo_root="$1"
  local repository="$2"
  local version="$3"
  local branch="$4"
  local head="$5"
  local protection_mode="$6"
  local confirmed="$7"

  release_verify_story_gate "$repo_root" origin/main "$head" true "" || return 1
  release_run_full_verification "$repo_root" "$head" check || return 1
  release_check_base_unchanged "$repo_root" || return 1
  release_confirm_publish "$repo_root" "$version" "$head" "$confirmed" "$branch" main "提交独立审查 PR" || return 1
  release_push_fixed_branch "$repo_root" "$branch" "$head" || return 1
  release_find_or_create_pr "$repository" "$branch" main "$head" "$version" release "${RELEASE_JIRA_ID:-}" "$protection_mode" || return 1
  release_validate_pr_binding "$RELEASE_PR_URL" "$version" "$head" || return 1
}

release_confirm_publish() {
  local repo_root="$1"
  local version="$2"
  local head="$3"
  local confirmed="$4"
  local source_branch="${5:-develop}"
  local target_branch="${6:-main}"
  local action_label="${7:-发布}"
  local answer

  printf '即将%s AgenticOps %s\n' "$action_label" "$version" >&2
  printf '仓库：%s\n' "tapstate/agentic-ops" >&2
  printf '发布 HEAD：%s\n' "$head" >&2
  printf '目标分支：%s -> %s\n' "$source_branch" "$target_branch" >&2
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
  printf '确认%s（%s -> %s）？[y/N] ' "$action_label" "$source_branch" "$target_branch" >&2
  if ! IFS= read -r answer; then
    answer=""
  fi
  case "$answer" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      release_fail "release_confirmation_rejected" "confirmation" "研发工程师取消了本次操作" "确认操作范围后重试原命令"
      return 1
      ;;
  esac
}

release_print_confirmation_bundle() {
  local repo_root="$1" action="$2" version="$3" head="$4"
  local source_branch="$5" target_branch="$6" risk="$7"
  local pr_reference="${8:-未关联 PR}" merge_reference="${9:-未关联 Merge commit}"
  local verification_reference
  if [ -n "${RELEASE_VERIFIED_AT:-}" ]; then
    verification_reference="固定完整验证通过（${RELEASE_VERIFIED_AT}）"
  else
    verification_reference="尚未执行当前动作所需的固定完整验证"
  fi
  printf '发布确认事项：\n' >&2
  printf '%s\n' "- 动作：${action}" >&2
  printf '%s\n' "- 目标：tapstate/agentic-ops，${source_branch} -> ${target_branch}，版本 ${version}，固定 HEAD ${head}" >&2
  printf '%s\n' "- 事实引用：main=$(git -C "$repo_root" rev-parse origin/main 2>/dev/null || printf unknown)，本地 Tag=$(git -C "$repo_root" rev-parse --verify "$version^{}" 2>/dev/null || printf absent)，远端 Tag=${RELEASE_REMOTE_TAG_COMMIT:-未读取或不存在}，PR=${pr_reference}，Merge commit=${merge_reference}" >&2
  printf '%s\n' "- 验证：${verification_reference}" >&2
  printf '%s\n' "- 风险：${risk}" >&2
  printf '%s\n' '- 不执行：不改写 main/develop，不删除或覆盖远端 Tag，不自动合并 PR。' >&2
  printf '%s\n' '- 后续门禁：确认内容与当前事实绑定；任一引用变化都必须重新检查。' >&2
}

release_read_remote_tag() {
  local repo_root="$1" version="$2" lines direct peeled
  lines="$(git -C "$repo_root" ls-remote --tags origin "refs/tags/$version" "refs/tags/$version^{}" 2>/dev/null)" || {
    release_fail "release_tag_remote_read_failed" "state_inspection" "无法读取远端 Tag 状态" "请检查网络后重新执行 release.sh inspect"
    return 1
  }
  direct="$(printf '%s\n' "$lines" | awk -v ref="refs/tags/$version" '$2 == ref {print $1; exit}')"
  peeled="$(printf '%s\n' "$lines" | awk -v ref="refs/tags/$version^{}" '$2 == ref {print $1; exit}')"
  RELEASE_REMOTE_TAG_REF="$direct"
  RELEASE_REMOTE_TAG_COMMIT="${peeled:-$direct}"
  RELEASE_REMOTE_TAG_ANNOTATED="false"
  [ -z "$direct" ] || [ -z "$peeled" ] || RELEASE_REMOTE_TAG_ANNOTATED="true"
}

# 回读当前 Head 的独立批准，不以 PR 已合并或正文声明代替审查事实。
release_check_independent_approval() {
  local pr="$1" head="$2" author approved
  author="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$pr" --repo tapstate/agentic-ops --json author --jq '.author.login' 2>/dev/null)" || return 1
  [ -n "$author" ] || return 1
  approved="$("${AGENTIC_OPS_GH_BIN:-gh}" api "repos/tapstate/agentic-ops/pulls/$pr/reviews?per_page=100" --paginate --slurp \
    --jq "add | map(select(.state == \"APPROVED\" or .state == \"CHANGES_REQUESTED\" or .state == \"DISMISSED\")) | group_by(.user.login) | map(sort_by(.submitted_at, .id) | last) | (all(.[]; .state != \"CHANGES_REQUESTED\")) and any(.[]; .state == \"APPROVED\" and .commit_id == \"$head\" and .user.login != \"$author\" and .user.type == \"User\")" 2>/dev/null)" || return 1
  [ "$approved" = true ] || {
    release_fail release_independent_approval_required review "缺少当前候选 Head 的独立人工批准或仍有要求修改的审查" "请在 GitHub 核实由 PR 作者以外的人员批准当前提交；不得用已合并状态代替审查"
    return 1
  }
}

release_read_merged_pr() {
  local pr_number="$1" row
  row="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$pr_number" --repo tapstate/agentic-ops \
    --json number,url,state,baseRefName,headRefOid,mergeCommit \
    --jq '[.number,.url,.state,.baseRefName,.headRefOid,.mergeCommit.oid] | @tsv' 2>/dev/null)" || return 1
  IFS=$'\t' read -r RELEASE_RECOVERY_PR_NUMBER RELEASE_RECOVERY_PR_URL \
    RELEASE_RECOVERY_PR_STATE RELEASE_RECOVERY_PR_BASE RELEASE_RECOVERY_PR_HEAD \
    RELEASE_RECOVERY_MERGE_COMMIT <<< "$row"
}

release_find_merged_pr() {
  local preferred_head="$1" preferred_merge="$2" rows number url state base head merge
  rows="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list --repo tapstate/agentic-ops --base main \
    --state merged --limit 100 --json number,url,state,baseRefName,headRefOid,mergeCommit \
    --jq '.[] | [.number,.url,.state,.baseRefName,.headRefOid,.mergeCommit.oid] | @tsv' 2>/dev/null)" || {
    release_fail "release_pr_state_read_failed" "state_inspection" "无法读取已合并 PR 状态" "请检查 GitHub 权限和网络后重新执行 release.sh inspect；不得据此认定 PR 缺失"
    return 2
  }
  while IFS=$'\t' read -r number url state base head merge; do
    [ -n "$number" ] || continue
    if [ "$head" = "$preferred_head" ] || { [ -n "$preferred_merge" ] && [ "$merge" = "$preferred_merge" ]; }; then
      RELEASE_RECOVERY_PR_NUMBER="$number"
      RELEASE_RECOVERY_PR_URL="$url"
      RELEASE_RECOVERY_PR_STATE="$state"
      RELEASE_RECOVERY_PR_BASE="$base"
      RELEASE_RECOVERY_PR_HEAD="$head"
      RELEASE_RECOVERY_MERGE_COMMIT="$merge"
      return 0
    fi
  done <<< "$rows"
  return 1
}

release_find_open_release_pr() {
  local release_branch="$1" rows
  rows="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list --repo tapstate/agentic-ops \
    --head "$release_branch" --base main --state open --limit 1 \
    --json number,url,state,baseRefName,headRefOid,mergeCommit \
    --jq '.[] | [.number,.url,.state,.baseRefName,.headRefOid,(.mergeCommit.oid // "")] | @tsv' 2>/dev/null)" || {
    release_fail "release_pr_state_read_failed" "state_inspection" "无法读取发布分支关联的开放 PR" "请检查 GitHub 权限和网络后重新执行 release.sh inspect"
    return 2
  }
  [ -n "$rows" ] || return 1
  IFS=$'\t' read -r RELEASE_RECOVERY_PR_NUMBER RELEASE_RECOVERY_PR_URL \
    RELEASE_RECOVERY_PR_STATE RELEASE_RECOVERY_PR_BASE RELEASE_RECOVERY_PR_HEAD \
    RELEASE_RECOVERY_MERGE_COMMIT <<< "$(printf '%s\n' "$rows" | head -n 1)"
  [ "$RELEASE_RECOVERY_PR_STATE" = "OPEN" ] && [ "$RELEASE_RECOVERY_PR_BASE" = "main" ]
}

release_candidate_is_in_main() {
  local repo_root="$1"
  local head="$2"
  git -C "$repo_root" fetch origin main >/dev/null 2>&1 || {
    release_fail "release_main_fetch_failed" "state_inspection" "无法刷新 origin/main" "请检查网络后重新执行 release.sh inspect 或 publish"
    return 2
  }
  git -C "$repo_root" merge-base --is-ancestor "$head" origin/main
}

release_inspect_state() {
  local root="$1" version="$2" requested_pr="${3:-}" rows local_tag main_head develop_head state next_command="" sync=not_applicable delivery=not_published head="" merge="" number="" url="" pr_state="" local_develop
  git -C "$root" fetch origin main develop >/dev/null 2>&1 || return 1
  main_head="$(git -C "$root" rev-parse origin/main)"
  develop_head="$(git -C "$root" rev-parse origin/develop)"
  local_develop="$(git -C "$root" rev-parse --verify develop 2>/dev/null || true)"
  local_tag="$(git -C "$root" rev-parse --verify "$version^{}" 2>/dev/null || true)"
  release_read_remote_tag "$root" "$version" || return 1
  if [ -n "$requested_pr" ]; then
    release_read_merged_pr "$requested_pr" || return 1
    [ "$RELEASE_RECOVERY_PR_BASE" = main ] || return 1
    number="$RELEASE_RECOVERY_PR_NUMBER"; url="$RELEASE_RECOVERY_PR_URL"
    pr_state="$RELEASE_RECOVERY_PR_STATE"; head="$RELEASE_RECOVERY_PR_HEAD"; merge="$RELEASE_RECOVERY_MERGE_COMMIT"
  else
    rows="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list --repo tapstate/agentic-ops --head "release/$version" --base main --state all \
      --json number,url,state,headRefOid,mergeCommit --jq '.[] | [.number,.url,.state,.headRefOid,(.mergeCommit.oid // "-")] | @tsv')" || return 1
    if [ -n "$rows" ]; then
      [ "$(printf '%s\n' "$rows" | wc -l | tr -d ' ')" = 1 ] || {
        release_fail release_pr_ambiguous state_inspection "发现多个版本 PR" "用 --merged-pr 指定确切 PR"
        return 1
      }
      IFS=$'\t' read -r number url pr_state head merge <<< "$rows"
    fi
  fi
  state=release_prepare_required
  next_command="internal/release/release.sh prepare --version $version"
  if [ "$pr_state" = MERGED ]; then
    RELEASE_BASE_COMMIT="$(git -C "$root" rev-parse "$merge^1")"
    release_validate_pr_binding "$url" "$version" "$head" || return 1
    release_verify_merge_commit "$root" "$head" "$merge" || return 1
    state=release_candidate_already_in_main
    next_command="internal/release/release.sh recover --version $version --merged-pr $number"
    if [ -n "$RELEASE_REMOTE_TAG_REF" ]; then
      if [ "$RELEASE_REMOTE_TAG_ANNOTATED" != true ] || [ "$RELEASE_REMOTE_TAG_COMMIT" != "$merge" ]; then
        state=release_remote_tag_conflict; next_command=""
      else
        delivery=published
        sync=pending_integration
        if git -C "$root" merge-base --is-ancestor "$merge" "$develop_head"; then
          sync=local_pending
          [ "$local_develop" != "$develop_head" ] || sync=synced
        elif git -C "$root" merge-base --is-ancestor "$develop_head" "$merge"; then sync=pending_fast_forward; fi
        state=release_completed_with_pending_sync
        if [ "$sync" = synced ]; then state=release_completed; next_command=""; fi
      fi
    fi
  elif [ "$pr_state" = OPEN ]; then
    state=release_waiting_manual_merge
    next_command="" # 人工审查/保护/合并由研发处理，不能把等待审查引导成自动发布。
  elif [ "$pr_state" = CLOSED ]; then
    state=release_pr_closed; next_command=""
  elif [ -n "$RELEASE_REMOTE_TAG_REF" ]; then
    state=release_merged_pr_missing; next_command=""
  elif git -C "$root" show-ref --verify --quiet "refs/heads/release/$version"; then
    state=release_candidate_present
    next_command="internal/release/release.sh submit-for-review --version $version"
  fi
  printf '{"ok":true,"operation":"release_inspect","version":"%s","state":"%s","delivery_status":"%s","develop_sync_status":"%s","develop_head":"%s","main_head":"%s","local_tag":"%s","remote_tag":"%s","pr_number":"%s","pr_url":"%s","pr_head":"%s","merge_commit":"%s","next_command":"%s"}\n' \
    "$version" "$state" "$delivery" "$sync" "$develop_head" "$main_head" "$local_tag" "${RELEASE_REMOTE_TAG_COMMIT:-}" "$number" "$url" "$head" "$merge" "$next_command"
}

release_recover_merged_candidate() {
  local repo_root="$1" version="$2" pr_number="$3" confirmed="$4" confirmation_digest="$5" protection_mode="$6"
  local soft_option=""
  if [ "$protection_mode" = soft ]; then soft_option=" --allow-soft-gate"; fi
  local candidate merge_commit pr_url local_tag local_release remote_release develop_head material digest tagger tag_object old_ref zero_ref
  if ! printf '%s\n' "$pr_number" | grep -Eq '^[1-9][0-9]*$'; then
    release_fail "invalid_release_merged_pr" "recovery_validation" "恢复发布必须提供正整数 --merged-pr" "请先运行 release.sh inspect 确认关联的已合并 PR 编号"
    return 1
  fi
  if ! release_read_merged_pr "$pr_number"; then
    release_fail "release_merged_pr_read_failed" "recovery_validation" "无法读取指定的已合并发布 PR" "请检查 PR 编号、GitHub 权限和网络后重试"
    return 1
  fi
  candidate="$RELEASE_RECOVERY_PR_HEAD"; merge_commit="$RELEASE_RECOVERY_MERGE_COMMIT"; pr_url="$RELEASE_RECOVERY_PR_URL"
  if [ "$RELEASE_RECOVERY_PR_STATE" != "MERGED" ] || [ "$RELEASE_RECOVERY_PR_BASE" != "main" ] || [ -z "$candidate" ] || [ -z "$merge_commit" ]; then
    release_fail "release_merged_pr_invalid" "recovery_validation" "指定 PR 不是包含固定候选的 main Merge commit" "请使用实际已合并到 main 的发布候选 PR，禁止猜测或绕过合并事实"
    return 1
  fi
  git -C "$repo_root" fetch origin main >/dev/null 2>&1 || {
    release_fail "release_main_fetch_failed" "recovery_validation" "无法刷新 origin/main" "请检查网络后重试"
    return 1
  }
  if ! git -C "$repo_root" merge-base --is-ancestor "$candidate" "$merge_commit" || ! git -C "$repo_root" merge-base --is-ancestor "$merge_commit" origin/main; then
    release_fail "release_merged_pr_invalid" "recovery_validation" "PR head、Merge commit 与当前 origin/main 的包含关系不成立" "请停止恢复并人工核查 main 历史"
    return 1
  fi
  RELEASE_BASE_COMMIT="$(git -C "$repo_root" rev-parse "$merge_commit^1")"
  release_verify_merge_commit "$repo_root" "$candidate" "$merge_commit" || return 1
  release_validate_pr_binding "$pr_url" "$version" "$candidate" || return 1
  release_read_remote_tag "$repo_root" "$version" || return 1
  if [ -n "$RELEASE_REMOTE_TAG_REF" ] && { [ "$RELEASE_REMOTE_TAG_ANNOTATED" != "true" ] || [ "$RELEASE_REMOTE_TAG_COMMIT" != "$merge_commit" ]; }; then
    release_fail "release_remote_tag_conflict" "recovery_validation" "远端同名 Tag 已存在但不是正确的 annotated main Merge commit Tag" "禁止删除或覆盖远端 Tag，请人工核查"
    return 1
  fi
  release_run_full_verification "$repo_root" "$candidate" check || return 1
  # 信任根交付始终需要受保护 main；普通历史 soft 恢复保留显式选择。
  local delivery_mode="$protection_mode" trust_changes
  trust_changes="$(release_story_gate_trust_root_changes "$repo_root" "$RELEASE_BASE_COMMIT" "$candidate")" || return 1
  if [ -n "$trust_changes" ]; then delivery_mode=hard; soft_option=""; fi
  protection_mode="$delivery_mode"
  workflow_check_delivery tapstate/agentic-ops "$delivery_mode" || return 1
  if [ "$delivery_mode" = hard ]; then release_check_independent_approval "$pr_number" "$candidate" || return 1; fi
  local_tag="$(git -C "$repo_root" rev-parse --verify "$version^{}" 2>/dev/null || true)"
  material="$version|$pr_number|$candidate|$merge_commit|$protection_mode|$(git -C "$repo_root" rev-parse origin/main)|$local_tag|${RELEASE_REMOTE_TAG_COMMIT:-}"
  digest="$(printf '%s' "$material" | git -C "$repo_root" hash-object --stdin)"
  release_print_confirmation_bundle "$repo_root" "恢复已提前合入候选并发布不可变 Tag" "$version" "$candidate" "PR #$pr_number" main "候选已由独立 PR 合入；只创建缺失的正确 Tag，绝不覆盖已有 Tag。" "$pr_url" "$merge_commit"
  if [ "$confirmed" != "true" ]; then
    release_fail "release_recovery_confirmation_required" "confirmation" "恢复计划已生成，尚未修改任何 Tag" "确认上述内容后执行 internal/release/release.sh recover --version $version --merged-pr $pr_number$soft_option --confirm-release --confirm-recovery $digest"
    return 1
  fi
  if [ "$confirmation_digest" != "$digest" ]; then
    release_fail "release_recovery_confirmation_stale" "confirmation" "恢复确认未绑定当前事实或状态已经变化" "重新执行不带确认参数的 recover，审查新的完整确认包"
    return 1
  fi
  RELEASE_PR_NUMBER="$pr_number"
  RELEASE_PR_URL="$pr_url"
  RELEASE_MERGE_COMMIT="$merge_commit"
  release_finish "$repo_root" "$version" "$candidate" "$merge_commit" "$protection_mode" release_recover || return 1

}

release_write_pr_body() {
  local body_file="$1"
  local version="$2"
  local source_branch="$3"
  local target_branch="$4"
  local head="$5"
  local jira_id="${6:-}"
  local protection_mode="${7:-hard}"
  local jira_evidence=""
  local protection_warning=""

  if [ -n "$jira_id" ]; then
    jira_evidence="- Jira 任务：\`$jira_id\`"
  fi
  if [ "$protection_mode" = "soft" ]; then
    protection_warning="- 风险：GitHub Free 私有仓库无法从服务器端阻止 main 直接推送；本 PR 必须人工使用 Merge commit 合并。"
  fi

  cat > "$body_file" <<EOF
## 发布证据

- 版本基线：\`$version\`
$jira_evidence
- 源分支：\`$source_branch\`
- 目标分支：\`$target_branch\`
- 待合并 HEAD：\`$head\`
- 本地验证完成时间（UTC）：\`$RELEASE_VERIFIED_AT\`
- 保护模式：\`$protection_mode\`
$protection_warning

<!-- agentic-ops-fixed-head:$head -->
<!-- agentic-ops-release:${version:-$jira_id}:$RELEASE_BASE_COMMIT -->

### 固定完整验证

- \`bash internal/tests/test_runtime.sh\`
- \`bash internal/tests/test_resources.sh\`
- \`bash tests/test_install.sh\`
- \`bash internal/tests/test_release.sh\`

以上命令全部通过。
EOF
}

release_find_or_create_pr() {
  local repository="$1"
  local source_branch="$2"
  local target_branch="$3"
  local head="$4"
  local version="$5"
  local publish_mode="${6:-release}"
  local jira_id="${7:-}"
  local protection_mode="${8:-hard}"
  local existing
  local pr_list
  local body_file
  local pr_title
  local pr_jq

  pr_jq=".[] | select(.headRefOid == \"$head\") | [.number, .url, .state, .headRefOid] | @tsv"
  if [ "$protection_mode" = "soft" ]; then
    pr_jq='.[] | [.number, .url, .state, .headRefOid] | @tsv'
  fi

  if ! pr_list="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list \
    --repo "$repository" \
    --base "$target_branch" \
    --head "$source_branch" \
    --state all \
    --json number,url,state,headRefOid \
    --jq "$pr_jq" 2>/dev/null)"; then
    release_fail "release_pr_list_failed" "pull_request" "无法查询现有发布 PR" "请检查 GitHub 认证和网络后重试，禁止在查询失败时创建重复 PR"
    return 1
  fi
  existing="$(printf '%s\n' "$pr_list" | head -n 1)"
  if [ -n "$existing" ]; then
    IFS=$'\t' read -r RELEASE_PR_NUMBER RELEASE_PR_URL RELEASE_PR_STATE RELEASE_PR_HEAD <<< "$existing"
    if [ "$RELEASE_PR_HEAD" != "$head" ]; then
      release_fail "release_pr_head_drift" "pull_request" "现有 PR HEAD 与固定发布 HEAD 不一致" "请停止合并并人工核查发布分支，禁止用新提交替换已验证 HEAD"
      return 1
    fi
    return 0
  fi

  body_file="$(mktemp)"
  release_write_pr_body "$body_file" "$version" "$source_branch" "$target_branch" "$head" "$jira_id" "$protection_mode"
  if [ "$publish_mode" = "hotfix" ]; then
    pr_title="Hotfix: $jira_id 修复合并到 $target_branch"
  else
    pr_title="Release: $version 合并 $source_branch 到 $target_branch"
  fi
  if ! RELEASE_PR_URL="$("${AGENTIC_OPS_GH_BIN:-gh}" pr create \
    --repo "$repository" \
    --base "$target_branch" \
    --head "$source_branch" \
    --title "$pr_title" \
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
  RELEASE_PR_HEAD="$head"
}

release_check_soft_pr_fixed_head_if_exists() {
  local repository="$1"
  local source_branch="$2"
  local target_branch="$3"
  local head="$4"
  local pr_list
  local existing

  if ! pr_list="$("${AGENTIC_OPS_GH_BIN:-gh}" pr list \
    --repo "$repository" \
    --base "$target_branch" \
    --head "$source_branch" \
    --state all \
    --json number,url,state,headRefOid \
    --jq '.[] | [.number, .url, .state, .headRefOid] | @tsv' 2>/dev/null)"; then
    release_fail "release_pr_list_failed" "pre_push" "无法在推送前查询现有发布 PR" "请检查 GitHub 认证和网络后重试"
    return 1
  fi
  existing="$(printf '%s\n' "$pr_list" | head -n 1)"
  if [ -z "$existing" ]; then
    return 0
  fi

  IFS=$'\t' read -r RELEASE_PR_NUMBER RELEASE_PR_URL RELEASE_PR_STATE RELEASE_PR_HEAD <<< "$existing"
  if [ "$RELEASE_PR_HEAD" != "$head" ]; then
    release_fail "release_pr_head_drift" "pre_push" "本地修复 HEAD 与现有 PR HEAD 不一致" "禁止推送；请恢复首次验证的固定 HEAD 后重试"
    return 1
  fi
  release_read_pr_fixed_head "$repository" || return 1
  if [ "$RELEASE_PR_FIXED_HEAD" != "$head" ]; then
    release_fail "release_pr_head_drift" "pre_push" "本地修复 HEAD 已偏离 PR 记录的首次验证 HEAD" "禁止推送；请恢复首次验证的固定 HEAD 后重试"
    return 1
  fi
}

release_refresh_pr_state() {
  local repository="$1"
  local result
  result="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$RELEASE_PR_URL" \
    --repo "$repository" \
    --json state,mergeCommit,url,number,headRefOid \
    --jq '[.state, (.mergeCommit.oid // "-"), .url, .number, .headRefOid] | @tsv')" || {
    release_fail "release_pr_read_failed" "manual_merge" "无法读取发布 PR 状态" "请检查 GitHub 状态后重新执行 publish"
    return 1
  }
  IFS=$'\t' read -r RELEASE_PR_STATE RELEASE_MERGE_COMMIT RELEASE_PR_URL RELEASE_PR_NUMBER RELEASE_PR_HEAD <<< "$result"
  if [ "$RELEASE_MERGE_COMMIT" = "-" ]; then RELEASE_MERGE_COMMIT=""; fi
}

release_read_pr_fixed_head() {
  local repository="$1"
  local body
  local fixed_head

  body="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$RELEASE_PR_URL" \
    --repo "$repository" --json body --jq .body)" || {
    release_fail "release_pr_read_failed" "manual_merge" "无法读取发布 PR 的固定 HEAD 证据" "请检查 GitHub 状态后重新执行 publish"
    return 1
  }
  fixed_head="$(printf '%s\n' "$body" | sed -n 's/.*<!-- agentic-ops-fixed-head:\([0-9a-f]\{40\}\) -->.*/\1/p' | head -n 1)"
  if [ -z "$fixed_head" ]; then
    release_fail "release_pr_fixed_head_missing" "manual_merge" "发布 PR 缺少固定 HEAD 证据" "禁止继续发布；请核查 PR 是否由发布脚本创建"
    return 1
  fi
  RELEASE_PR_FIXED_HEAD="$fixed_head"
}

release_wait_for_manual_merge() {
  local repo_root="$1"
  local repository="$2"
  local operation="$3"
  local version="$4"
  local head="$5"
  local branch="$6"
  local jira_id="${7:-}"

  release_refresh_pr_state "$repository" || return 1
  release_read_pr_fixed_head "$repository" || return 1
  if [ "$RELEASE_PR_HEAD" != "$head" ]; then
    release_fail "release_pr_head_drift" "manual_merge" "PR HEAD 与固定发布 HEAD 不一致" "请停止合并并人工核查发布分支"
    return 1
  fi
  if [ "$RELEASE_PR_FIXED_HEAD" != "$head" ]; then
    release_fail "release_pr_head_drift" "manual_merge" "当前 PR HEAD 已偏离首次验证的固定发布 HEAD" "请停止合并并从原始固定 HEAD 重新创建发布 PR"
    return 1
  fi
  case "$RELEASE_PR_STATE" in
    MERGED)
      return 0
      ;;
    CLOSED)
      release_fail "release_pr_closed" "manual_merge" "发布 PR 已关闭但未合并" "请恢复或重新创建 PR 后重试"
      return 1
      ;;
    OPEN)
      release_write_waiting_audit "$repo_root" "$operation" "$version" "$head" "$branch" "$jira_id" || return 1
      printf '{"schema_version":"step-result/v2","ok":false,"operation":"%s","status":"waiting_for_manual_merge","retry_safe":true,"result":{"status":"blocked","summary":"等待人工使用 Merge commit 合并 PR","facts":{},"evidence":[],"effects":[],"remaining":[]},"next_step":{"kind":"decision","scope":"flow","mode":"manual","executor":"human","action":"merge_pr_with_merge_commit_then_rerun","question":"请使用 Merge commit 合并 PR 后重新运行继续命令","choices":[{"id":"merge","label":"合并 PR 并继续","recommended":true}],"submit":{"operation":"submit_decision","effect":"record_only"},"call":{"operation":"submit_decision","argv":[]}},"version":"%s","head":"%s","branch":"%s","pr_number":%s,"pr_url":"%s","protection_mode":"soft","audit_file":"%s","continue_command":"%s"}\n' \
        "$operation" "$version" "$head" "$branch" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_AUDIT_FILE" "$RELEASE_CONTINUE_COMMAND"
      return 2
      ;;
    *)
      release_fail "release_pr_state_invalid" "manual_merge" "无法识别发布 PR 状态" "请核查 PR 后重新执行 publish"
      return 1
      ;;
  esac
}

# 软门禁下仍由研发工程师在 GitHub 上执行 Merge commit。本函数只负责在
# 固定 HEAD 未漂移的前提下等待该人工事实出现，随后让当前 publish 继续完成校验和 Tag。
release_wait_for_soft_merge() {
  local repository="$1"
  local head="$2"
  local attempt=0

  while [ "$attempt" -lt 360 ]; do
    release_refresh_pr_state "$repository" || return 1
    release_read_pr_fixed_head "$repository" || return 1
    if [ "$RELEASE_PR_HEAD" != "$head" ]; then
      release_fail "release_pr_head_drift" "manual_merge" "PR HEAD 与固定发布 HEAD 不一致" "请停止合并并人工核查发布分支"
      return 1
    fi
    if [ "$RELEASE_PR_FIXED_HEAD" != "$head" ]; then
      release_fail "release_pr_head_drift" "manual_merge" "当前 PR HEAD 已偏离首次验证的固定发布 HEAD" "请停止合并并从原始固定 HEAD 重新创建发布 PR"
      return 1
    fi
    case "$RELEASE_PR_STATE" in
      MERGED)
        return 0
        ;;
      CLOSED)
        release_fail "release_pr_closed" "manual_merge" "发布 PR 已关闭但未合并" "请恢复或重新创建 PR 后重试"
        return 1
        ;;
      OPEN)
        attempt=$((attempt + 1))
        sleep 5
        ;;
      *)
        release_fail "release_pr_state_invalid" "manual_merge" "无法识别发布 PR 状态" "请核查 PR 后重新执行 publish"
        return 1
        ;;
    esac
  done
  release_fail "release_merge_timeout" "manual_merge" "等待发布 PR 合并超时" "请检查 PR 门禁，处理后重新执行 publish"
  return 1
}

release_enable_auto_merge() {
  local repository="$1"
  if [ "$RELEASE_PR_STATE" = "MERGED" ]; then
    return 0
  fi
  if ! "${AGENTIC_OPS_GH_BIN:-gh}" pr merge "$RELEASE_PR_URL" --repo "$repository" --merge --auto --match-head-commit "$RELEASE_PR_HEAD"; then
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

# 交付已确认后只同步该 Merge commit，不顺带发布或集成其它提交。
release_sync_develop_to_main() {
  local root="$1" commit="$2" remote local_head current
  RELEASE_DEVELOP_COMMIT=""
  RELEASE_DEVELOP_SYNC=unknown
  git -C "$root" fetch origin main develop >/dev/null 2>&1 || return 1
  git -C "$root" merge-base --is-ancestor "$commit" origin/main || return 1
  remote="$(git -C "$root" rev-parse origin/develop)"
  if ! git -C "$root" merge-base --is-ancestor "$commit" "$remote"; then
    if ! git -C "$root" merge-base --is-ancestor "$remote" "$commit"; then
      RELEASE_DEVELOP_COMMIT="$remote"
      RELEASE_DEVELOP_SYNC=pending_integration
      printf '发布已交付；develop %s 有并行提交，需另行集成 %s。\n' "$remote" "$commit" >&2
      return 0
    fi
    AGENTIC_OPS_SPECIAL_PUSH=release git -C "$root" push origin "$commit:refs/heads/develop" || true
    git -C "$root" fetch origin develop >/dev/null 2>&1 || return 1
    remote="$(git -C "$root" rev-parse origin/develop)"
    git -C "$root" merge-base --is-ancestor "$commit" "$remote" || return 1
  fi
  RELEASE_DEVELOP_COMMIT="$remote"
  RELEASE_DEVELOP_SYNC=local_pending
  local_head="$(git -C "$root" rev-parse --verify develop 2>/dev/null || true)"
  current="$(git -C "$root" branch --show-current)"
  # 不切换用户分支、不处理暂存修改、不触碰其它 worktree。
  if [ "$current" = develop ] && [ -z "$(git -C "$root" status --porcelain)" ] &&
    git -C "$root" merge-base --is-ancestor "$local_head" "$remote"; then
    git -C "$root" merge --ff-only "$remote" >/dev/null || return 1
    RELEASE_DEVELOP_SYNC=synced
  elif [ "$local_head" = "$remote" ]; then
    RELEASE_DEVELOP_SYNC=synced
  fi
}

release_validate_pr_binding() {
  local url="$1" identity="$2" head="$3" body facts actual_base actual_head fixed markers
  facts="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$url" --repo tapstate/agentic-ops \
    --json baseRefName,headRefOid --jq '[.baseRefName,.headRefOid] | @tsv')" || return 1
  IFS=$'\t' read -r actual_base actual_head <<< "$facts"
  if [ "$actual_base" != main ] || [ "$actual_head" != "$head" ]; then
    release_fail release_pr_binding_invalid preflight "PR 实际目标或 Head 已变化" "停止发布并核对实际 PR，正文标记不能替代 GitHub 事实"
    return 1
  fi
  body="$("${AGENTIC_OPS_GH_BIN:-gh}" pr view "$url" --repo tapstate/agentic-ops --json body --jq .body)" || return 1
  fixed="$(printf '%s\n' "$body" | grep '<!-- agentic-ops-fixed-head:' || true)"
  [ "$fixed" = "<!-- agentic-ops-fixed-head:$head -->" ] || return 1
  markers="$(printf '%s\n' "$body" | grep '<!-- agentic-ops-release:' || true)"
  if [ "$markers" != "<!-- agentic-ops-release:$identity:$RELEASE_BASE_COMMIT -->" ]; then
    # 仅无新绑定的旧 PR 可回退到版本行；冲突的新绑定必须失败关闭。
    if [ -n "$markers" ]; then
      release_fail release_pr_binding_invalid preflight "PR 发布绑定与当前基线不一致" "核查 PR，不得用旧版本行覆盖冲突的绑定"
      return 1
    fi
    printf '%s\n' "$body" | grep -Fxq -- "- 版本基线：\`$identity\`" || {
      release_fail release_pr_binding_invalid preflight "PR 未绑定该版本或 Hotfix 及基线" "核查 PR 与发布记录，禁止借用其它 PR 发布"
      return 1
    }
  fi
}

# 保留既有 step-result/v2 与 Hotfix 字段，额外表达交付后的同步待办。
release_result_json() {
  python3 - "$1" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
waiting = value.get('status') == 'waiting_for_manual_merge'
pending = value.get('develop_sync_status') not in (None, 'synced')
remaining = ['按 main/develop 实际引用完成同步，不覆盖并行开发提交'] if pending else []
if waiting:
    remaining = ['独立人工审查并以 Merge commit 合入 PR，然后执行继续命令']
value.update(schema_version='step-result/v2', retry_safe=True,
             result=dict(status='blocked' if waiting else 'succeeded',
                         summary='等待独立人工合并' if waiting else ('发布已交付，分支同步待完成' if pending else '发布已完成'),
                         facts={}, evidence=[], effects=[], remaining=remaining),
             next_step=dict(kind='none', scope='flow', mode='manual', executor='stop',
                            action='hotfix_completed' if value['operation'] == 'hotfix_publish' else 'release_completed', call=None))
if waiting or pending:
    value['next_step'] = dict(kind='decision', scope='flow', mode='manual', executor='human',
                             action='review_and_merge_hotfix_pr' if waiting else 'resolve_develop_sync',
                             question=remaining[0], choices=[dict(id='resolve', label='处理待办', recommended=True)],
                             submit=dict(operation='submit_decision', effect='record_only'),
                             call=dict(operation='submit_decision', argv=[]))
if value.get('version'):
    value['tag_commit'] = value.get('merge_commit', '')
if value['operation'] == 'release_recover':
    value['recovery_reason'] = 'candidate_already_in_main'
if value['operation'] == 'hotfix_publish':
    value.update(main_commit=value.get('merge_commit', ''), jira_interaction=False)
    value.setdefault('develop_commit', value.get('merge_commit', ''))
    value.setdefault('changed', not waiting)
    value.setdefault('branch_created', False)
    value['gate'] = 'story_gate'
    if value.get('protection_mode') == 'direct':
        value['merge_subject'] = 'Hotfix: ' + value['jira_id'] + ' 合并 develop 到 main'
print(json.dumps(value, ensure_ascii=False))
PY
}

release_finish() {
  local root="$1" version="$2" head="$3" commit="$4" protection="$5" operation="$6" payload status=completed
  release_verify_remote_contains "$root" "$commit" || return 1
  release_verify_merge_commit "$root" "$head" "$commit" || return 1
  if [ -n "$version" ]; then
    release_create_and_push_version_tag "$root" "$version" "$commit" || return 1
  fi
  release_sync_develop_to_main "$root" "$commit" || RELEASE_DEVELOP_SYNC=unknown
  if [ "$RELEASE_DEVELOP_SYNC" != synced ]; then status=completed_with_pending_sync; fi
  payload="$(printf '{"schema_version":"step-result/v2","ok":true,"operation":"%s","status":"%s","delivery_status":"published","develop_sync_status":"%s","version":"%s","jira_id":"%s","head":"%s","merge_commit":"%s","develop_commit":"%s","tag":"%s","verified_at":"%s","pr_number":%s,"pr_url":"%s","protection_mode":"%s"}' \
    "$operation" "$status" "$RELEASE_DEVELOP_SYNC" "$version" "${RELEASE_JIRA_ID:-}" "$head" "$commit" "${RELEASE_DEVELOP_COMMIT:-}" "$version" "$RELEASE_VERIFIED_AT" "${RELEASE_PR_NUMBER:-0}" "${RELEASE_PR_URL:-}" "$protection")"
  payload="$(release_result_json "$payload")" || return 1
  release_write_audit_json "$root" "release-$RELEASE_IDENTITY-$head.json" "$payload" || return 1
  printf '%s\n' "$payload"
}

release_verify_merge_commit() {
  local root="$1" head="$2" commit="$3"
  release_verification merged --root "$root" --base "$RELEASE_BASE_COMMIT" --head "$head" --merge "$commit" || {
    release_fail release_merge_result_changed merge_verification "实际合并父提交或代码树与已验收范围不符" "停止 Tag；重新评估实际结果、验收与授权范围"
    return 1
  }
}

release_check_fixed_branch() {
  local repo_root="$1"
  local branch="$2"
  local candidate_head="$3"
  local local_head
  local remote_head

  local_head="$(git -C "$repo_root" show-ref --hash --verify "refs/heads/$branch" 2>/dev/null || true)"
  remote_head="$(git -C "$repo_root" ls-remote --heads origin "refs/heads/$branch" 2>/dev/null)" || return 1
  remote_head="${remote_head%%[[:space:]]*}"
  if [ -n "$remote_head" ]; then
    git -C "$repo_root" fetch origin "$branch" >/dev/null 2>&1 || {
      release_fail "release_fixed_branch_fetch_failed" "release_branch" "无法刷新 origin/$branch" "请检查网络后重试"
      return 1
    }
    if [ "$remote_head" != "$candidate_head" ] || { [ -n "$local_head" ] && [ "$local_head" != "$remote_head" ]; }; then
      release_fail "release_fixed_branch_conflict" "release_branch" "现有 $branch 不是本次已验证的固定 HEAD" "请人工核查固定发布分支，禁止覆盖或移动"
      return 1
    fi
    RELEASE_FIXED_HEAD="$remote_head"
    return 0
  fi
  if [ -n "$local_head" ] && [ "$local_head" != "$candidate_head" ]; then
    release_fail "release_fixed_branch_conflict" "release_branch" "本地 $branch 不是本次已验证的固定 HEAD" "请人工核查固定发布分支，禁止移动"
    return 1
  fi
  RELEASE_FIXED_HEAD="$candidate_head"
}

release_prepare_fixed_branch() {
  local repo_root="$1" branch="$2" candidate_head="$3"
  release_check_fixed_branch "$repo_root" "$branch" "$candidate_head" || return 1
  if ! git -C "$repo_root" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$repo_root" branch "$branch" "$candidate_head" || {
      release_fail release_fixed_branch_create_failed release_branch "无法创建固定发布分支 $branch" "请检查本地 Git 状态后重试"
      return 1
    }
  fi
}

release_resolve_prepared_fixed_branch() {
  local repo_root="$1"
  local branch="$2"
  local local_head
  local remote_head

  local_head="$(git -C "$repo_root" show-ref --hash --verify "refs/heads/$branch" 2>/dev/null || true)"
  remote_head="$(git -C "$repo_root" ls-remote --heads origin "refs/heads/$branch" 2>/dev/null)" || return 1
  remote_head="${remote_head%%[[:space:]]*}"
  if [ -n "$remote_head" ]; then
    git -C "$repo_root" fetch origin "$branch" >/dev/null 2>&1 || {
      release_fail "release_fixed_branch_fetch_failed" "release_branch" "无法刷新 origin/$branch" "请检查网络后重试"
      return 1
    }
    if [ -n "$local_head" ] && [ "$local_head" != "$remote_head" ]; then
      release_fail "release_fixed_branch_conflict" "release_branch" "本地与远端 $branch 目标不一致" "请人工核查固定发布分支，禁止覆盖"
      return 1
    fi
    RELEASE_FIXED_HEAD="$remote_head"
    return 0
  fi
  if [ -z "$local_head" ]; then
    release_fail "release_prepare_required" "release_branch" "缺少已验证的固定发布分支 $branch" "请先执行 release.sh prepare --version ${branch#release/}"
    return 1
  fi
  RELEASE_FIXED_HEAD="$local_head"
}

release_push_fixed_branch() {
  local repo_root="$1"
  local branch="$2"
  local head="$3"
  local local_head
  local remote_head

  local_head="$(git -C "$repo_root" show-ref --hash --verify "refs/heads/$branch" 2>/dev/null || true)"
  if [ -z "$local_head" ]; then
    git -C "$repo_root" branch "$branch" "$head" || {
      release_fail "release_fixed_branch_create_failed" "release_branch" "无法创建固定发布分支 $branch" "请检查本地分支状态后重试"
      return 1
    }
  elif [ "$local_head" != "$head" ]; then
    release_fail "release_fixed_branch_conflict" "release_branch" "本地 $branch 不是固定发布 HEAD" "禁止移动发布分支，请人工核查"
    return 1
  fi

  remote_head="$(git -C "$repo_root" ls-remote --heads origin "refs/heads/$branch" 2>/dev/null)" || return 1
  remote_head="${remote_head%%[[:space:]]*}"
  if [ -n "$remote_head" ] && [ "$remote_head" != "$head" ]; then
    release_fail "release_fixed_branch_conflict" "release_branch" "远端 $branch 不是固定发布 HEAD" "禁止覆盖远端发布分支，请人工核查"
    return 1
  fi
  if [ -z "$remote_head" ] && ! AGENTIC_OPS_SPECIAL_PUSH=release \
    git -C "$repo_root" push -u origin "refs/heads/$branch:refs/heads/$branch"; then
    release_fail "release_fixed_branch_push_failed" "release_branch" "无法推送固定发布分支 $branch" "请检查远端权限后重试"
    return 1
  fi
}

release_push_tag_if_needed() {
  local root="$1" version="$2"
  if [ "$RELEASE_TAG_REMOTE_EXISTS" != true ]; then
    AGENTIC_OPS_SPECIAL_PUSH=release git -C "$root" push origin "refs/tags/$version" || true
  fi
  release_read_remote_tag "$root" "$version" || return 1
  if [ "$RELEASE_REMOTE_TAG_ANNOTATED" != true ] || [ "$RELEASE_REMOTE_TAG_COMMIT" != "$RELEASE_TAG_COMMIT" ]; then
    release_fail release_tag_readback_failed tag_push "远端 Tag 未确认指向发布 Merge commit" "保留现场，先回读远端；禁止覆盖或移动 Tag"
    return 1
  fi
}

release_create_and_push_version_tag() {
  local repo_root="$1"
  local version="$2"
  local merge_commit="$3"
  local local_tag
  local local_type

  release_read_remote_tag "$repo_root" "$version" || return 1
  if [ -n "$RELEASE_REMOTE_TAG_REF" ]; then
    if [ "$RELEASE_REMOTE_TAG_ANNOTATED" != "true" ] || [ "$RELEASE_REMOTE_TAG_COMMIT" != "$merge_commit" ]; then
      release_fail "release_tag_remote_conflict" "tag_validation" "远端 Tag $version 已存在但未指向已核验的 main Merge commit" "禁止移动或覆盖远端 Tag，请人工核查"
      return 1
    fi
    RELEASE_TAG_REMOTE_EXISTS="true"
    RELEASE_TAG_COMMIT="$merge_commit"
    return 0
  fi

  local_tag="$(git -C "$repo_root" rev-parse --verify "$version^{}" 2>/dev/null || true)"
  local_type="$(git -C "$repo_root" cat-file -t "refs/tags/$version" 2>/dev/null || true)"
  if [ -n "$local_tag" ] && { [ "$local_type" != "tag" ] || [ "$local_tag" != "$merge_commit" ]; }; then
    release_fail "release_local_tag_conflict" "tag_validation" "本地 Tag $version 未指向已核验的 main Merge commit" "请人工核查；发布脚本不会移动或删除本地 Tag"
    return 1
  fi
  if [ -z "$local_tag" ] && ! git -C "$repo_root" tag -a "$version" "$merge_commit" -m "AgenticOps $version release merge"; then
    release_fail "release_tag_create_failed" "tag_creation" "无法在 main Merge commit 创建本地 Tag $version" "请检查本地 Git 身份和状态后重试"
    return 1
  fi
  RELEASE_TAG_REMOTE_EXISTS="false"
  RELEASE_TAG_COMMIT="$merge_commit"
  release_push_tag_if_needed "$repo_root" "$version"
}

release_write_audit_json() {
  local repo_root="$1"
  local audit_name="$2"
  local payload="$3"
  local audit_file
  local root_real
  local root_prefix

  case "$audit_name" in
    ""|.*|*/*|*..*|*[!A-Za-z0-9._-]*)
      release_fail "release_audit_path_unsafe" "audit_write" \
        "发布审计文件名不安全" \
        "请停止发布并人工核查版本、HEAD 和 Jira 标识"
      return 1
      ;;
    *.json)
      ;;
    *)
      release_fail "release_audit_path_unsafe" "audit_write" \
        "发布审计文件必须使用 JSON 后缀" \
        "请停止发布并人工核查审计目标"
      return 1
      ;;
  esac

  if [ -L "$repo_root" ] || [ ! -d "$repo_root" ]; then
    release_fail "release_audit_path_unsafe" "audit_write" \
      "发布审计信任根不是普通目录" \
      "请移除 .local/release 路径中的符号链接或特殊文件后重试"
    return 1
  fi
  root_real="$(cd "$repo_root" 2>/dev/null && pwd -P)" || {
    release_fail "release_audit_path_unsafe" "audit_write" \
      "无法解析发布审计信任根的真实路径" \
      "请停止发布并人工核查仓库路径"
    return 1
  }
  root_prefix="$root_real/"

  # 从已验证的仓库物理目录开始，逐级只用当前目录中的相对名称检查、创建并
  # cd。每次 cd 后重新检查 pwd -P containment；进入最终目录后以同一 cwd
  # 创建临时文件和 rename，避免祖先名称在检查后被替换时转向仓库外。
  audit_file="$({
    cd "$repo_root" 2>/dev/null || exit 1
    [ "$(pwd -P)" = "$root_real" ] || exit 2
    for component in .local release; do
      if [ -L "$component" ] || { [ -e "$component" ] && [ ! -d "$component" ]; }; then
        exit 3
      fi
      if [ ! -e "$component" ]; then
        mkdir -- "$component" || exit 4
      fi
      if [ -L "$component" ] || [ ! -d "$component" ]; then
        exit 5
      fi
      cd -- "$component" 2>/dev/null || exit 6
      current_real="$(pwd -P)" || exit 7
      [ "${current_real#"$root_prefix"}" != "$current_real" ] || exit 8
    done
    if [ -L "$audit_name" ] || { [ -e "$audit_name" ] && [ ! -f "$audit_name" ]; }; then
      exit 9
    fi
    pending="$(umask 077 && mktemp .audit.XXXXXX)" || exit 10
    if [ -L "$pending" ] || [ ! -f "$pending" ]; then
      rm -f -- "$pending"
      exit 11
    fi
    if ! printf '%s\n' "$payload" > "$pending" || \
      ! chmod 0600 "$pending" || \
      ! mv -f -- "$pending" "$audit_name"; then
      rm -f -- "$pending"
      exit 12
    fi
    [ ! -L "$audit_name" ] && [ -f "$audit_name" ] || exit 13
    printf '%s/%s\n' "$(pwd -P)" "$audit_name"
  })" || {
    release_fail "release_audit_path_unsafe" "audit_write" \
      "发布审计目录或叶子不是仓库内可安全原子替换的普通路径" \
      "请移除 .local/release 路径中的符号链接或特殊文件后重试"
    return 1
  }

  RELEASE_AUDIT_FILE="$audit_file"
}

release_write_audit() {
  local repo_root="$1"
  local version="$2"
  local head="$3"
  local protection_mode="${4:-hard}"
  local payload
  payload="$(printf '{"operation":"release_publish","status":"completed","version":"%s","head":"%s","verified_at":"%s","pr_number":%s,"pr_url":"%s","merge_commit":"%s","develop_commit":"%s","tag":"%s","tag_commit":"%s","protection_mode":"%s"}' \
    "$version" "$head" "$RELEASE_VERIFIED_AT" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_MERGE_COMMIT" "${RELEASE_DEVELOP_COMMIT:-}" "$version" "$RELEASE_TAG_COMMIT" "$protection_mode")"
  release_write_audit_json "$repo_root" "release-$version-$head.json" "$payload"
}

release_write_recovery_audit() {
  local repo_root="$1" version="$2" head="$3" protection_mode="${4:-soft}" payload
  payload="$(printf '{"operation":"release_recover","status":"completed","version":"%s","head":"%s","verified_at":"%s","pr_number":%s,"pr_url":"%s","merge_commit":"%s","tag":"%s","tag_commit":"%s","protection_mode":"%s","recovery_reason":"candidate_already_in_main"}' \
    "$version" "$head" "$RELEASE_VERIFIED_AT" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_MERGE_COMMIT" "$version" "$RELEASE_TAG_COMMIT" "$protection_mode")"
  release_write_audit_json "$repo_root" "release-recover-$version-$head.json" "$payload"
}

release_write_waiting_audit() {
  local repo_root="$1"
  local operation="$2"
  local version="$3"
  local head="$4"
  local branch="$5"
  local audit_name
  local payload

  audit_name="release-$version-$head.json"
  RELEASE_CONTINUE_COMMAND="internal/release/release.sh publish --version $version --allow-soft-gate --confirm-release"
  payload="$(printf '{"operation":"%s","status":"waiting_for_manual_merge","version":"%s","branch":"%s","head":"%s","verified_at":"%s","pr_number":%s,"pr_url":"%s","protection_mode":"soft","continue_command":"%s"}' \
    "$operation" "$version" "$branch" "$head" "$RELEASE_VERIFIED_AT" "$RELEASE_PR_NUMBER" "$RELEASE_PR_URL" "$RELEASE_CONTINUE_COMMAND")"
  release_write_audit_json "$repo_root" "$audit_name" "$payload"
}

release_validate_jira_id() {
  local jira_id="$1"
  if ! printf '%s\n' "$jira_id" | grep -Eq '^[A-Z][A-Z0-9]+-[1-9][0-9]*$'; then
    release_fail "invalid_jira_id" "hotfix_publish" "Jira ID 格式无效" "请使用例如 AO-123 或 TAP-12371 的大写任务编号"
    return 1
  fi
}
