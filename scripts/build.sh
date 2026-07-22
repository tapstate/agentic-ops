#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

dist_root="${AGENTIC_OPS_DIST_DIR:-dist/build}"
version_state="${AGENTIC_OPS_VERSION_STATE:-DEV}"
case "$version_state" in
  source|src|SRC) version_state="SRC" ;;
  dev|DEV) version_state="DEV" ;;
  release|res|RES) version_state="RES" ;;
  *) echo "unsupported version state: $version_state" >&2; exit 1 ;;
esac

if [ "$#" -gt 0 ]; then
  echo "build version is generated automatically; do not pass version arguments" >&2
  exit 1
fi
if [ -n "${AGENTIC_OPS_VERSION:-}" ]; then
  echo "AGENTIC_OPS_VERSION is not supported for build; version is generated automatically" >&2
  exit 1
fi
targets="${AGENTIC_OPS_TARGETS:-darwin/arm64 darwin/amd64 linux/arm64 linux/amd64}"
package_path="github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cli"
commit="${AGENTIC_OPS_COMMIT:-$(git rev-parse --short HEAD)}"
build_time="${AGENTIC_OPS_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
if [ -n "${AGENTIC_OPS_GENERATED_VERSION:-}" ]; then
  version="$AGENTIC_OPS_GENERATED_VERSION"
elif [ "${AGENTIC_OPS_BUILD_TEST_MODE:-}" = "1" ]; then
  version="$(AGENTIC_OPS_VERSION_TEST_MODE="1" AGENTIC_OPS_COMMIT="$commit" bash scripts/version.sh "$version_state")"
else
  version="$(bash scripts/version.sh "$version_state")"
fi
version_tail="${version#${version_state}-}"
iteration_with_index="${version_tail%-*}"
iteration_version="${iteration_with_index%.*}"
commit_index="${iteration_with_index##*.}"
build_dir="$dist_root/$version"

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
  binary="$out_dir/agentic-cli"

  mkdir -p "$out_dir"
  CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
    go build \
      -trimpath \
      -ldflags "-s -w -X ${package_path}.Version=${version} -X ${package_path}.VersionState=${version_state} -X ${package_path}.IterationVersion=${iteration_version} -X ${package_path}.CommitIndex=${commit_index} -X ${package_path}.Commit=${commit} -X ${package_path}.BuildTime=${build_time}" \
      -o "$binary" \
      ./packages/agentic-cli/cmd/agentic-cli

  checksum_file "$binary" > "$binary.sha256"
done

printf '{"ok":true,"operation":"build","version":"%s","version_state":"%s","iteration_version":"%s","commit_index":%s,"commit":"%s","build_time":"%s","build_dir":"%s"}\n' "$version" "$version_state" "$iteration_version" "$commit_index" "$commit" "$build_time" "$build_dir"
