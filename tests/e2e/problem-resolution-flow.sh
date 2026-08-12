#!/usr/bin/env bash
set -euo pipefail

export GOCACHE="${GOCACHE:-/tmp/agentic-ops-go-cache}"
export GOMODCACHE="${GOMODCACHE:-/tmp/agentic-ops-go-mod-cache}"

workspace_root="$(mktemp -d)"
trap 'chmod -R u+w "$workspace_root" 2>/dev/null || true; rm -rf "$workspace_root"' EXIT
export AGENTIC_OPS_WORKSPACE_ROOT="$workspace_root"

cmd=(go run ./packages/agentic-cli/cmd/agentic-cli)
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
install_root="$workspace_root/install"
feedback_date="$(date -u +%F)"
completion_body="$workspace_root/completion-body.md"
cat > "$completion_body" <<'MARKDOWN'
## 变更内容

修复接管原子性和证据链。

## 验证命令与结果

go test ./...：通过。

## 风险

未发现额外风险。

## 恢复说明

无需恢复。

## 事实来源

Jira AO、Git 和 GitHub PR 回读。
MARKDOWN

"${cmd[@]}" doctor --workspace tapstate | grep '"status":"ok"'
"${cmd[@]}" doctor --workspace tapstate | grep '"github":{"message":"GitHub CLI check requires --check-github","status":"skipped"}'

update_manifest="$workspace_root/update-manifest.json"
cat > "$update_manifest" <<JSON
{
  "version": "SRC-source",
  "asset_version": "2026.07.22.1",
  "min_cli_version": "SRC-source",
  "min_asset_version": "2026.07.22.1",
  "compatibility_policy": "exact_pair",
  "migration_required": true,
  "asset_source": {"kind": "local_directory", "path": "$repo_root/install-resources/basic"},
  "severity": "required",
  "reason": "takeover_task 可能写入无效证据",
  "blocked_operations": ["takeover_task"]
}
JSON
"${cmd[@]}" update check --manifest "$update_manifest" --install-dir "$install_root" | grep '"severity":"required"'
"${cmd[@]}" update check --manifest "$update_manifest" --install-dir "$install_root" | grep '"blocked_operations":\["takeover_task"\]'
"${cmd[@]}" update apply --manifest "$update_manifest" --install-dir "$install_root" | grep '"operation":"update_apply"'

"${cmd[@]}" profile validate --workspace tapstate | grep '"operation":"profile_validate"'
profile_source="$workspace_root/tapstate-profile-hotfix.yaml"
profile_backup="install-resources/basic/projects/tapstate/profile.yaml.bak"
test ! -e "$profile_backup"
cp install-resources/basic/projects/tapstate/profile.yaml "$profile_source"
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

"${cmd[@]}" workspace init --project tapstate --jira-user dev@example.com --source-root "$repo_root" | grep '"operation":"workspace_init"'
test -d "$workspace_root/.agentic-ops/run-logs"
"${cmd[@]}" takeover-task TAP-MISSING-REPO --workspace tapstate | grep '"target_repo":"tapstate/tap-api"'

takeover_output="$("${cmd[@]}" takeover-task TAP-123 --workspace tapstate)"
printf '%s\n' "$takeover_output" | grep '"operation":"takeover_task"'
run_id="$(printf '%s\n' "$takeover_output" | sed -n 's/.*"agentic_run_id":"\([^"]*\)".*/\1/p')"
test -n "$run_id"
"${cmd[@]}" write-evidence --workspace tapstate --run-id "$run_id" --content-file "$completion_body" | grep '"operation":"write_evidence"'

events_path="$workspace_root/.agentic-ops/feedback/events.ndjson"
diagnosis_timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat >> "$events_path" <<JSON
{"timestamp":"$diagnosis_timestamp","workspace":"tapstate","agentic_run_id":"diagnosis-secret","agentic_cli_version":"SRC-source","version_state":"SRC","asset_version":"unknown","task_type":"diagnosis","operation":"doctor","current_stage":"diagnosis","agentic_next_action":"ask_owner","ok":false,"code":"agentic_cli_logic_error","gate":"doctor","gate_status":"blocked","human_gate":true,"requires_human_action":true,"message":"token=abc123 password=hidden"}
JSON

recovery_evidence="$workspace_root/recovery-evidence.md"
cat > "$recovery_evidence" <<'MARKDOWN'
# 恢复证据

已回读 Jira 评论与 GitHub 检查状态，确认远端写入完成且不得重试。
MARKDOWN
"${cmd[@]}" feedback record-recovery \
  --workspace tapstate \
  --run-id "$run_id" \
  --issue-key TAP-123 \
  --original-operation write_pr_evidence \
  --original-code github_ci_read_failed \
  --evidence-file "$recovery_evidence" \
  --external-reference jira-comment-46517 \
  --readback-verified=true \
  --remote-write-completed=true \
  --retry-safe=false \
  --confirm-recovery-record | grep '"appended":true'

"${cmd[@]}" feedback bundle --workspace tapstate --run-id diagnosis-secret --redact | grep '"redacted":true'
bundle_path="$workspace_root/.agentic-ops/feedback/bundles/diagnosis-secret.md"
test -f "$bundle_path"
grep '\[REDACTED\]' "$bundle_path"
if grep -E 'abc123|password=hidden' "$bundle_path"; then
  echo "feedback bundle must redact secrets" >&2
  exit 1
fi

"${cmd[@]}" feedback report --workspace tapstate --date "$feedback_date" | grep '"blocked":1'
grep 'blocked: 1' "$workspace_root/.agentic-ops/feedback/reports/$feedback_date.md"

"${cmd[@]}" feedback analyze --workspace tapstate --date "$feedback_date" | grep '"github_ci_read_failed"'
test -f "$workspace_root/.agentic-ops/feedback/reports/analysis-$feedback_date.md"
proposal_output="$("${cmd[@]}" feedback propose --workspace tapstate --date "$feedback_date")"
printf '%s\n' "$proposal_output" | grep '"recovered_count":1'
printf '%s\n' "$proposal_output" | grep -E '"recommended_asset":"[^"]+"'
test -f "$workspace_root/.agentic-ops/feedback/reports/proposals-$feedback_date.md"
