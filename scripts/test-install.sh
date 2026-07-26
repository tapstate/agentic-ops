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

bad_repo="$tmp_dir/bad-source"
bad_home_dir="$tmp_dir/bad-home"
mkdir -p "$bad_home_dir"
git clone "$source_repo" "$bad_repo" >/dev/null 2>&1
git -C "$bad_repo" config user.email agentic-ops-test@example.test
git -C "$bad_repo" config user.name "AgenticOps Test"
printf '\n# checksum drift\n' >> "$bad_repo/install-resources/basic/ai-assets/README.md"
git -C "$bad_repo" add install-resources/basic/ai-assets/README.md
git -C "$bad_repo" commit -m "break checksum" >/dev/null

bad_install_out="$tmp_dir/bad-install.out"
bad_install_err="$tmp_dir/bad-install.err"
if HOME="$bad_home_dir" SHELL=/bin/zsh AGENTIC_OPS_REPO_URL="$bad_repo" bash "$bad_repo/scripts/install.sh" >"$bad_install_out" 2>"$bad_install_err"; then
  echo "expected install with checksum drift to fail" >&2
  exit 1
fi
grep "AgenticOps install failed before bin/agentic-cli was installed" "$bad_install_err"
grep "Resource checksum mismatch" "$bad_install_err"
grep "Changed file: install-resources/basic/ai-assets/README.md" "$bad_install_err"
grep "Resource-only change: run from tapstate/agentic-ops source repo: bash scripts/update-checksums.sh" "$bad_install_err"
grep "CLI code or platform binary change: run bash scripts/test-build.sh" "$bad_install_err"
test ! -e "$bad_home_dir/.agentic-ops/bin/agentic-cli"

install_out="$tmp_dir/install.out"
install_err="$tmp_dir/install.err"
HOME="$home_dir" SHELL=/bin/zsh AGENTIC_OPS_REPO_URL="$source_repo" bash "$source_repo/scripts/install.sh" >"$install_out" 2>"$install_err"
grep '"source":"managed_clone"' "$install_out"
grep '"path_configured":false' "$install_out"
grep "agentic-cli is installed but not on PATH" "$install_err"
grep 'case ":\$PATH:" in' "$install_err"
grep '"path_profile_configured":true' "$install_out"
grep '"path_profile_updated":true' "$install_out"
grep "PATH entry added to shell profile" "$install_err"
profile_file="$home_dir/.zshrc"
profile_line='export PATH="$HOME/.agentic-ops/bin:$PATH"'
test -f "$profile_file"
test "$(grep -cF "$profile_line" "$profile_file")" -eq 1

install_dir="$home_dir/.agentic-ops"
test -d "$install_dir/.git"
test -f "$install_dir/agent-guides.md"
grep "按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。" "$install_dir/agent-guides.md" >/dev/null
test -f "$install_dir/install-resources/basic/manifest.json"
test -x "$install_dir/bin/agentic-cli"
test -f "$install_dir/.local/current-ref"
test -f "$install_dir/.local/install-log.json"

printf '# local tracked change\n' >> "$install_dir/README.md"
update_out="$tmp_dir/update.out"
update_err="$tmp_dir/update.err"
if HOME="$home_dir" SHELL=/bin/zsh AGENTIC_OPS_REPO_URL="$source_repo" bash "$install_dir/scripts/install.sh" >"$update_out" 2>"$update_err"; then
  echo "expected update without confirmation to fail" >&2
  exit 1
fi
grep "update cancelled" "$update_err"

confirmed_update_out="$tmp_dir/confirmed-update.out"
confirmed_update_err="$tmp_dir/confirmed-update.err"
HOME="$home_dir" SHELL=/bin/zsh AGENTIC_OPS_REPO_URL="$source_repo" AGENTIC_OPS_ASSUME_YES=1 bash "$install_dir/scripts/install.sh" >"$confirmed_update_out" 2>"$confirmed_update_err"
grep '"operation":"update"' "$confirmed_update_out"
grep '"path_profile_configured":true' "$confirmed_update_out"
grep '"path_profile_updated":false' "$confirmed_update_out"
grep "PATH entry already exists in shell profile" "$confirmed_update_err"
test "$(grep -cF "$profile_line" "$profile_file")" -eq 1
test -f "$install_dir/.local/previous-ref"
test -f "$install_dir/.local/update-stash"
test -x "$install_dir/bin/agentic-cli"

"$install_dir/bin/agentic-cli" --version | grep '"operation":"version"'

printf '{"ok":true,"operation":"test_install","target":"%s"}\n' "$target_dir"
