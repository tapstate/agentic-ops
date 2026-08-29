#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
workspace=""
agent="both"
project="tapdata"

usage() {
  printf '用法：workspace-init.sh --workspace <项目工作空间> [--agent claude|codex|both] [--project <项目>]\n'
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
      agent="$2"
      shift 2
      ;;
    --project)
      test "$#" -ge 2 || { usage >&2; exit 2; }
      project="$2"
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
case "$agent" in claude|codex|both) ;; *) printf 'AgenticOps：agent 必须是 claude、codex 或 both\n' >&2; exit 2 ;; esac
install_root="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$install_root")"
workspace="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$workspace")"
case "$workspace" in
  "$install_root"|"$install_root"/*)
    printf 'AgenticOps：项目工作空间不能是 Product Root 或其子目录：%s\n' "$workspace" >&2
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

python3 "$install_root/bootstrap/render.py" \
  --install-home "$install_root" --workspace "$workspace" \
  --agent "$agent" --project "$project"
mkdir -p "$workspace/.gate"
chmod 0700 "$workspace/.gate"

if git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1; then
  exclude="$(git -C "$workspace" rev-parse --git-path info/exclude)"
  for pattern in \
    .agenticops.json .gate/ AGENTS.md CLAUDE.md .mcp.json \
    .claude/settings.json .codex/agenticops-hooks.example.json; do
    grep -Fxq "$pattern" "$exclude" 2>/dev/null || printf '%s\n' "$pattern" >> "$exclude"
  done
fi

printf 'AgenticOps 项目工作空间已初始化：%s（project=%s，agent=%s）\n' "$workspace" "$project" "$agent"
printf '统一入口：%s/agenticops doctor --workspace %s\n' "$install_root" "$workspace"
if [ "$agent" = "codex" ] || [ "$agent" = "both" ]; then
  printf 'Codex：请按当前版本支持的 Hook 配置加载 .codex/agenticops-hooks.example.json。\n'
fi
