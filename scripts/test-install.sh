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
trap 'rm -rf "$tmp_dir"' EXIT

source_repo="$tmp_dir/source"
home_dir="$tmp_dir/home"

mkdir -p "$source_repo" "$home_dir"
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

HOME="$home_dir" AGENTIC_OPS_REPO_URL="$source_repo" bash "$source_repo/scripts/install.sh" | grep '"source":"managed_clone"'

install_dir="$home_dir/.agentic-ops"
test -d "$install_dir/.git"
test -f "$install_dir/install-resources/basic/manifest.json"
test -x "$install_dir/bin/agentic-cli"
test -f "$install_dir/.local/current-ref"
test -f "$install_dir/.local/install-log.json"

printf '# local tracked change\n' >> "$install_dir/README.md"
update_out="$tmp_dir/update.out"
update_err="$tmp_dir/update.err"
if HOME="$home_dir" AGENTIC_OPS_REPO_URL="$source_repo" bash "$install_dir/scripts/install.sh" >"$update_out" 2>"$update_err"; then
  echo "expected update without confirmation to fail" >&2
  exit 1
fi
grep "update cancelled" "$update_err"

HOME="$home_dir" AGENTIC_OPS_REPO_URL="$source_repo" AGENTIC_OPS_ASSUME_YES=1 bash "$install_dir/scripts/install.sh" | grep '"operation":"update"'
test -f "$install_dir/.local/previous-ref"
test -f "$install_dir/.local/update-stash"
test -x "$install_dir/bin/agentic-cli"

"$install_dir/bin/agentic-cli" --version | grep '"operation":"version"'

printf '{"ok":true,"operation":"test_install","target":"%s"}\n' "$target_dir"
