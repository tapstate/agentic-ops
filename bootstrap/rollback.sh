#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
state_tool="$install_root/bootstrap/product_state.py"
test -d "$install_root/.git" || { printf 'AgenticOps：未找到安装目录：%s\n' "$install_root" >&2; exit 2; }
mode="$(python3 "$state_tool" --product-root "$install_root" read --field mode)"
test "$mode" = "installed" || { printf 'AgenticOps：rollback 只适用于安装 Product Root\n' >&2; exit 2; }
test -z "$(git -C "$install_root" status --porcelain)" || {
  printf 'AgenticOps：安装目录存在修改，拒绝回退\n' >&2
  exit 2
}
previous_ref="$(python3 "$state_tool" --product-root "$install_root" read --field previous_ref)"
test -n "$previous_ref" || { printf 'AgenticOps：没有可回退版本\n' >&2; exit 2; }
current_ref="$(git -C "$install_root" rev-parse HEAD)"
git -C "$install_root" cat-file -e "${previous_ref}^{commit}"
git -C "$install_root" checkout --detach "$previous_ref"
python3 "$state_tool" --product-root "$install_root" update-ref \
  --current-ref "$previous_ref" --previous-ref "$current_ref"
printf 'AgenticOps 已回退：%s -> %s\n' "$current_ref" "$previous_ref"
