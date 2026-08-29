#!/usr/bin/env bash
set -euo pipefail

product_root="${AGENTIC_OPS_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)}"
target_branch="develop"

test -f "$product_root/.agentic-ops-source" || {
  printf 'AgenticOps：setup 只适用于源码 Product Root\n' >&2
  exit 2
}
if [ -f "$product_root/.local/product.json" ]; then
  configured_mode="$(python3 "$product_root/bootstrap/product_state.py" +    --product-root "$product_root" read --field mode)"
  test "$configured_mode" != "installed" || {
    printf 'AgenticOps：安装 Product Root 不能执行 setup；请使用 agenticops update\n' >&2
    exit 2
  }
fi
for command_name in git python3 uv; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'AgenticOps：源码维护缺少命令：%s\n' "$command_name" >&2
    exit 2
  }
done

current_branch="$(git -C "$product_root" branch --show-current)"
if [ "$current_branch" != "$target_branch" ]; then
  test -z "$(git -C "$product_root" status --porcelain)" || {
    printf 'AgenticOps：工作区有未提交修改，拒绝自动切换到 develop\n' >&2
    exit 2
  }
fi
git -C "$product_root" fetch origin "$target_branch"
if [ "$current_branch" != "$target_branch" ]; then
  if git -C "$product_root" show-ref --verify --quiet "refs/heads/$target_branch"; then
    git -C "$product_root" switch "$target_branch"
  else
    git -C "$product_root" switch --create "$target_branch" --track "origin/$target_branch"
  fi
fi
git -C "$product_root" merge --ff-only "origin/$target_branch"

repository="$(git -C "$product_root" remote get-url origin)"
current_ref="$(git -C "$product_root" rev-parse HEAD)"
python3 "$product_root/bootstrap/product_state.py" \
  --product-root "$product_root" write \
  --mode source --repository "$repository" --branch "$target_branch" \
  --current-ref "$current_ref"

mkdir -p "$product_root/.local/venv" "$product_root/.local/cache"
chmod 0700 "$product_root/.local" "$product_root/.local/venv" "$product_root/.local/cache"
UV_CACHE_DIR="$product_root/.local/cache/uv" \
UV_PROJECT_ENVIRONMENT="$product_root/.local/venv/internal" \
  uv sync --locked --project "$product_root/internal"

# shellcheck source=internal/release/lib/development-workflow.sh
. "$product_root/internal/release/lib/development-workflow.sh"
workflow_install_trusted_hooks "$product_root"

printf 'AgenticOps 源码维护环境已就绪：branch=%s，ref=%s\n' "$target_branch" "$current_ref"
