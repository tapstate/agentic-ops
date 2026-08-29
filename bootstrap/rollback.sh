#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
state_tool="$install_root/bootstrap/product_state.py"
test -d "$install_root/.git" || { printf 'AgenticOps：未找到安装目录：%s\n' "$install_root" >&2; exit 2; }
mode="$(python3 "$state_tool" --product-root "$install_root" read --field mode)"
test "$mode" = "installed" || { printf 'AgenticOps：rollback 只适用于使用工作面；维护工作面请使用 Git 流程\n' >&2; exit 2; }
# shellcheck source=bootstrap/lifecycle-common.sh
. "$install_root/bootstrap/lifecycle-common.sh"
lifecycle_acquire_lock "$install_root" "rollback:installed"
lifecycle_require_clean_tree "$install_root" "使用"
previous_ref="$(python3 "$state_tool" --product-root "$install_root" read --field previous_ref)"
test -n "$previous_ref" || { printf 'AgenticOps：没有可回退版本\n' >&2; exit 2; }
current_ref="$(git -C "$install_root" rev-parse HEAD)"
git -C "$install_root" cat-file -e "${previous_ref}^{commit}"
git -C "$install_root" checkout --detach "$previous_ref"
python3 "$state_tool" --product-root "$install_root" update-ref \
  --current-ref "$previous_ref" --previous-ref "$current_ref"
printf 'AgenticOps 回退完成：工作面=使用，%s -> %s\n' "$current_ref" "$previous_ref"
