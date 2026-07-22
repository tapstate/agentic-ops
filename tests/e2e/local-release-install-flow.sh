#!/usr/bin/env bash
set -euo pipefail

tmp_dir="$(mktemp -d)"
trap 'chmod -R u+w "$tmp_dir" 2>/dev/null || true; rm -rf "$tmp_dir"' EXIT

repo_root="$(pwd)"
target="$(go env GOOS)/$(go env GOARCH)"
target_name="${target/\//-}"
commit="$(git rev-parse --short HEAD)"
iteration_version="v0.1"
commit_index="11"
version="RES-${iteration_version}.${commit_index}-${commit}"

export GOCACHE="${GOCACHE:-$tmp_dir/go-cache}"
export GOMODCACHE="${GOMODCACHE:-$tmp_dir/go-mod-cache}"

printf '\n' | \
AGENTIC_OPS_TARGETS="$target" \
AGENTIC_OPS_DIST_DIR="$tmp_dir/build" \
AGENTIC_OPS_RELEASE_DIR="$tmp_dir/release" \
AGENTIC_OPS_RELEASE_TEST_MODE="1" \
AGENTIC_OPS_ITERATION_VERSION="$iteration_version" \
AGENTIC_OPS_COMMIT_INDEX="$commit_index" \
AGENTIC_OPS_COMMIT="$commit" \
  bash scripts/release.sh

release_dir="$tmp_dir/release/$version"
test -f "$release_dir/agentic-cli_${version}_${target_name}.tar.gz"
test -f "$release_dir/agentic-ops-assets_${version}.tar.gz"

deploy_home="$tmp_dir/home"
AGENTIC_OPS_RELEASE_DIR="$tmp_dir/release" HOME="$deploy_home" bash scripts/init.sh | grep "\"version\":\"$version\""

bin="$deploy_home/.agentic-ops/bin/agentic-cli"
test -x "$bin"
"$bin" --version | grep "\"version\":\"$version\""
"$bin" --version | grep '"version_state":"RES"'

workspace_root="$tmp_dir/workspaces/cyntex"
mkdir -p "$workspace_root"
export AGENTIC_OPS_HOME="$deploy_home/.agentic-ops"
export AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root"

"$bin" preflight --workspace CYNTEX | grep '"operation":"preflight"'
"$bin" preflight --workspace CYNTEX | grep "\"install_dir\":\"$AGENTIC_OPS_HOME\""
"$bin" workspace init --workspace CYNTEX | grep '"operation":"workspace_init"'
"$bin" agent init --workspace CYNTEX | grep '"operation":"agent_init"'
"$bin" list-tasks --workspace CYNTEX | grep '"key":"TAP-123"'
"$bin" takeover-task TAP-123 --workspace CYNTEX | grep '"target_repo":"CYNTEX/example-repo"'
"$bin" resume-takeover --workspace CYNTEX --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"resume_takeover"'
"$bin" write-evidence --workspace CYNTEX --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"write_evidence"'
"$bin" feedback report --workspace CYNTEX --date 2026-07-22 | grep '"runs":3'

test -f "$deploy_home/.agentic-ops/assets/$version/manifest.json"
test -f "$deploy_home/.agentic-ops/current.json"
test -f "$workspace_root/.agentic-ops/feedback/events.ndjson"
test -f "$workspace_root/.agentic-ops/feedback/daily/2026-07-22.md"
