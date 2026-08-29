#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
test -d "$install_root/.git" || { printf 'AgenticOps：未找到安装目录：%s\n' "$install_root" >&2; exit 2; }

current_ref="$(git -C "$install_root" rev-parse HEAD)"
git -C "$install_root" fetch origin main
target_ref="$(git -C "$install_root" rev-parse origin/main)"
git -C "$install_root" merge --ff-only "$target_ref"
mkdir -p "$install_root/user"
printf '%s\n' "$current_ref" > "$install_root/user/previous-ref"
printf '%s\n' "$target_ref" > "$install_root/user/current-ref"
chmod 0600 "$install_root/user/previous-ref" "$install_root/user/current-ref"
printf 'AgenticOps 已更新：%s -> %s\n' "$current_ref" "$target_ref"
