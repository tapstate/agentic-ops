#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
cd "$repo_root"

current_os="$(uname -s | tr '[:upper:]' '[:lower:]')"
current_arch="$(uname -m)"
case "$current_os" in
  darwin) target_os="darwin" ;;
  linux) target_os="linux" ;;
  *) echo "unsupported OS: $current_os" >&2; exit 1 ;;
esac
case "$current_arch" in
  arm64|aarch64) target_arch="arm64" ;;
  x86_64|amd64) target_arch="amd64" ;;
  *) echo "unsupported arch: $current_arch" >&2; exit 1 ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

source_repo="$tmp_dir/source"
home_dir="$tmp_dir/home"
workspace_root="$tmp_dir/workspace"

mkdir -p "$source_repo" "$home_dir" "$workspace_root"
export HOME="$home_dir"
tar \
  --exclude .git \
  --exclude .agentic-ops \
  --exclude .superpowers \
  --exclude .idea \
  --exclude dist \
  -cf - . | tar -C "$source_repo" -xf -

git -C "$source_repo" init -b main >/dev/null
git -C "$source_repo" config user.email agentic-ops-test@example.test
git -C "$source_repo" config user.name "AgenticOps Test"
git -C "$source_repo" add .
git -C "$source_repo" commit -m "test source" >/dev/null

AGENTIC_OPS_REPO_URL="$source_repo" bash "$source_repo/scripts/install.sh" | grep '"operation":"install"'

cmd="$home_dir/.agentic-ops/bin/agentic-cli"
test -x "$cmd"
test -f "$home_dir/.agentic-ops/install-resources/basic/profiles/tapdata.yaml"

cd "$workspace_root"
"$cmd" workspace init --project tapdata --jira-user harsen@tapdata.io | grep '"operation":"workspace_init"'
"$cmd" agent init | grep '"workspace":"tapdata"'
AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root" "$cmd" preflight | grep '"workspace":"tapdata"'

test -f "$workspace_root/.agentic-ops/profiles/tapdata.yaml"
test -f "$workspace_root/.agentic-ops/agent.json"
test -f "$workspace_root/AGENTS.md"

printf '{"ok":true,"operation":"local_install_flow","target":"%s-%s"}\n' "$target_os" "$target_arch"
