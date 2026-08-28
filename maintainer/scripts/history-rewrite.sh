#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[ "$#" -eq 1 ] || { echo "usage: history-rewrite.sh <approved-manifest.json>" >&2; exit 2; }
manifest="$1"
[ -f "$manifest" ] || { echo "history_rewrite_manifest_not_found" >&2; exit 1; }

read_field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$manifest" "$1"; }
for field in jira_key source_root_commit candidate_root_commit expected_main expected_develop candidate_commit; do
  value="$(read_field "$field")" || { echo "history_rewrite_manifest_invalid:$field" >&2; exit 1; }
  printf -v "$field" '%s' "$value"
done
case "$jira_key" in AO-[1-9][0-9]*) ;; *) echo "history_rewrite_manifest_invalid:jira_key" >&2; exit 1;; esac
for sha in "$source_root_commit" "$candidate_root_commit" "$expected_main" "$expected_develop" "$candidate_commit"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "history_rewrite_manifest_invalid:sha" >&2; exit 1; }
done
git -C "$repo_root" diff --quiet || { echo "history_rewrite_worktree_dirty" >&2; exit 1; }
git -C "$repo_root" fetch origin main develop --prune
[ "$(git -C "$repo_root" rev-parse origin/main)" = "$expected_main" ] || { echo "history_rewrite_main_changed" >&2; exit 1; }
[ "$(git -C "$repo_root" rev-parse origin/develop)" = "$expected_develop" ] || { echo "history_rewrite_develop_changed" >&2; exit 1; }
git -C "$repo_root" cat-file -e "$candidate_commit^{commit}" || { echo "history_rewrite_candidate_missing" >&2; exit 1; }
[ "$(git -C "$repo_root" rev-list --max-parents=0 "$candidate_commit")" = "$candidate_root_commit" ] || { echo "history_rewrite_candidate_root_invalid" >&2; exit 1; }
delete_refs="$(python3 -c 'import json,sys; print("\\n".join(json.load(open(sys.argv[1])).get("delete_refs", [])))' "$manifest")"
push_refs=("$candidate_commit:refs/heads/main" "$candidate_commit:refs/heads/develop")
while IFS= read -r ref; do
  [ -z "$ref" ] && continue
  case "$ref" in refs/heads/release/*|refs/tags/v*) push_refs+=(":$ref");; *) echo "history_rewrite_delete_ref_invalid" >&2; exit 1;; esac
done <<< "$delete_refs"
AGENTIC_OPS_SPECIAL_PUSH=history-rewrite AGENTIC_OPS_HISTORY_REWRITE_JIRA="$jira_key" \
  git -C "$repo_root" push --atomic --force-with-lease="refs/heads/main:$expected_main" --force-with-lease="refs/heads/develop:$expected_develop" origin \
  "${push_refs[@]}"
git -C "$repo_root" fetch origin main develop --prune
[ "$(git -C "$repo_root" rev-parse origin/main)" = "$candidate_commit" ] && [ "$(git -C "$repo_root" rev-parse origin/develop)" = "$candidate_commit" ] || { echo "history_rewrite_readback_failed" >&2; exit 1; }
