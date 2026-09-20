#!/usr/bin/env bash
set -euo pipefail

product_root="${AGENTIC_OPS_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)}"
target_branch="develop"
source_pool="$HOME/.agentic-ops-repos"
source_pool_explicit=false

usage() {
  printf '用法：setup.sh [--source-pool <目录>]\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-pool)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      source_pool="$2"
      source_pool_explicit=true
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
  if configured_mode="$(python3 "$product_root/bootstrap/product_state.py" \
      --product-root "$product_root" read --field mode 2>/dev/null)"; then
    test "$configured_mode" != "installed" || {
      printf 'AgenticOps：安装目录不能执行 setup；请使用 agenticops update\n' >&2
      exit 2
    }
    if [ "$source_pool_explicit" = false ]; then
      exec env AGENTIC_OPS_HOME="$product_root" bash "$product_root/bootstrap/update.sh"
    fi
    env AGENTIC_OPS_HOME="$product_root" bash "$product_root/bootstrap/update.sh"
    python3 "$product_root/bootstrap/product_state.py" \
      --product-root "$product_root" update-source-pool --source-pool "$source_pool"
    printf 'AgenticOps 源码池已更新：%s\n' "$(python3 "$product_root/bootstrap/product_state.py" --product-root "$product_root" read --field source_pool)"
    exit 0
  fi
  # 产品源码的本地生命周期配置可由 setup 重新生成；不解析或迁移任何工位状态。
  printf 'AgenticOps：产品源码本地配置版本不支持，将重新初始化产品配置\n' >&2
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
source_pool="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$source_pool")"
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
  --current-ref "$current_ref" --source-pool "$source_pool"
python3 "$product_root/bootstrap/skill_wiring.py" \
  --product-root "$product_root" --refresh

printf 'AgenticOps 初始化完成：branch=%s，ref=%s，源码池=%s\n' "$target_branch" "$current_ref" "$source_pool"
