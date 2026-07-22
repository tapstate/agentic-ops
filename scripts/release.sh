#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

version="${1:-${AGENTIC_OPS_VERSION:-0.1.0-dev}}"
asset_version="${AGENTIC_OPS_ASSET_VERSION:-$version}"
dist_root="${AGENTIC_OPS_DIST_DIR:-dist/build}"
release_root="${AGENTIC_OPS_RELEASE_DIR:-dist/release}"
build_dir="$dist_root/$version"
release_dir="$release_root/$version"
targets="${AGENTIC_OPS_TARGETS:-darwin/arm64 darwin/amd64 linux/arm64 linux/amd64}"

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

AGENTIC_OPS_DIST_DIR="$dist_root" AGENTIC_OPS_TARGETS="$targets" bash scripts/build.sh "$version" >/dev/null

mkdir -p "$release_dir"
checksums="$release_dir/checksums.txt"
manifest="$release_dir/manifest.json"
: > "$checksums"

artifact_names=()
artifact_targets=()
artifact_types=()

for target in $targets; do
  target_name="${target/\//-}"
  binary="$build_dir/$target_name/agent-task-ops"
  package="agent-task-ops_${version}_${target_name}.tar.gz"
  package_path="$release_dir/$package"

  tar -C "$build_dir/$target_name" -czf "$package_path" agent-task-ops
  printf '%s  %s\n' "$(checksum_file "$package_path")" "$package" >> "$checksums"

  artifact_names+=("$package")
  artifact_targets+=("$target_name")
  artifact_types+=("binary")
done

asset_package="agentic-ops-assets_${asset_version}.tar.gz"
asset_package_path="$release_dir/$asset_package"
tar -czf "$asset_package_path" assets
printf '%s  %s\n' "$(checksum_file "$asset_package_path")" "$asset_package" >> "$checksums"

artifact_names+=("$asset_package")
artifact_targets+=("all")
artifact_types+=("assets")

{
  printf '{'
  printf '"version":"%s",' "$version"
  printf '"asset_version":"%s",' "$asset_version"
  printf '"artifacts":['
  for i in "${!artifact_names[@]}"; do
    if [ "$i" -gt 0 ]; then
      printf ','
    fi
    printf '{"name":"%s","target":"%s","type":"%s"}' "${artifact_names[$i]}" "${artifact_targets[$i]}" "${artifact_types[$i]}"
  done
  printf ']}'
  printf '\n'
} > "$manifest"

printf '{"ok":true,"operation":"release","version":"%s","asset_version":"%s","release_dir":"%s"}\n' "$version" "$asset_version" "$release_dir"
