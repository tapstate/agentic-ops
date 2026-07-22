#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

version="${1:-${AGENTIC_OPS_VERSION:-0.1.0-dev}}"
dist_root="${AGENTIC_OPS_DIST_DIR:-dist/build}"
build_dir="$dist_root/$version"
targets="${AGENTIC_OPS_TARGETS:-darwin/arm64 darwin/amd64 linux/arm64 linux/amd64}"
package_path="github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/cli"

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

mkdir -p "$build_dir"

for target in $targets; do
  goos="${target%/*}"
  goarch="${target#*/}"
  target_name="${goos}-${goarch}"
  out_dir="$build_dir/$target_name"
  binary="$out_dir/agent-task-ops"

  mkdir -p "$out_dir"
  CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
    go build \
      -trimpath \
      -ldflags "-s -w -X ${package_path}.Version=${version}" \
      -o "$binary" \
      ./packages/agent-task-ops/cmd/agent-task-ops

  checksum_file "$binary" > "$binary.sha256"
done

printf '{"ok":true,"operation":"build","version":"%s","build_dir":"%s"}\n' "$version" "$build_dir"
