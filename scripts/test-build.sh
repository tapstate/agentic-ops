#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
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
target_dir="${target_os}-${target_arch}"

tmp_dir="$(mktemp -d)"
trap 'chmod -R u+w "$tmp_dir" 2>/dev/null || true; rm -rf "$tmp_dir"' EXIT

export GOCACHE="$tmp_dir/go-cache"
export GOMODCACHE="${GOMODCACHE:-$tmp_dir/go-mod-cache}"

bash scripts/build.sh | grep '"operation":"build"'

test -x "install-resources/darwin-arm64/agentic-cli"
test -x "install-resources/darwin-amd64/agentic-cli"
test -x "install-resources/linux-arm64/agentic-cli"
test -x "install-resources/linux-amd64/agentic-cli"
test -x "install-resources/$target_dir/agentic-cli"
test -f "skills/design-takeover-capability/SKILL.md"
test -f install-resources/checksums.txt
grep 'basic/manifest.json' install-resources/checksums.txt >/dev/null
grep "$target_dir/agentic-cli" install-resources/checksums.txt >/dev/null

"install-resources/$target_dir/agentic-cli" --version | grep '"operation":"version"'
"install-resources/$target_dir/agentic-cli" update rollback -h | grep 'Usage: agentic-cli update rollback'

printf '{"ok":true,"operation":"test_build","target":"%s"}\n' "$target_dir"
