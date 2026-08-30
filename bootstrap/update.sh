#!/usr/bin/env bash
set -euo pipefail

product_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
state_tool="$product_root/bootstrap/product_state.py"
state_path="$product_root/.local/product.json"

test -d "$product_root/.git" || { printf 'AgenticOps：未找到产品根目录：%s\n' "$product_root" >&2; exit 2; }
test -f "$state_path" || {
  if [ -f "$product_root/.agentic-ops-source" ]; then
    printf 'AgenticOps：维护工作面尚未初始化，请先执行 agenticops setup\n' >&2
  else
    printf 'AgenticOps：使用工作面本地配置缺失，请重新安装\n' >&2
  fi
  exit 2
}

# shellcheck source=bootstrap/lifecycle-common.sh
. "$product_root/bootstrap/lifecycle-common.sh"
mode="$(python3 "$state_tool" --product-root "$product_root" read --field mode)"
face="$(lifecycle_work_face "$mode")"
test "$face" != "未知" || { printf 'AgenticOps：产品根目录工作面无效\n' >&2; exit 2; }
lifecycle_acquire_lock "$product_root" "update:$mode"
lifecycle_require_clean_tree "$product_root" "$face"

branch="$(python3 "$state_tool" --product-root "$product_root" read --field tracking_branch)"
repository="$(python3 "$state_tool" --product-root "$product_root" read --field repository)"
git check-ref-format --branch "$branch" >/dev/null
lifecycle_require_recorded_remote "$product_root" "$repository" "$face"

current_ref="$(git -C "$product_root" rev-parse HEAD)"
if [ "$mode" = "installed" ]; then
  recorded_ref="$(python3 "$state_tool" --product-root "$product_root" read --field current_ref)"
  test "$current_ref" = "$recorded_ref" || {
    printf 'AgenticOps：使用工作面 HEAD 与本地配置不一致，拒绝更新\n' >&2
    exit 2
  }
elif [ "$mode" = "source" ]; then
  test -f "$product_root/.agentic-ops-source" || {
    printf 'AgenticOps：维护工作面缺少源码标识，拒绝更新\n' >&2
    exit 2
  }
  current_branch="$(git -C "$product_root" branch --show-current)"
  test "$current_branch" = "$branch" || {
    printf 'AgenticOps：维护工作面当前分支为 %s，应在 %s 执行 update\n' "${current_branch:-detached HEAD}" "$branch" >&2
    exit 2
  }
  command -v uv >/dev/null 2>&1 || {
    printf 'AgenticOps：维护工作面缺少命令：uv\n' >&2
    exit 2
  }
fi

git -C "$product_root" fetch origin \
  "refs/heads/$branch:refs/remotes/origin/$branch"
target_ref="$(git -C "$product_root" rev-parse "refs/remotes/origin/$branch")"
counts="$(git -C "$product_root" rev-list --left-right --count "HEAD...$target_ref")"
ahead="${counts%%[[:space:]]*}"
behind="${counts##*[[:space:]]}"
if [ "$ahead" -gt 0 ] && [ "$behind" -gt 0 ]; then
  printf 'AgenticOps：%s工作面与 origin/%s 已分叉（ahead=%s，behind=%s），拒绝更新\n' \
    "$face" "$branch" "$ahead" "$behind" >&2
  exit 2
fi
if [ "$mode" = "installed" ] && [ "$ahead" -gt 0 ]; then
  printf 'AgenticOps：使用工作面领先 origin/%s，远端历史可能已变化，拒绝更新\n' "$branch" >&2
  exit 2
fi
if [ "$behind" -gt 0 ]; then
  git -C "$product_root" merge --ff-only "$target_ref"
fi
updated_ref="$(git -C "$product_root" rev-parse HEAD)"

if [ "$mode" = "source" ]; then
  mkdir -p "$product_root/.local/venv" "$product_root/.local/cache"
  chmod 0700 "$product_root/.local" "$product_root/.local/venv" "$product_root/.local/cache"
  if ! UV_CACHE_DIR="$product_root/.local/cache/uv" \
    UV_PROJECT_ENVIRONMENT="$product_root/.local/venv/internal" \
      uv sync --locked --project "$product_root/internal"; then
    printf 'AgenticOps：源码已更新到 %s，但维护依赖同步失败；修复后重新执行 update\n' "$updated_ref" >&2
    exit 2
  fi
  # shellcheck source=internal/release/lib/development-workflow.sh
  . "$product_root/internal/release/lib/development-workflow.sh"
  if ! workflow_install_trusted_hooks "$product_root"; then
    printf 'AgenticOps：源码已更新到 %s，但受信 Hook 同步失败；修复后重新执行 update\n' "$updated_ref" >&2
    exit 2
  fi
  python3 "$state_tool" --product-root "$product_root" update-ref \
    --current-ref "$updated_ref"
else
  python3 "$state_tool" --product-root "$product_root" update-ref \
    --current-ref "$updated_ref" --previous-ref "$current_ref"
fi

printf 'AgenticOps 更新完成：工作面=%s，branch=%s，%s -> %s，ahead=%s\n' \
  "$face" "$branch" "$current_ref" "$updated_ref" "$ahead"
if [ "$updated_ref" != "$current_ref" ]; then
  python3 "$product_root/bootstrap/workspace_registry.py" \
    --product-root "$product_root" pending --product-ref "$updated_ref"
fi
if [ "$mode" = "source" ] && [ "$ahead" -gt 0 ]; then
  printf 'AgenticOps：维护分支本地领先 %s 个提交；update 不会自动推送\n' "$ahead"
fi
