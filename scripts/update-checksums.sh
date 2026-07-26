#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

install_resources_dir="${AGENTIC_OPS_INSTALL_RESOURCES_DIR:-install-resources}"
checksums="${AGENTIC_OPS_CHECKSUMS_OUT:-$install_resources_dir/checksums.txt}"

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

if [ ! -d "$install_resources_dir/basic" ]; then
  echo "install resource base not found: $install_resources_dir/basic" >&2
  exit 1
fi

tmp_checksums="$(mktemp)"
trap 'rm -f "$tmp_checksums"' EXIT

while IFS= read -r file; do
  rel="${file#"$install_resources_dir"/}"
  printf '%s  %s\n' "$(checksum_file "$file")" "$rel" >> "$tmp_checksums"
done < <(find "$install_resources_dir/basic" -type f -print | LC_ALL=C sort)

while IFS= read -r file; do
  rel="${file#"$install_resources_dir"/}"
  printf '%s  %s\n' "$(checksum_file "$file")" "$rel" >> "$tmp_checksums"
done < <(find "$install_resources_dir" -mindepth 2 -maxdepth 2 -type f -name agentic-cli -print | LC_ALL=C sort)

mkdir -p "$(dirname "$checksums")"
mv "$tmp_checksums" "$checksums"
trap - EXIT

printf '{"ok":true,"operation":"update_checksums","checksums":"%s"}\n' "$checksums"
