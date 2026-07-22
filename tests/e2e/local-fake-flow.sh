#!/usr/bin/env bash
set -euo pipefail

export GOCACHE="${GOCACHE:-/tmp/agentic-ops-go-cache}"
export GOMODCACHE="${GOMODCACHE:-/tmp/agentic-ops-go-mod-cache}"

workspace_root="$(mktemp -d)"
trap 'rm -rf "$workspace_root"' EXIT
export AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root"

cmd="go run ./packages/agentic-cli/cmd/agentic-cli"
install_root="$workspace_root/install"

$cmd --version | grep '"operation":"version"'
$cmd assets install --source assets --install-dir "$install_root" --version RES-v0.1.1-a68372d | grep '"operation":"assets_install"'
$cmd preflight --workspace tapstate | grep '"operation":"preflight"'
$cmd workspace init --workspace tapstate | grep '"operation":"workspace_init"'
$cmd agent init --workspace tapstate | grep '"operation":"agent_init"'
$cmd list-tasks --workspace tapstate | grep '"key":"TAP-123"'
$cmd takeover-task TAP-123 --workspace tapstate | grep '"current_stage":"takeover_started"'
$cmd resume-takeover --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"resume_takeover"'
$cmd write-evidence --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"write_evidence"'
$cmd feedback report --workspace tapstate --date 2026-07-21 | grep '"runs":3'
test -f "$workspace_root/.agentic-ops/feedback/events.ndjson"
test -f "$workspace_root/.agentic-ops/feedback/daily/2026-07-21.md"
test -f "$install_root/assets/RES-v0.1.1-a68372d/manifest.json"
test -f "$install_root/current.json"
