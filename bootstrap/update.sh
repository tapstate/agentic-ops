#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
state_tool="$install_root/bootstrap/product_state.py"
test -d "$install_root/.git" || { printf 'AgenticOps：未找到安装目录：%s\n' "$install_root" >&2; exit 2; }
mode="$(python3 "$state_tool" --product-root "$install_root" read --field mode)"
test "$mode" = "installed" || { printf 'AgenticOps：update 只适用于安装 Product Root\n' >&2; exit 2; }
test -z "$(git -C "$install_root" status --porcelain)" || {
  printf 'AgenticOps：安装目录存在修改，拒绝更新\n' >&2
  exit 2
}

branch="$(python3 "$state_tool" --product-root "$install_root" read --field tracking_branch)"
git check-ref-format --branch "$branch" >/dev/null
current_ref="$(git -C "$install_root" rev-parse HEAD)"
recorded_ref="$(python3 "$state_tool" --product-root "$install_root" read --field current_ref)"
test "$current_ref" = "$recorded_ref" || {
  printf 'AgenticOps：安装 HEAD 与本地配置不一致，拒绝更新\n' >&2
  exit 2
}
git -C "$install_root" fetch origin \
  "refs/heads/$branch:refs/remotes/origin/$branch"
target_ref="$(git -C "$install_root" rev-parse "refs/remotes/origin/$branch")"
git -C "$install_root" merge --ff-only "$target_ref"
python3 "$state_tool" --product-root "$install_root" update-ref \
  --current-ref "$target_ref" --previous-ref "$current_ref"
printf 'AgenticOps 已更新：channel=%s，%s -> %s\n' "$branch" "$current_ref" "$target_ref"
