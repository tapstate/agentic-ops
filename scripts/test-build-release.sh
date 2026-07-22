#!/usr/bin/env bash
set -euo pipefail

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

target="$(go env GOOS)/$(go env GOARCH)"
version="0.1.0-test"
asset_version="2026.07.22.1"
export GOCACHE="$tmp_dir/go-cache"
export GOMODCACHE="$tmp_dir/go-mod-cache"

AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/build" \
  bash scripts/build.sh "$version"

target_name="${target/\//-}"
binary="$tmp_dir/build/$version/$target_name/agent-task-ops"

test -x "$binary"
"$binary" --version | grep "\"version\":\"$version\""
test -f "$binary.sha256"

AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/build" \
AGENTIC_OPS_RELEASE_DIR="$tmp_dir/release" \
AGENTIC_OPS_ASSET_VERSION="$asset_version" \
  bash scripts/release.sh "$version"

release_dir="$tmp_dir/release/$version"

test -f "$release_dir/agent-task-ops_${version}_${target_name}.tar.gz"
test -f "$release_dir/agentic-ops-assets_${asset_version}.tar.gz"
test -f "$release_dir/checksums.txt"
test -f "$release_dir/manifest.json"

tar -tzf "$release_dir/agentic-ops-assets_${asset_version}.tar.gz" | grep '^assets/manifest.json$'
grep "\"version\":\"$version\"" "$release_dir/manifest.json"
grep "\"asset_version\":\"$asset_version\"" "$release_dir/manifest.json"
