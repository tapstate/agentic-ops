#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

tmp_dir="$(mktemp -d)"
trap 'chmod -R u+w "$tmp_dir" 2>/dev/null || true; rm -rf "$tmp_dir"' EXIT

test_repo="$tmp_dir/repo"
mkdir -p "$test_repo"
git -C "$test_repo" init -b main >/dev/null
git -C "$test_repo" config user.email agentic-ops-test@example.test
git -C "$test_repo" config user.name "AgenticOps Test"
printf '# release workflow fixture\n' > "$test_repo/README.md"
git -C "$test_repo" add README.md
git -C "$test_repo" commit -m "initial" >/dev/null

if [ ! -x "$repo_root/.githooks/pre-commit" ]; then
  echo "missing pre-commit hook" >&2
  exit 1
fi
if [ ! -x "$repo_root/.githooks/pre-push" ]; then
  echo "missing pre-push hook" >&2
  exit 1
fi
mkdir -p "$test_repo/.githooks"
cp "$repo_root/.githooks/pre-commit" "$test_repo/.githooks/pre-commit"
cp "$repo_root/.githooks/pre-push" "$test_repo/.githooks/pre-push"
git -C "$test_repo" config core.hooksPath .githooks

printf 'blocked\n' > "$test_repo/blocked.txt"
git -C "$test_repo" add blocked.txt
if git -C "$test_repo" commit -m "must fail" >"$tmp_dir/commit.out" 2>"$tmp_dir/commit.err"; then
  echo "expected main commit to be blocked" >&2
  exit 1
fi
grep 'direct commit to main is prohibited' "$tmp_dir/commit.err" >/dev/null

git -C "$test_repo" switch -c develop >/dev/null
git -C "$test_repo" commit -m "develop commit" >/dev/null

head_sha="$(git -C "$test_repo" rev-parse HEAD)"
zero_sha="0000000000000000000000000000000000000000"
if printf 'refs/heads/main %s refs/heads/main %s\n' "$head_sha" "$zero_sha" |
  (cd "$test_repo" && .githooks/pre-push) >"$tmp_dir/push-main.out" 2>"$tmp_dir/push-main.err"; then
  echo "expected main push to be blocked" >&2
  exit 1
fi
grep 'direct push to main is prohibited' "$tmp_dir/push-main.err" >/dev/null

printf 'refs/heads/develop %s refs/heads/develop %s\n' "$head_sha" "$zero_sha" |
  (cd "$test_repo" && .githooks/pre-push)

printf '{"ok":true,"operation":"test_release_workflow","cases":2}\n'
