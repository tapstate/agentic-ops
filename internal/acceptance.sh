#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"

usage() {
  cat <<'EOF'
用法：
  internal/acceptance.sh                     # 完整验收
  internal/acceptance.sh quick               # Runtime + 资源边界
  internal/acceptance.sh full                # 四项固定验收
  internal/acceptance.sh <检查项>...          # 按需组合
  internal/acceptance.sh --list

检查项：runtime resources install release
EOF
}

list_checks() {
  cat <<'EOF'
quick: runtime resources
full: runtime resources install release
runtime: Gate、契约、Adapter、Workflow、故事门禁
resources: 工程结构和架构边界
install: 源码维护、安装、工作空间、更新和回退
release: 发布治理
EOF
}

checks=()

add_check() {
  local candidate="$1"
  local existing
  for existing in ${checks[@]+"${checks[@]}"}; do
    [ "$existing" != "$candidate" ] || return 0
  done
  checks+=("$candidate")
}

expand_selection() {
  case "$1" in
    quick)
      add_check runtime
      add_check resources
      ;;
    full)
      add_check runtime
      add_check resources
      add_check install
      add_check release
      ;;
    runtime|resources|install|release)
      add_check "$1"
      ;;
    *)
      printf 'AgenticOps：未知验收项：%s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
}

if [ "${1:-}" = "--list" ]; then
  [ "$#" -eq 1 ] || { usage >&2; exit 2; }
  list_checks
  exit 0
fi
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi
if [ "$#" -eq 0 ]; then
  set -- full
fi
for selection in "$@"; do
  expand_selection "$selection"
done

run_id="$(date '+%Y%m%d-%H%M%S')-$$"
run_root="$repo_root/.local/acceptance/$run_id"
summary="$run_root/summary.tsv"
mkdir -p "$run_root"
chmod 0700 "$repo_root/.local" "$repo_root/.local/acceptance" "$run_root"
printf 'check\tstatus\texit_code\tduration_seconds\tlog\n' > "$summary"
chmod 0600 "$summary"

run_check() {
  local check_id="$1"
  local script
  local log="$run_root/$check_id.log"
  local started
  local ended
  local status
  local code
  case "$check_id" in
    runtime) script="$repo_root/internal/tests/test_runtime.sh" ;;
    resources) script="$repo_root/internal/tests/test_resources.sh" ;;
    install) script="$repo_root/tests/test_install.sh" ;;
    release) script="$repo_root/internal/tests/test_release.sh" ;;
    *) printf '内部错误：未登记验收项 %s\n' "$check_id" >&2; return 2 ;;
  esac

  printf '\n[%s] 开始\n' "$check_id"
  started="$(date +%s)"
  if bash "$script" >"$log" 2>&1; then
    code=0
    status=PASS
  else
    code=$?
    status=FAIL
  fi
  ended="$(date +%s)"
  chmod 0600 "$log"
  cat "$log"
  printf '[%s] %s（%ss）\n' "$check_id" "$status" "$((ended - started))"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$check_id" "$status" "$code" "$((ended - started))" "$log" >> "$summary"
  [ "$code" -eq 0 ]
}

failed=0
for check_id in ${checks[@]+"${checks[@]}"}; do
  run_check "$check_id" || failed=$((failed + 1))
done

printf '\n验收结果：%s；通过=%s，失败=%s\n' \
  "$([ "$failed" -eq 0 ] && printf PASS || printf FAIL)" \
  "$((${#checks[@]} - failed))" "$failed"
printf '验收记录：%s\n' "$summary"
[ "$failed" -eq 0 ]
