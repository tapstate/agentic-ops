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
git -C "$repo_root" diff --quiet && git -C "$repo_root" diff --cached --quiet || { echo "history_rewrite_worktree_dirty" >&2; exit 1; }
git -C "$repo_root" fetch origin main develop --prune
[ "$(git -C "$repo_root" rev-parse origin/main)" = "$expected_main" ] || { echo "history_rewrite_main_changed" >&2; exit 1; }
[ "$(git -C "$repo_root" rev-parse origin/develop)" = "$expected_develop" ] || { echo "history_rewrite_develop_changed" >&2; exit 1; }
git -C "$repo_root" cat-file -e "$candidate_commit^{commit}" || { echo "history_rewrite_candidate_missing" >&2; exit 1; }
[ "$(git -C "$repo_root" rev-list --max-parents=0 "$candidate_commit")" = "$candidate_root_commit" ] || { echo "history_rewrite_candidate_root_invalid" >&2; exit 1; }
git -C "$repo_root" cat-file -e "$source_root_commit^{commit}" || { echo "history_rewrite_source_root_missing" >&2; exit 1; }
git -C "$repo_root" merge-base --is-ancestor "$source_root_commit" "$expected_main" || { echo "history_rewrite_source_root_unbound" >&2; exit 1; }

ref_updates="$(python3 -c '
import json, re, sys

path, expected_main, expected_develop, candidate = sys.argv[1:]
try:
    data = json.load(open(path, encoding="utf-8"))
    updates = data["ref_updates"]
    if not isinstance(updates, list) or not updates:
        raise ValueError("ref_updates")
    seen = set()
    required = {
        "refs/heads/main": (expected_main, candidate),
        "refs/heads/develop": (expected_develop, candidate),
    }
    rows = []
    for update in updates:
        ref = update["ref"]
        expected = update["expected"]
        target = update.get("target")
        if not isinstance(ref, str) or not re.fullmatch(r"refs/(heads/(main|develop|release/[^ ]+)|tags/v[^ ]+)", ref):
            raise ValueError("ref")
        if ref in seen or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{40}", expected):
            raise ValueError("expected")
        if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[0-9a-f]{40}", target)):
            raise ValueError("target")
        seen.add(ref)
        rows.append((ref, expected, target or "-"))
    for ref, pair in required.items():
        row = next((item for item in rows if item[0] == ref), None)
        if row is None or row[1:] != pair:
            raise ValueError("required")
    print("\n".join("\t".join(row) for row in rows))
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(2)
' "$manifest" "$expected_main" "$expected_develop" "$candidate_commit")" || {
  echo "history_rewrite_manifest_invalid:ref_updates" >&2
  exit 1
}

push_refs=()
lease_args=()
while IFS=$'\t' read -r ref expected target; do
  [ -n "$ref" ] || continue
  remote_value="$(git -C "$repo_root" ls-remote --refs origin "$ref" 2>/dev/null | awk -v expected_ref="$ref" '$2 == expected_ref {print $1; exit}')"
  [ "$remote_value" = "$expected" ] || { echo "history_rewrite_ref_changed:$ref" >&2; exit 1; }
  lease_args+=("--force-with-lease=$ref:$expected")
  if [ "$target" = "-" ]; then
    push_refs+=(":$ref")
    continue
  fi
  case "$ref" in
    refs/heads/*)
      git -C "$repo_root" cat-file -e "$target^{commit}" || { echo "history_rewrite_target_missing:$ref" >&2; exit 1; }
      ;;
    refs/tags/*)
      [ "$(git -C "$repo_root" cat-file -t "$target" 2>/dev/null || true)" = "tag" ] || { echo "history_rewrite_tag_not_annotated:$ref" >&2; exit 1; }
      ;;
  esac
  push_refs+=("$target:$ref")
done <<< "$ref_updates"

AGENTIC_OPS_SPECIAL_PUSH=history-rewrite AGENTIC_OPS_HISTORY_REWRITE_JIRA="$jira_key" \
  git -C "$repo_root" push --atomic "${lease_args[@]}" origin \
  "${push_refs[@]}"
git -C "$repo_root" fetch origin main develop --prune
while IFS=$'\t' read -r ref _expected target; do
  [ -n "$ref" ] || continue
  remote_value="$(git -C "$repo_root" ls-remote --refs origin "$ref" 2>/dev/null | awk -v expected_ref="$ref" '$2 == expected_ref {print $1; exit}')"
  if [ "$target" = "-" ]; then
    [ -z "$remote_value" ] || { echo "history_rewrite_readback_failed:$ref" >&2; exit 1; }
  else
    [ "$remote_value" = "$target" ] || { echo "history_rewrite_readback_failed:$ref" >&2; exit 1; }
  fi
done <<< "$ref_updates"
