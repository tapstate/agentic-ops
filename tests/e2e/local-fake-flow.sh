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
profile_backup="profiles/tapstate.yaml.bak"
test ! -e "$profile_backup"
cp profiles/tapstate.yaml "$profile_source"
$cmd profile update --workspace tapstate --source "$profile_source" | grep '"operation":"profile_update"'
$cmd profile validate --workspace tapstate | grep '"operation":"profile_validate"'
$cmd profile rollback --workspace tapstate | grep '"operation":"profile_rollback"'
rm -f "$profile_backup"
$cmd policy validate --workspace tapstate | grep '"operation":"policy_validate"'
policy_source="$workspace_root/default-policy-hotfix.yaml"
policy_backup="assets/policies/default.yaml.bak"
test ! -e "$policy_backup"
cp assets/policies/default.yaml "$policy_source"
$cmd policy update --workspace tapstate --source "$policy_source" | grep '"operation":"policy_update"'
$cmd policy validate --workspace tapstate | grep '"operation":"policy_validate"'
$cmd policy rollback --workspace tapstate | grep '"operation":"policy_rollback"'
rm -f "$policy_backup"
$cmd doctor --workspace tapstate | grep '"operation":"doctor"'
$cmd preflight --workspace tapstate | grep '"operation":"preflight"'
$cmd workspace init --workspace tapstate --jira-user dev@example.com --jira-project TAP | grep '"operation":"workspace_init"'
$cmd agent init --workspace tapstate | grep '"operation":"agent_init"'
$cmd list-tasks --workspace tapstate | grep '"key":"TAP-123"'
set +e
missing_repo_output="$($cmd takeover-task TAP-MISSING-REPO --workspace tapstate 2>/dev/null)"
missing_repo_code="$?"
set -e
test "$missing_repo_code" -eq 1
printf '%s\n' "$missing_repo_output" | grep '"code":"missing_target_repo"'
$cmd takeover-task TAP-123 --workspace tapstate | grep '"current_agent_id":"agentic-cli-local-agent"'
$cmd resume-takeover --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"resume_takeover"'
$cmd write-evidence --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"write_evidence"'
$cmd release-agent --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 --issue-key TAP-123 --completion-evidence evidence.md | grep '"current_agent_id_cleared":true'
$cmd feedback bundle --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 --redact | grep '"operation":"feedback_bundle"'
$cmd feedback report --workspace tapstate --date 2026-07-21 | grep '"runs":5'
$cmd feedback report --workspace tapstate --date 2026-07-21 | grep '"blocked":1'
test -f "$workspace_root/.agentic-ops/feedback/events.ndjson"
test -f "$workspace_root/.agentic-ops/feedback/bundles/TAP-123-takeover-20260721103012-a8f3.md"
test -f "$workspace_root/.agentic-ops/feedback/reports/2026-07-21.md"
test -f "$install_root/assets/RES-v0.1.1-a68372d/manifest.json"
test -f "$install_root/current.json"
