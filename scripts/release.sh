#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

dist_root="${AGENTIC_OPS_DIST_DIR:-dist/build}"
release_root="${AGENTIC_OPS_RELEASE_DIR:-dist/release}"
targets="${AGENTIC_OPS_TARGETS:-darwin/arm64 darwin/amd64 linux/arm64 linux/amd64}"
version_state="RES"

confirm_value() {
  local label="$1"
  local value="$2"
  local answer=""
  printf '%s [%s] press Enter to continue: ' "$label" "$value" >&2
  IFS= read -r answer || true
  if [ -n "$answer" ] && [ "$answer" != "$value" ]; then
    printf '%s is generated automatically and cannot be overridden\n' "$label" >&2
    exit 1
  fi
  printf '%s' "$value"
}

if [ "$#" -gt 0 ]; then
  echo "release version is generated automatically; do not pass version arguments" >&2
  exit 1
fi
if [ -n "${AGENTIC_OPS_VERSION:-}" ]; then
  echo "AGENTIC_OPS_VERSION is not supported for release; version is generated automatically" >&2
  exit 1
fi

if [ "${AGENTIC_OPS_RELEASE_TEST_MODE:-}" = "1" ]; then
  generated_iteration_version="${AGENTIC_OPS_ITERATION_VERSION:-v0.0}"
  generated_commit_index="${AGENTIC_OPS_COMMIT_INDEX:-0}"
  generated_commit="${AGENTIC_OPS_COMMIT:-$(git rev-parse --short HEAD)}"
  generated_version="$(AGENTIC_OPS_VERSION_TEST_MODE="1" AGENTIC_OPS_ITERATION_VERSION="$generated_iteration_version" AGENTIC_OPS_COMMIT_INDEX="$generated_commit_index" AGENTIC_OPS_COMMIT="$generated_commit" bash scripts/version.sh "$version_state")"
else
  generated_commit="$(git rev-parse --short HEAD)"
  generated_version="$(bash scripts/version.sh "$version_state")"
fi
version="$(confirm_value "Release version" "$generated_version")"
asset_version="$version"
version_tail="${version#${version_state}-}"
iteration_with_index="${version_tail%-*}"
iteration_version="${iteration_with_index%.*}"
commit_index="${iteration_with_index##*.}"

build_dir="$dist_root/$version"
release_dir="$release_root/$version"

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

AGENTIC_OPS_DIST_DIR="$dist_root" AGENTIC_OPS_TARGETS="$targets" AGENTIC_OPS_VERSION_STATE="$version_state" AGENTIC_OPS_COMMIT="$generated_commit" AGENTIC_OPS_GENERATED_VERSION="$version" bash scripts/build.sh >/dev/null

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
  printf '"version_state":"%s",' "$version_state"
  printf '"iteration_version":"%s",' "$iteration_version"
  printf '"commit_index":%s,' "$commit_index"
  printf '"asset_version":"%s",' "$asset_version"
  printf '"support_policy":"latest_only",'
  printf '"update_policy":"auto_update_to_latest_recommended",'
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

printf '{"ok":true,"operation":"release","version":"%s","version_state":"%s","iteration_version":"%s","commit_index":%s,"asset_version":"%s","release_dir":"%s"}\n' "$version" "$version_state" "$iteration_version" "$commit_index" "$asset_version" "$release_dir"
