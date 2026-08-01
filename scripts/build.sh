#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

install_resources_dir="${AGENTIC_OPS_INSTALL_RESOURCES_DIR:-install-resources}"
version_state="${AGENTIC_OPS_VERSION_STATE:-INS}"
case "$version_state" in
  source|src|SRC) version_state="SRC" ;;
  install|ins|INS) version_state="INS" ;;
  dev|DEV) version_state="DEV" ;;
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

if [ ! -d "$install_resources_dir/basic" ]; then
  echo "install resource base not found: $install_resources_dir/basic" >&2
  exit 1
fi

asset_manifest="$install_resources_dir/basic/manifest.json"
if [ ! -f "$asset_manifest" ]; then
  echo "asset manifest not found: $asset_manifest" >&2
  exit 1
fi
grep -q '"asset_version"' "$asset_manifest"
grep -q '"min_cli_version"' "$asset_manifest"
grep -q '"compatibility_policy": "exact_pair"' "$asset_manifest"
grep -q '"asset_source"' "$asset_manifest"

for target in $targets; do
  goos="${target%/*}"
  goarch="${target#*/}"
  target_name="${goos}-${goarch}"
  out_dir="$install_resources_dir/$target_name"
  binary="$out_dir/agentic-cli"

  mkdir -p "$out_dir"
  CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
    go build \
      -trimpath \
      -ldflags "-s -w -X ${package_path}.Version=${version} -X ${package_path}.VersionState=${version_state} -X ${package_path}.IterationVersion=${iteration_version} -X ${package_path}.CommitIndex=${commit_index} -X ${package_path}.Commit=${commit} -X ${package_path}.BuildTime=${build_time}" \
      -o "$binary" \
      ./packages/agentic-cli/cmd/agentic-cli
  chmod 0755 "$binary"
done

AGENTIC_OPS_INSTALL_RESOURCES_DIR="$install_resources_dir" bash scripts/update-checksums.sh >/dev/null

printf '{"ok":true,"operation":"build","version":"%s","version_state":"%s","iteration_version":"%s","commit_index":%s,"commit":"%s","build_time":"%s","install_resources_dir":"%s"}\n' "$version" "$version_state" "$iteration_version" "$commit_index" "$commit" "$build_time" "$install_resources_dir"
