#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
repository="git@github.com:tapstate/agentic-ops.git"
branch="main"

usage() {
  printf '用法：install.sh [--install-home <目录>] [--repository <Git URL>] [--branch <分支>]\n'
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
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'AgenticOps：未知参数：%s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

missing=""
for command_name in git python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    missing="${missing}${missing:+, }${command_name}"
  fi
done
if [ -n "$missing" ]; then
  printf 'AgenticOps：缺少安装依赖：%s\n' "$missing" >&2
  exit 2
fi

python_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  printf 'AgenticOps：需要 Python 3.9+，当前为 %s\n' "$python_version" >&2
  exit 2
fi

if [ -e "$install_root" ]; then
  printf 'AgenticOps：安装目录已存在：%s；请使用 agenticops update 更新\n' "$install_root" >&2
  exit 2
fi

git clone --filter=blob:none --no-checkout --branch "$branch" --single-branch \
  "$repository" "$install_root"
git -C "$install_root" sparse-checkout init --cone
git -C "$install_root" sparse-checkout set adapters bootstrap contracts gate policies projects workflow
git -C "$install_root" checkout "$branch"

current_ref="$(git -C "$install_root" rev-parse HEAD)"
python3 "$install_root/bootstrap/product_state.py" \
  --product-root "$install_root" write \
  --mode installed --repository "$repository" --branch "$branch" \
  --current-ref "$current_ref"

printf 'AgenticOps 安装完成：%s\n' "$install_root"
printf '安装通道：%s（%s）\n' "$branch" "$current_ref"
printf '下一步：%s/agenticops init --workspace <项目工作空间> --project tapdata\n' "$install_root"
