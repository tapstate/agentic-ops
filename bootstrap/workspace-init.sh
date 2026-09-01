#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
workspace=""
agents=()
project="tapdata"
repository_pool=""

usage() {
  printf '用法：workspace-init.sh --workspace <项目工作空间> [--agent <Agent ID>]... [--project <项目>] [--repository-pool <目录>]\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      workspace="$2"
      shift 2
      ;;
    --agent)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      agents+=("$2")
      shift 2
      ;;
    --project)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      project="$2"
      shift 2
      ;;
    --repository-pool)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      repository_pool="$2"
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

test -n "$workspace" || { usage >&2; exit 2; }
install_root="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$install_root")"
workspace="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$workspace")"
case "$workspace" in
  "$install_root"|"$install_root"/*)
    printf 'AgenticOps：项目工作空间不能是产品根目录或其子目录：%s\n' "$workspace" >&2
    exit 2
    ;;
esac
test ! -f "$workspace/.agentic-ops-source" || {
  printf 'AgenticOps：源码仓库不能初始化为业务项目工作空间：%s\n' "$workspace" >&2
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

agent_arguments=()
for agent_id in ${agents[@]+"${agents[@]}"}; do
  agent_arguments+=(--agent "$agent_id")
done
pool_arguments=()
test -z "$repository_pool" || pool_arguments+=(--repository-pool "$repository_pool")
python3 "$install_root/bootstrap/render.py" \
  --install-home "$install_root" --workspace "$workspace" \
  --project "$project" ${agent_arguments[@]+"${agent_arguments[@]}"} \
  ${pool_arguments[@]+"${pool_arguments[@]}"}
mkdir -p "$workspace/.agenticops/tasks"
chmod 0700 "$workspace/.agenticops" "$workspace/.agenticops/tasks"
python3 - "$install_root" "$workspace" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from workflow import task_store
task_store.migrate_legacy(sys.argv[2])
PY
python3 "$install_root/bootstrap/workspace_registry.py" \
  --product-root "$install_root" register --workspace "$workspace"

printf 'AgenticOps 项目工作空间已初始化：%s（project=%s）\n' "$workspace" "$project"
printf '统一入口：cd %s && ./agenticops doctor\n' "$workspace"
