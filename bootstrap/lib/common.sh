#!/usr/bin/env bash

set -euo pipefail

agentic_bootstrap_error() {
  local code="$1"
  local message="$2"
  local action="$3"

  printf 'AgenticOps：%s\n' "$message" >&2
  printf '{"ok":false,"operation":"bootstrap","status":"failed","code":"%s","retry_safe":true,"message":"%s","required_human_action":"%s"}\n' \
    "$code" "$message" "$action"
  exit 1
}

agentic_find_uv() {
  if [ -n "${AGENTIC_OPS_UV:-}" ] && [ -x "$AGENTIC_OPS_UV" ]; then
    printf '%s\n' "$AGENTIC_OPS_UV"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi
  agentic_bootstrap_error \
    "uv_not_found" \
    "未找到 uv，无法准备锁定的 Python 3.12 Runtime" \
    "请先安装 uv，或通过 AGENTIC_OPS_UV 指向可信的 uv 可执行文件"
}

agentic_require_managed_clone() {
  local install_dir="$1"
  if [ ! -e "$install_dir/.git" ]; then
    agentic_bootstrap_error \
      "managed_clone_required" \
      "目标目录不是 AgenticOps managed clone：$install_dir" \
      "请重新执行 bootstrap/install.sh 安装"
  fi
}

agentic_sync_runtime() {
  local install_dir="$1"
  local uv_bin="$2"

  "$uv_bin" sync --locked --project "$install_dir" --python 3.12
  mkdir -p "$install_dir/bin"
  install -m 0755 "$install_dir/bootstrap/agentic-cli" "$install_dir/bin/agentic-cli"
  "$install_dir/bin/agentic-cli" \
    --workspace-root "$install_dir" \
    --mode source_maintenance \
    workspace inspect >/dev/null
}

agentic_write_refs() {
  local install_dir="$1"
  local previous_ref="$2"
  local current_ref="$3"

  mkdir -p "$install_dir/.local"
  if [ -n "$previous_ref" ]; then
    printf '%s\n' "$previous_ref" > "$install_dir/.local/previous-ref"
  fi
  printf '%s\n' "$current_ref" > "$install_dir/.local/current-ref"
}
