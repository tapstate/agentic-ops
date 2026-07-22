#!/usr/bin/env bash
set -euo pipefail

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
repo_root="$(pwd)"

target="$(go env GOOS)/$(go env GOARCH)"
current_commit="$(git rev-parse --short HEAD)"
iteration_version="v0.1"
commit_index="7"
auto_commit="abc1234"
auto_dev_version="DEV-${iteration_version}.${commit_index}-${auto_commit}"
dev_version="DEV-${iteration_version}.8-${current_commit}"
release_version="RES-${iteration_version}.8-${current_commit}"
env_release_version="RES-${iteration_version}.9-${current_commit}"
prompt_version="RES-${iteration_version}.10-${current_commit}"
export GOCACHE="$tmp_dir/go-cache"
export GOMODCACHE="$tmp_dir/go-mod-cache"

generated_version="$(AGENTIC_OPS_VERSION_TEST_MODE="1" AGENTIC_OPS_ITERATION_VERSION="$iteration_version" AGENTIC_OPS_COMMIT_INDEX="$commit_index" AGENTIC_OPS_COMMIT="$auto_commit" bash scripts/version.sh dev)"
test "$generated_version" = "$auto_dev_version"

AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/auto-build" \
AGENTIC_OPS_BUILD_TEST_MODE="1" \
AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
AGENTIC_OPS_COMMIT_INDEX="$commit_index" \
AGENTIC_OPS_COMMIT="$auto_commit" \
  bash scripts/build.sh

auto_binary="$tmp_dir/auto-build/$auto_dev_version/${target/\//-}/agent-task-ops"
test -x "$auto_binary"
"$auto_binary" --version | grep "\"version\":\"$auto_dev_version\""
"$auto_binary" --version | grep '"version_state":"DEV"'
"$auto_binary" --version | grep "\"iteration_version\":\"$iteration_version\""
"$auto_binary" --version | grep "\"commit_index\":$commit_index"
"$auto_binary" --version | grep "\"commit\":\"$auto_commit\""

if AGENTIC_OPS_TARGETS="$target" \
  AGENTIC_OPS_DIST_DIR="$tmp_dir/reject-build-arg" \
  bash scripts/build.sh "$dev_version"; then
  echo "build.sh must reject positional version arguments" >&2
  exit 1
fi

if AGENTIC_OPS_TARGETS="$target" \
  AGENTIC_OPS_DIST_DIR="$tmp_dir/reject-build-env" \
  AGENTIC_OPS_VERSION="$dev_version" \
  bash scripts/build.sh; then
  echo "build.sh must reject AGENTIC_OPS_VERSION" >&2
  exit 1
fi

if AGENTIC_OPS_TARGETS="$target" \
  AGENTIC_OPS_DIST_DIR="$tmp_dir/reject-build" \
  AGENTIC_OPS_RELEASE_DIR="$tmp_dir/reject-release" \
  bash scripts/release.sh "$release_version"; then
  echo "release.sh must reject positional version arguments" >&2
  exit 1
fi

if AGENTIC_OPS_TARGETS="$target" \
  AGENTIC_OPS_DIST_DIR="$tmp_dir/reject-env-build" \
  AGENTIC_OPS_RELEASE_DIR="$tmp_dir/reject-env-release" \
  AGENTIC_OPS_VERSION="$release_version" \
  bash scripts/release.sh; then
  echo "release.sh must reject AGENTIC_OPS_VERSION" >&2
  exit 1
fi

AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/build" \
AGENTIC_OPS_BUILD_TEST_MODE="1" \
AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
AGENTIC_OPS_COMMIT_INDEX="8" \
AGENTIC_OPS_COMMIT="$current_commit" \
  bash scripts/build.sh

target_name="${target/\//-}"
binary="$tmp_dir/build/$dev_version/$target_name/agent-task-ops"

test -x "$binary"
"$binary" --version | grep "\"version\":\"$dev_version\""
"$binary" --version | grep '"version_state":"DEV"'
test -f "$binary.sha256"

if printf '%s\n' "RES-v0.1.99-override" | \
  AGENTIC_OPS_TARGETS="$target" \
  AGENTIC_OPS_DIST_DIR="$tmp_dir/reject-confirm-build" \
  AGENTIC_OPS_RELEASE_DIR="$tmp_dir/reject-confirm-release" \
  AGENTIC_OPS_RELEASE_TEST_MODE="1" \
  AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
  AGENTIC_OPS_COMMIT_INDEX="8" \
  AGENTIC_OPS_COMMIT="$current_commit" \
  bash scripts/release.sh; then
  echo "release.sh must reject manual release version override at confirmation" >&2
  exit 1
fi

printf '\n' | \
AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/build" \
AGENTIC_OPS_RELEASE_DIR="$tmp_dir/release" \
AGENTIC_OPS_RELEASE_TEST_MODE="1" \
AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
AGENTIC_OPS_COMMIT_INDEX="8" \
AGENTIC_OPS_COMMIT="$current_commit" \
  bash scripts/release.sh

release_dir="$tmp_dir/release/$release_version"

test -f "$release_dir/agent-task-ops_${release_version}_${target_name}.tar.gz"
test -f "$release_dir/agentic-ops-assets_${release_version}.tar.gz"
test -f "$release_dir/checksums.txt"
test -f "$release_dir/manifest.json"

tar -tzf "$release_dir/agentic-ops-assets_${release_version}.tar.gz" | grep '^assets/manifest.json$'
release_binary_dir="$tmp_dir/release-binary"
mkdir -p "$release_binary_dir"
tar -xzf "$release_dir/agent-task-ops_${release_version}_${target_name}.tar.gz" -C "$release_binary_dir"
"$release_binary_dir/agent-task-ops" --version | grep "\"version\":\"$release_version\""
"$release_binary_dir/agent-task-ops" --version | grep '"version_state":"RES"'
"$release_binary_dir/agent-task-ops" --version | grep "\"iteration_version\":\"$iteration_version\""
"$release_binary_dir/agent-task-ops" --version | grep '"commit_index":8'
grep "\"version\":\"$release_version\"" "$release_dir/manifest.json"
grep "\"asset_version\":\"$release_version\"" "$release_dir/manifest.json"
grep '"version_state":"RES"' "$release_dir/manifest.json"
grep "\"iteration_version\":\"$iteration_version\"" "$release_dir/manifest.json"
grep '"commit_index":8' "$release_dir/manifest.json"
grep '"support_policy":"latest_only"' "$release_dir/manifest.json"
grep '"update_policy":"auto_update_to_latest_recommended"' "$release_dir/manifest.json"

printf '\n\n' | \
AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/env-build" \
AGENTIC_OPS_RELEASE_DIR="$tmp_dir/env-release" \
AGENTIC_OPS_RELEASE_TEST_MODE="1" \
AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
AGENTIC_OPS_COMMIT_INDEX="9" \
AGENTIC_OPS_COMMIT="$current_commit" \
  bash scripts/release.sh

env_release_dir="$tmp_dir/env-release/$env_release_version"

test -f "$env_release_dir/agent-task-ops_${env_release_version}_${target_name}.tar.gz"
test -f "$env_release_dir/agentic-ops-assets_${env_release_version}.tar.gz"
grep "\"version\":\"$env_release_version\"" "$env_release_dir/manifest.json"
grep "\"asset_version\":\"$env_release_version\"" "$env_release_dir/manifest.json"
grep '"version_state":"RES"' "$env_release_dir/manifest.json"
grep '"support_policy":"latest_only"' "$env_release_dir/manifest.json"
grep '"update_policy":"auto_update_to_latest_recommended"' "$env_release_dir/manifest.json"

tag_repo="$tmp_dir/tag-repo"
git init "$tag_repo" >/dev/null
git -C "$tag_repo" config user.email test@example.com
git -C "$tag_repo" config user.name test
printf 'base\n' > "$tag_repo/file.txt"
git -C "$tag_repo" add file.txt
git -C "$tag_repo" commit -m base >/dev/null
git -C "$tag_repo" tag "$iteration_version"
printf 'one\n' >> "$tag_repo/file.txt"
git -C "$tag_repo" commit -am one >/dev/null
printf 'two\n' >> "$tag_repo/file.txt"
git -C "$tag_repo" commit -am two >/dev/null
printf 'three\n' >> "$tag_repo/file.txt"
git -C "$tag_repo" commit -am three >/dev/null
tag_commit="$(git -C "$tag_repo" rev-parse --short HEAD)"
tag_version="RES-${iteration_version}.3-${tag_commit}"
tag_generated="$(cd "$tag_repo" && "$repo_root/scripts/version.sh" RES)"
test "$tag_generated" = "$tag_version"

no_tag_repo="$tmp_dir/no-tag-repo"
git init "$no_tag_repo" >/dev/null
git -C "$no_tag_repo" config user.email test@example.com
git -C "$no_tag_repo" config user.name test
printf 'base\n' > "$no_tag_repo/file.txt"
git -C "$no_tag_repo" add file.txt
git -C "$no_tag_repo" commit -m base >/dev/null
if (cd "$no_tag_repo" && "$repo_root/scripts/version.sh" RES); then
  echo "version.sh must require an iteration tag" >&2
  exit 1
fi

printf '\n' | \
AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/prompt-build" \
AGENTIC_OPS_RELEASE_DIR="$tmp_dir/prompt-release" \
AGENTIC_OPS_RELEASE_TEST_MODE="1" \
AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
AGENTIC_OPS_COMMIT_INDEX="10" \
AGENTIC_OPS_COMMIT="$current_commit" \
  bash scripts/release.sh

prompt_release_dir="$tmp_dir/prompt-release/$prompt_version"

test -f "$prompt_release_dir/agent-task-ops_${prompt_version}_${target_name}.tar.gz"
test -f "$prompt_release_dir/agentic-ops-assets_${prompt_version}.tar.gz"
grep "\"version\":\"$prompt_version\"" "$prompt_release_dir/manifest.json"
grep "\"asset_version\":\"$prompt_version\"" "$prompt_release_dir/manifest.json"
grep '"version_state":"RES"' "$prompt_release_dir/manifest.json"
grep '"support_policy":"latest_only"' "$prompt_release_dir/manifest.json"
grep '"update_policy":"auto_update_to_latest_recommended"' "$prompt_release_dir/manifest.json"
