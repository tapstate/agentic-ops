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
      printf '[AgenticOps][初始化][失败] 未知参数：%s（退出码=2）\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

ao_stage="检查工位条件"
trap 'ao_status=$?; printf "[AgenticOps][初始化][失败] %s（退出码=%s）\n" "$ao_stage" "$ao_status" >&2; exit "$ao_status"' ERR
printf '[AgenticOps][初始化][进行] %s\n' "$ao_stage" >&2

test -n "$station" || { usage >&2; exit 2; }
install_root="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$install_root")"
station="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$station")"
case "$station" in
  "$install_root"|"$install_root"/*)
    printf '[AgenticOps][初始化][失败] 项目工位不能是产品根目录或其子目录：%s（退出码=2）\n' "$station" >&2
    exit 2
    ;;
esac
test ! -f "$station/.agentic-ops-source" || {
  printf '[AgenticOps][初始化][失败] 源码仓库不能初始化为业务项目工位：%s（退出码=2）\n' "$station" >&2
  exit 2
}
test -f "$install_root/contracts/gate-request.schema.json" || {
  printf '[AgenticOps][初始化][失败] 安装不完整：%s（退出码=2）\n' "$install_root" >&2
  exit 2
}
test -f "$install_root/gate/runner.py" || {
  printf '[AgenticOps][初始化][失败] 安装缺少标准 Gate Runner：%s（退出码=2）\n' "$install_root" >&2
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
ao_stage="登记工位"
printf '[AgenticOps][初始化][进行] %s\n' "$ao_stage" >&2
python3 "$install_root/bootstrap/station_registry.py" \
  --product-root "$install_root" --lifecycle-held register --station "$station"
ao_stage="生成项目与 Agent 接线"
printf '[AgenticOps][初始化][进行] %s\n' "$ao_stage" >&2
python3 "$install_root/bootstrap/render.py" \
  --quiet --install-home "$install_root" --station "$station" \
  --project "$project" ${agent_arguments[@]+"${agent_arguments[@]}"} \
  ${source_pool_arguments[@]+"${source_pool_arguments[@]}"} \
  ${reuse_arguments[@]+"${reuse_arguments[@]}"}
ao_stage="读取初始化结果"
python3 - "$station" <<'PYTHON'
import json
import sys
from pathlib import Path
station = Path(sys.argv[1])
config = json.loads((station / ".agenticops/station.json").read_text())
init = json.loads((station / ".agenticops/init.json").read_text())
project = config["project"]
agents = ",".join(config["agents"])
product_ref = init["product_ref"]
print("[AgenticOps][初始化][完成] 项目工位已初始化")
print("  工位目录：%s" % station)
print("  项目：%s" % project)
print("  Agent：%s" % agents)
print("  产品提交：%s" % product_ref)
PYTHON
printf '  下一步：cd %q && ./agenticops station doctor\n' "$station"
printf '  提示：首次使用 Jira 时由 Agent 引导配置 Atlassian 插件。\n'
