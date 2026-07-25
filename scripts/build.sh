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

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

write_checksums() {
  local checksums="$install_resources_dir/checksums.txt"
  local tmp_checksums="$checksums.tmp"
  : > "$tmp_checksums"

  while IFS= read -r file; do
    rel="${file#"$install_resources_dir"/}"
    printf '%s  %s\n' "$(checksum_file "$file")" "$rel" >> "$tmp_checksums"
  done < <(find "$install_resources_dir/basic" -type f -print | LC_ALL=C sort)

  while IFS= read -r file; do
    rel="${file#"$install_resources_dir"/}"
    printf '%s  %s\n' "$(checksum_file "$file")" "$rel" >> "$tmp_checksums"
  done < <(find "$install_resources_dir" -mindepth 2 -maxdepth 2 -type f -name agentic-cli -print | LC_ALL=C sort)

  mv "$tmp_checksums" "$checksums"
}

if [ ! -d "$install_resources_dir/basic" ]; then
  echo "install resource base not found: $install_resources_dir/basic" >&2
  exit 1
fi

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

write_checksums

printf '{"ok":true,"operation":"build","version":"%s","version_state":"%s","iteration_version":"%s","commit_index":%s,"commit":"%s","build_time":"%s","install_resources_dir":"%s"}\n' "$version" "$version_state" "$iteration_version" "$commit_index" "$commit" "$build_time" "$install_resources_dir"
