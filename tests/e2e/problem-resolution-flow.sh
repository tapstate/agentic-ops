#!/usr/bin/env bash
set -euo pipefail

export GOCACHE="${GOCACHE:-/tmp/agentic-ops-go-cache}"
export GOMODCACHE="${GOMODCACHE:-/tmp/agentic-ops-go-mod-cache}"

workspace_root="$(mktemp -d)"
trap 'chmod -R u+w "$workspace_root" 2>/dev/null || true; rm -rf "$workspace_root"' EXIT
export AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root"

cmd=(go run ./packages/agentic-cli/cmd/agentic-cli)
install_root="$workspace_root/install"

"${cmd[@]}" doctor --workspace tapstate | grep '"status":"ok"'
"${cmd[@]}" doctor --workspace tapstate | grep '"github":{"message":"GitHub CLI check requires --check-github","status":"skipped"}'

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
"${cmd[@]}" update check --manifest "$update_manifest" | grep '"severity":"required"'
"${cmd[@]}" update check --manifest "$update_manifest" | grep '"blocked_operations":\["takeover_task"\]'
"${cmd[@]}" update apply --manifest "$update_manifest" --install-dir "$install_root" | grep '"operation":"update_apply"'

"${cmd[@]}" profile validate --workspace tapstate | grep '"operation":"profile_validate"'
profile_source="$workspace_root/tapstate-profile-hotfix.yaml"
profile_backup="install-resources/basic/profiles/tapstate.yaml.bak"
test ! -e "$profile_backup"
cp install-resources/basic/profiles/tapstate.yaml "$profile_source"
"${cmd[@]}" profile update --workspace tapstate --source "$profile_source" | grep '"operation":"profile_update"'
"${cmd[@]}" profile rollback --workspace tapstate | grep '"operation":"profile_rollback"'
rm -f "$profile_backup"

"${cmd[@]}" policy validate --workspace tapstate | grep '"operation":"policy_validate"'
policy_source="$workspace_root/default-policy-hotfix.yaml"
policy_backup="install-resources/basic/policies/default.yaml.bak"
test ! -e "$policy_backup"
cp install-resources/basic/policies/default.yaml "$policy_source"
"${cmd[@]}" policy update --workspace tapstate --source "$policy_source" | grep '"operation":"policy_update"'
"${cmd[@]}" policy rollback --workspace tapstate | grep '"operation":"policy_rollback"'
rm -f "$policy_backup"

"${cmd[@]}" workspace init --workspace tapstate --jira-user dev@example.com --jira-project TAP | grep '"operation":"workspace_init"'
test -d "$workspace_root/.agentic-ops/run-logs"
"${cmd[@]}" takeover-task TAP-MISSING-REPO --workspace tapstate | grep '"target_repo":"tapstate/tap-api"'

"${cmd[@]}" takeover-task TAP-123 --workspace tapstate | grep '"operation":"takeover_task"'
"${cmd[@]}" write-evidence --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"write_evidence"'

events_path="$workspace_root/.agentic-ops/feedback/events.ndjson"
cat >> "$events_path" <<'JSON'
{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"diagnosis-secret","agentic_cli_version":"SRC-source","version_state":"SRC","asset_version":"unknown","task_type":"diagnosis","operation":"doctor","current_stage":"diagnosis","next_action":"ask_owner","ok":false,"code":"agentic_cli_logic_error","gate":"doctor","gate_status":"blocked","human_gate":true,"requires_human_action":true,"message":"token=abc123 password=hidden"}
JSON

"${cmd[@]}" feedback bundle --workspace tapstate --run-id diagnosis-secret --redact | grep '"redacted":true'
bundle_path="$workspace_root/.agentic-ops/feedback/bundles/diagnosis-secret.md"
test -f "$bundle_path"
grep '\[REDACTED\]' "$bundle_path"
if grep -E 'abc123|password=hidden' "$bundle_path"; then
  echo "feedback bundle must redact secrets" >&2
  exit 1
fi

"${cmd[@]}" feedback report --workspace tapstate --date 2026-07-21 | grep '"blocked":1'
grep 'blocked: 1' "$workspace_root/.agentic-ops/feedback/reports/2026-07-21.md"
