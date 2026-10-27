#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
repository="git@github.com:tapstate/agentic-ops.git"
branch="main"
source_pool="$HOME/.agentic-ops-repos"

usage() {
  printf '用法：install.sh [--install-home <目录>] [--repository <Git URL>] [--branch <分支>] [--source-pool <目录>]\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-home)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      install_root="$2"
      shift 2
      ;;
    --repository)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      repository="$2"
      shift 2
      ;;
    --branch)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      branch="$2"
      shift 2
      ;;
    --source-pool)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      source_pool="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '[AgenticOps][安装][失败] 未知参数：%s（退出码=2）\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

ao_stage="检查安装条件"
trap 'ao_status=$?; printf "[AgenticOps][安装][失败] %s（退出码=%s）\n" "$ao_stage" "$ao_status" >&2; exit "$ao_status"' ERR
printf '[AgenticOps][安装][进行] %s\n' "$ao_stage" >&2

missing=""
for command_name in git python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    missing="${missing}${missing:+, }${command_name}"
  fi
done
if [ -n "$missing" ]; then
  printf '[AgenticOps][安装][失败] 缺少安装依赖：%s（退出码=2）\n' "$missing" >&2
  exit 2
fi

# stdin 安装尚无产品辅助模块，只做同一 3.12 基线的启动检查。
install_python="${AGENTIC_OPS_PYTHON:-${AO_PRODUCT_PYTHON:-$(command -v python3.12 || command -v python3)}}"
if ! "$install_python" -I -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
  printf '[AgenticOps][安装][失败] 需要 Python 3.12.x；请设置 AGENTIC_OPS_PYTHON 为已准备的解释器（退出码=2）\n' >&2
  exit 2
fi
python3() { "$install_python" -I "$@"; }

if [ -e "$install_root" ]; then
  printf '[AgenticOps][安装][失败] 安装目录已存在：%s；请使用 agenticops update 更新（退出码=2）\n' "$install_root" >&2
  exit 2
fi

ao_stage="下载产品（分支=${branch}）"
printf '[AgenticOps][安装][进行] %s\n' "$ao_stage" >&2
# 部分克隆依赖显式材料准备：安装检出会下载当前文件，后续 update 先补对象再本地核验。
# 不得在通用 Git 核验中取消 GIT_NO_LAZY_FETCH 来补偿安装生命周期的准备缺项。
git clone --quiet --filter=blob:none --no-checkout --branch "$branch" --single-branch \
  "$repository" "$install_root"
ao_stage="检出产品文件"
printf '[AgenticOps][安装][进行] %s\n' "$ao_stage" >&2
git -C "$install_root" -c promisor.quiet=true sparse-checkout init --cone
# 此脚本支持通过 stdin 单独执行，不能依赖调用者目录中的辅助文件。
install_paths=(adapters bootstrap contracts docs gate policies projects workflow)
shared_entry="$(git -C "$install_root" ls-tree HEAD -- skills/shared)"
if [ -n "$shared_entry" ]; then
  case "$shared_entry" in
    '040000 tree '*) install_paths+=(skills/shared) ;;
    *) printf '[AgenticOps][安装][失败] 目标共享 Skill 根不是目录，拒绝安装（退出码=2）\n' >&2; exit 2 ;;
  esac
fi
git -C "$install_root" -c promisor.quiet=true sparse-checkout set "${install_paths[@]}"
git -C "$install_root" -c promisor.quiet=true checkout --quiet "$branch"

ao_stage="保存安装配置"
printf '[AgenticOps][安装][进行] %s\n' "$ao_stage" >&2
current_ref="$(git -C "$install_root" rev-parse HEAD)"
if [ -f "$install_root/bootstrap/python_runtime.py" ]; then
  install_python="$(python3 "$install_root/bootstrap/python_runtime.py" prepare --root "$install_root")"
fi
source_pool="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$source_pool")"
python3 "$install_root/bootstrap/product_state.py" \
  --product-root "$install_root" write \
  --mode installed --repository "$repository" --branch "$branch" \
  --current-ref "$current_ref" --source-pool "$source_pool"
python3 "$install_root/bootstrap/station_registry.py" \
  --product-root "$install_root" initialize

printf '[AgenticOps][安装][完成] 产品已安装\n'
printf '  安装目录：%s\n' "$install_root"
printf '  安装通道：%s\n' "$branch"
printf '  产品提交：%s\n' "$current_ref"
printf '  源码池：%s\n' "$source_pool"
printf '  下一步：%q station init --station <项目工位> --project <项目ID>\n' "$install_root/agenticops"
