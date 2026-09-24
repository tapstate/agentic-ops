#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
station=""
agents=()
project_arguments=()
reuse_arguments=()
source_pool=""

usage() {
  printf '用法：station-init.sh --station <项目工位> [--agent <Agent ID>]... [--project <项目>] [--source-pool <目录>] [--reuse-materials]\n首次初始化必须指定 --project；已有工位省略时沿用绑定，不允许切换项目。\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --station)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      station="$2"
      shift 2
      ;;
    --agent)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      agents+=("$2")
      shift 2
      ;;
    --project)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      project_arguments=(--project "$2")
      shift 2
      ;;
    --reuse-materials)
      reuse_arguments+=(--reuse-materials)
      shift
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
      printf 'AgenticOps：未知参数：%s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

test -n "$station" || { usage >&2; exit 2; }
install_root="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$install_root")"
station="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$station")"
case "$station" in
  "$install_root"|"$install_root"/*)
    printf 'AgenticOps：项目工位不能是产品根目录或其子目录：%s\n' "$station" >&2
    exit 2
    ;;
esac
test ! -f "$station/.agentic-ops-source" || {
  printf 'AgenticOps：源码仓库不能初始化为业务项目工位：%s\n' "$station" >&2
  exit 2
}
test -f "$install_root/contracts/gate-request.schema.json" || {
  printf 'AgenticOps：安装不完整：%s\n' "$install_root" >&2
  exit 2
}
test -f "$install_root/gate/runner.py" || {
  printf 'AgenticOps：安装缺少标准 Gate Runner：%s\n' "$install_root" >&2
  exit 2
}

# shellcheck source=bootstrap/lifecycle-common.sh
. "$install_root/bootstrap/lifecycle-common.sh"
lifecycle_acquire_lock "$install_root" "station-init:$station"

# 在登记前只读确认项目，仍保持登记先于生成以支持失败恢复。
project="$(python3 "$install_root/bootstrap/render.py" \
  --install-home "$install_root" --station "$station" --resolve-project \
  ${project_arguments[@]+"${project_arguments[@]}"})"

agent_arguments=()
for agent_id in ${agents[@]+"${agents[@]}"}; do
  agent_arguments+=(--agent "$agent_id")
done
source_pool_arguments=()
if [ -n "$source_pool" ]; then
  source_pool_arguments=(--source-pool "$source_pool")
fi
python3 "$install_root/bootstrap/station_registry.py" \
  --product-root "$install_root" --lifecycle-held register --station "$station"
python3 "$install_root/bootstrap/render.py" \
  --install-home "$install_root" --station "$station" \
  --project "$project" ${agent_arguments[@]+"${agent_arguments[@]}"} \
  ${source_pool_arguments[@]+"${source_pool_arguments[@]}"} \
  ${reuse_arguments[@]+"${reuse_arguments[@]}"}
printf 'AgenticOps 项目工位已初始化：%s（project=%s）\n' "$station" "$project"
printf '统一入口：cd %s && ./agenticops station doctor\n' "$station"
printf '必需插件：首次使用 Jira 事实时，Agent 会检查 atlassian；缺失时只暂停依赖步骤并引导你在当前 Agent 客户端安装和登录。GitHub 工具由 Agent 按任务自行选择。\n'
