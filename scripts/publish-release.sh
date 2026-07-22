#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/publish-release.sh <release_dir>" >&2
  exit 1
fi

release_dir="$1"
manifest="$release_dir/manifest.json"
checksums="$release_dir/checksums.txt"
gh_bin="${AGENTIC_OPS_GH_BIN:-gh}"

if [ ! -d "$release_dir" ]; then
  echo "release directory not found: $release_dir" >&2
  exit 1
fi
if [ ! -f "$manifest" ]; then
  echo "manifest not found: $manifest" >&2
  exit 1
fi
if [ ! -f "$checksums" ]; then
  echo "checksums not found: $checksums" >&2
  exit 1
fi

version="$(sed -n 's/.*"version":"\([^"]*\)".*/\1/p' "$manifest" | head -n 1)"
if [ -z "$version" ]; then
  echo "manifest version is required" >&2
  exit 1
fi

assets=()
while IFS= read -r artifact; do
  assets+=("$artifact")
done < <(find "$release_dir" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name 'manifest.json' -o -name 'checksums.txt' \) | sort)

if [ "${#assets[@]}" -eq 0 ]; then
  echo "release assets not found: $release_dir" >&2
  exit 1
fi

notes_file="$(mktemp)"
trap 'rm -f "$notes_file"' EXIT
{
  printf '# %s\n\n' "$version"
  printf 'AgenticOps release artifact set.\n\n'
  printf '%s\n' '- manifest.json'
  printf '%s\n' '- checksums.txt'
  printf '%s\n' '- binary and asset archives'
} > "$notes_file"

action="upload"
if [ -n "${AGENTIC_OPS_GITHUB_REPO:-}" ]; then
  view_args=(release view "$version" --repo "$AGENTIC_OPS_GITHUB_REPO")
  create_args=(release create "$version" "${assets[@]}" --title "$version" --notes-file "$notes_file" --repo "$AGENTIC_OPS_GITHUB_REPO")
  upload_args=(release upload "$version" "${assets[@]}" --clobber --repo "$AGENTIC_OPS_GITHUB_REPO")
else
  view_args=(release view "$version")
  create_args=(release create "$version" "${assets[@]}" --title "$version" --notes-file "$notes_file")
  upload_args=(release upload "$version" "${assets[@]}" --clobber)
fi

if ! "$gh_bin" "${view_args[@]}" >/dev/null 2>&1; then
  action="create"
  "$gh_bin" "${create_args[@]}"
else
  "$gh_bin" "${upload_args[@]}"
fi

printf '{"ok":true,"operation":"release_publish","version":"%s","release_dir":"%s","action":"%s","assets":%d}\n' "$version" "$release_dir" "$action" "${#assets[@]}"
