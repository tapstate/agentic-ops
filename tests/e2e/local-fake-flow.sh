#!/usr/bin/env bash
set -euo pipefail

export GOCACHE="${GOCACHE:-/tmp/agentic-ops-go-cache}"
export GOMODCACHE="${GOMODCACHE:-/tmp/agentic-ops-go-mod-cache}"
export AGENTIC_OPS_JIRA_ADAPTER="fake"

workspace_root="$(mktemp -d)"
trap 'rm -rf "$workspace_root"' EXIT
export AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root"

cmd="go run ./packages/agentic-cli/cmd/agentic-cli"
install_root="$workspace_root/install"

$cmd --version | grep '"operation":"version"'
$cmd assets install --source install-resources/basic --install-dir "$install_root" --version INS-v0.1.1-a68372d | grep '"operation":"assets_install"'
update_manifest="$workspace_root/update-manifest.json"
cat > "$update_manifest" <<'JSON'
{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "required",
  "reason": "takeover_task 可能写入无效证据",
  "blocked_operations": ["takeover_task"]
}
JSON
$cmd update check --manifest "$update_manifest" | grep '"severity":"required"'
$cmd update apply --manifest "$update_manifest" --install-dir "$install_root" | grep '"operation":"update_apply"'
$cmd contract validate | grep '"operation":"contract_validate"'
$cmd profile validate --workspace tapstate | grep '"operation":"profile_validate"'
profile_source="$workspace_root/tapstate-profile-hotfix.yaml"
profile_backup="install-resources/basic/profiles/tapstate.yaml.bak"
test ! -e "$profile_backup"
cp install-resources/basic/profiles/tapstate.yaml "$profile_source"
$cmd profile update --workspace tapstate --source "$profile_source" | grep '"operation":"profile_update"'
$cmd profile validate --workspace tapstate | grep '"operation":"profile_validate"'
$cmd profile rollback --workspace tapstate | grep '"operation":"profile_rollback"'
rm -f "$profile_backup"
$cmd policy validate --workspace tapstate | grep '"operation":"policy_validate"'
policy_source="$workspace_root/default-policy-hotfix.yaml"
policy_backup="install-resources/basic/policies/default.yaml.bak"
test ! -e "$policy_backup"
cp install-resources/basic/policies/default.yaml "$policy_source"
$cmd policy update --workspace tapstate --source "$policy_source" | grep '"operation":"policy_update"'
$cmd policy validate --workspace tapstate | grep '"operation":"policy_validate"'
$cmd policy rollback --workspace tapstate | grep '"operation":"policy_rollback"'
rm -f "$policy_backup"
$cmd doctor --workspace tapstate | grep '"operation":"doctor"'
$cmd preflight --workspace tapstate | grep '"operation":"preflight"'
$cmd workspace init --project tapstate --jira-user dev@example.com | grep '"operation":"workspace_init"'
$cmd workspace init --project tapstate --jira-user dev@example.com | grep '"profile":'
$cmd agent init --workspace tapstate | grep '"operation":"agent_init"'
$cmd list-tasks --workspace tapstate | grep '"key":"TAP-123"'
$cmd task run TAP-123 --workspace tapstate --process empty | grep '"capability_id":"empty_task_v1"'
$cmd task run TAP-BUG-123 --workspace tapstate | grep '"defect_complexity":"normal"'
$cmd takeover-task TAP-MISSING-REPO --workspace tapstate | grep '"target_repo":"tapstate/tap-api"'
set +e
in_progress_output="$($cmd takeover-task TAP-IN-PROGRESS --workspace tapstate 2>/dev/null)"
in_progress_code="$?"
set -e
test "$in_progress_code" -eq 1
printf '%s\n' "$in_progress_output" | grep '"code":"invalid_takeover_stage"'
$cmd takeover-task TAP-123 --workspace tapstate | grep '"current_agent_id":"agentic-cli-local-agent"'
$cmd resume-takeover --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"previous_stage":"takeover_started"'
$cmd inspect-workspace --workspace tapstate --source-root . | grep '"operation":"inspect_workspace"'
$cmd prepare-pr --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 --source-root . --base main --title "Fix TAP-123" | grep '"create_pr_gate_required":true'
$cmd write-evidence --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"audit_submitted":true'
release_output="$($cmd release-agent --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 --issue-key TAP-123 --completion-evidence evidence.md)"
printf '%s\n' "$release_output" | grep '"audit_submitted":true'
printf '%s\n' "$release_output" | grep '"current_agent_id_cleared":true'
$cmd feedback bundle --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 --redact | grep '"operation":"feedback_bundle"'
$cmd feedback report --workspace tapstate --date 2026-07-21 | grep '"runs":12'
$cmd feedback report --workspace tapstate --date 2026-07-21 | grep '"blocked":1'
test -f "$workspace_root/.agentic-ops/feedback/events.ndjson"
test -f "$workspace_root/.agentic-ops/profiles/tapstate.yaml"
test -d "$workspace_root/.agentic-ops/run-logs"
test -f "$workspace_root/.agentic-ops/feedback/bundles/TAP-123-takeover-20260721103012-a8f3.md"
test -f "$workspace_root/.agentic-ops/feedback/reports/2026-07-21.md"
test -f "$install_root/assets/INS-v0.1.1-a68372d/manifest.json"
test -f "$install_root/current.json"
