#!/usr/bin/env bash
# AO_PRODUCT_SHELL_BEGIN
exec python3 -c 'import sys; from pathlib import Path; p=Path(sys.argv[1]).resolve(); l=p.parents[1]/"bootstrap/product_access.py"; n={"__file__":str(l),"_AO_LOADER_SOURCE":l.read_bytes()}; exec(compile(n["_AO_LOADER_SOURCE"],str(l),"exec"),n); raise SystemExit(n["shell_entry"](str(p),sys.argv[2:]))' "$0" "$@"
# AO_PRODUCT_SHELL_END
set -euo pipefail

ao_stage="核验产品根目录与操作条件"
trap 'ao_status=$?; printf "[AgenticOps][回退][失败] %s（退出码=%s）\n" "$ao_stage" "$ao_status" >&2; exit "$ao_status"' ERR

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
state_tool="$install_root/bootstrap/product_state.py"
test -d "$install_root/.git" || { printf '[AgenticOps][回退][失败] 未找到安装目录：%s（退出码=2）\n' "$install_root" >&2; exit 2; }
mode="$(python3 "$state_tool" --product-root "$install_root" read --field mode)"
test "$mode" = "installed" || { printf '[AgenticOps][回退][失败] rollback 只适用于安装目录；产品源码请使用 Git 流程（退出码=2）\n' >&2; exit 2; }
# shellcheck source=bootstrap/lifecycle-common.sh
. "$install_root/bootstrap/lifecycle-common.sh"
lifecycle_acquire_lock "$install_root" "rollback:installed"
lifecycle_require_clean_tree "$install_root" "使用"
recorded_ref="$(python3 "$state_tool" --product-root "$install_root" read --field current_ref)"
test "$(git -C "$install_root" rev-parse HEAD)" = "$recorded_ref" || {
  printf '[AgenticOps][回退][失败] 安装目录 HEAD 与本地配置不一致，拒绝回退（退出码=2）\n' >&2
  exit 2
}
repository="$(python3 "$state_tool" --product-root "$install_root" read --field repository)"
lifecycle_require_recorded_remote "$install_root" "$repository" "安装目录"
previous_ref="$(python3 "$state_tool" --product-root "$install_root" read --field previous_ref)"
test -n "$previous_ref" || { printf '[AgenticOps][回退][失败] 没有可回退版本（退出码=2）\n' >&2; exit 2; }
current_ref="$(git -C "$install_root" rev-parse HEAD)"
git -C "$install_root" cat-file -e "${previous_ref}^{commit}"
python3 "$install_root/bootstrap/station_compatibility.py" \
  --product-root "$install_root" check-upgrade \
  --current-ref "$current_ref" --target-ref "$previous_ref" --operation rollback
ao_stage="切换回退版本"
printf '[AgenticOps][回退][进行] %s\n' "$ao_stage" >&2
lifecycle_refresh_install_scope "$install_root" "$previous_ref"
git -C "$install_root" checkout --quiet --detach "$previous_ref"
python3 "$state_tool" --product-root "$install_root" update-ref \
  --current-ref "$previous_ref" --previous-ref "$current_ref"
printf '[AgenticOps][回退][完成] 安装目录已回退：%s -> %s\n' "$current_ref" "$previous_ref"
