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
test -f "$home_dir/.agentic-ops/install-resources/basic/projects/tapdata/profile.yaml"

project_repo="$tmp_dir/tapdata-project"
mkdir -p "$project_repo"
git -C "$project_repo" init -b main >/dev/null
git -C "$project_repo" config user.email agentic-ops-test@example.test
git -C "$project_repo" config user.name "AgenticOps Test"
printf '# Tapdata project fixture\n' > "$project_repo/README.md"
git -C "$project_repo" add README.md
git -C "$project_repo" commit -m "test project source" >/dev/null

personal_profile="$home_dir/.agentic-ops/user/projects/tapdata/profile.local.yaml"
mkdir -p "$(dirname "$personal_profile")"
printf 'github:\n  repositories:\n    default: %s\n' "$project_repo" > "$personal_profile"

cd "$workspace_root"
"$cmd" workspace init --project tapdata --jira-user lead@example.com | grep '"operation":"workspace_init"'
"$cmd" agent init | grep '"workspace":"tapdata"'
AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root" "$cmd" preflight | grep '"workspace":"tapdata"'
AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root" "$cmd" profile resolve --project tapdata | grep '"project_package"'

test -f "$workspace_root/.agentic-ops/profile.local.yaml"
test ! -f "$workspace_root/.agentic-ops/profiles/tapdata.yaml"
test -d "$workspace_root/repos/tapdata/.git"
test -f "$workspace_root/repos/tapdata/README.md"
grep "source_root: $workspace_root/repos/tapdata" "$workspace_root/.agentic-ops/profile.local.yaml" >/dev/null
maintainer_path_pattern='/Users/lhs/works/'"spaces"
local_user_pattern='user: lead@'"example.com"
if grep -E "$maintainer_path_pattern|$local_user_pattern" "$home_dir/.agentic-ops/install-resources/basic/projects/tapdata/profile.yaml"; then
  echo "shared install profile must not contain maintainer-local configuration" >&2
  exit 1
fi
test -f "$workspace_root/.agentic-ops/agent.json"
test -f "$workspace_root/AGENTS.md"

printf '{"ok":true,"operation":"local_install_flow","target":"%s-%s"}\n' "$target_os" "$target_arch"
