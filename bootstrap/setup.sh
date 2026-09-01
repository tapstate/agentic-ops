#!/usr/bin/env bash
set -euo pipefail

product_root="${AGENTIC_OPS_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)}"
target_branch="develop"
repository_pool=""
repository_provisioning="auto-clone"

usage() {
  printf '用法：setup.sh [--repository-pool <目录>] [--repository-provisioning manual|auto-clone]\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repository-pool)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      repository_pool="$2"
      shift 2
      ;;
    --repository-provisioning)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      repository_provisioning="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'AgenticOps：未知 setup 参数：%s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

test -f "$product_root/.agentic-ops-source" || {
  printf 'AgenticOps：setup 只适用于源码产品根目录\n' >&2
  exit 2
}
if [ -f "$product_root/.local/product.json" ]; then
  configured_mode="$(python3 "$product_root/bootstrap/product_state.py" \
    --product-root "$product_root" read --field mode)"
  test "$configured_mode" != "installed" || {
    printf 'AgenticOps：使用工作面不能执行 setup；请使用 agenticops update\n' >&2
    exit 2
  }
  if [ -n "$repository_pool" ] || [ ! -f "$product_root/.local/repository-pool.json" ]; then
    pool_arguments=()
    test -z "$repository_pool" || pool_arguments+=(--root "$repository_pool")
    python3 "$product_root/bootstrap/repository_pool.py" --product-root "$product_root" \
      configure ${pool_arguments[@]+"${pool_arguments[@]}"} \
      --provisioning "$repository_provisioning" >/dev/null
  fi
  exec env AGENTIC_OPS_HOME="$product_root" bash "$product_root/bootstrap/update.sh"
fi
for command_name in git python3 uv; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'AgenticOps：源码维护缺少命令：%s\n' "$command_name" >&2
    exit 2
  }
done

# shellcheck source=bootstrap/lifecycle-common.sh
. "$product_root/bootstrap/lifecycle-common.sh"
lifecycle_acquire_lock "$product_root" "setup:source"
lifecycle_require_clean_tree "$product_root" "维护"

current_branch="$(git -C "$product_root" branch --show-current)"
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
mkdir -p "$product_root/.local/venv" "$product_root/.local/cache"
chmod 0700 "$product_root/.local" "$product_root/.local/venv" "$product_root/.local/cache"
UV_CACHE_DIR="$product_root/.local/cache/uv" \
UV_PROJECT_ENVIRONMENT="$product_root/.local/venv/internal" \
  uv sync --locked --project "$product_root/internal"

# shellcheck source=internal/release/lib/development-workflow.sh
. "$product_root/internal/release/lib/development-workflow.sh"
workflow_install_trusted_hooks "$product_root"

python3 "$product_root/bootstrap/product_state.py" \
  --product-root "$product_root" write \
  --mode source --repository "$repository" --branch "$target_branch" \
  --current-ref "$current_ref"
pool_arguments=()
test -z "$repository_pool" || pool_arguments+=(--root "$repository_pool")
python3 "$product_root/bootstrap/repository_pool.py" --product-root "$product_root" \
  configure ${pool_arguments[@]+"${pool_arguments[@]}"} \
  --provisioning "$repository_provisioning" >/dev/null
python3 "$product_root/bootstrap/skill_wiring.py" \
  --product-root "$product_root" --refresh

printf 'AgenticOps 初始化完成：工作面=维护，branch=%s，ref=%s\n' "$target_branch" "$current_ref"
printf 'Source Pool：%s（%s）\n' \
  "$(python3 "$product_root/bootstrap/repository_pool.py" --product-root "$product_root" read --field root)" \
  "$repository_provisioning"
