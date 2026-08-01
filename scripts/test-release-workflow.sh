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

if [ ! -f "$repo_root/scripts/lib/development-workflow.sh" ]; then
  echo "missing development workflow gate" >&2
  exit 1
fi

fake_gh="$tmp_dir/gh"
cat > "$fake_gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_GH_STATE_DIR:?}"
printf '%s\n' "$*" >> "$FAKE_GH_STATE_DIR/calls.log"

if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
  [ ! -f "$FAKE_GH_STATE_DIR/deny-auth-status" ]
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "user" ]; then
  if [ -f "$FAKE_GH_STATE_DIR/deny-api-user" ]; then
    exit 1
  fi
  printf 'HarsenLin\n'
  exit 0
fi

if [ "${1:-}" = "pr" ]; then
  case "${2:-}" in
    list)
      if [ -f "$FAKE_GH_STATE_DIR/deny-pr-list" ]; then
        echo "pull request list denied" >&2
        exit 1
      fi
      if [ -f "$FAKE_GH_STATE_DIR/pr-created" ]; then
        if [[ "$*" == *"select(.headRefOid"* ]] && [[ "$*" != *"$(cat "$FAKE_GH_STATE_DIR/pr-head")"* ]]; then
          exit 0
        fi
        fake_pr_state="OPEN"
        if [ -f "$FAKE_GH_STATE_DIR/pr-merged" ]; then
          fake_pr_state="MERGED"
        elif [ -f "$FAKE_GH_STATE_DIR/pr-closed" ]; then
          fake_pr_state="CLOSED"
        fi
        printf '7\thttps://github.com/tapstate/agentic-ops/pull/7\t%s\t%s\n' "$fake_pr_state" "$(cat "$FAKE_GH_STATE_DIR/pr-head")"
      fi
      exit 0
      ;;
    create)
      if [ -f "$FAKE_GH_STATE_DIR/deny-pr-create" ]; then
        echo "pull request creation denied" >&2
        exit 1
      fi
      printf 'pr-create\n' >> "$FAKE_GH_STATE_DIR/writes.log"
      touch "$FAKE_GH_STATE_DIR/pr-created"
      git rev-parse HEAD > "$FAKE_GH_STATE_DIR/pr-head"
      git rev-parse HEAD > "$FAKE_GH_STATE_DIR/pr-fixed-head"
      previous_argument=""
      for current_argument in "$@"; do
        if [ "$previous_argument" = "--head" ]; then
          printf '%s\n' "$current_argument" > "$FAKE_GH_STATE_DIR/pr-branch"
        fi
        previous_argument="$current_argument"
      done
      printf 'https://github.com/tapstate/agentic-ops/pull/7\n'
      exit 0
      ;;
    view)
      case " $* " in
        *" --json body "*)
          printf '<!-- agentic-ops-fixed-head:%s -->\n' "$(cat "$FAKE_GH_STATE_DIR/pr-fixed-head")"
          ;;
        *" --jq .number "*)
          printf '7\n'
          ;;
        *)
          fake_view_state="OPEN"
          fake_view_merge="-"
          if [ -f "$FAKE_GH_STATE_DIR/pr-merged" ]; then
            fake_view_state="MERGED"
            fake_view_merge="$(cat "$FAKE_GH_STATE_DIR/merge-commit")"
          elif [ -f "$FAKE_GH_STATE_DIR/pr-closed" ]; then
            fake_view_state="CLOSED"
          fi
          if [[ " $* " = *"headRefOid"* ]]; then
            printf '%s\t%s\thttps://github.com/tapstate/agentic-ops/pull/7\t7\t%s\n' "$fake_view_state" "$fake_view_merge" "$(cat "$FAKE_GH_STATE_DIR/pr-head")"
          else
            printf '%s\t%s\thttps://github.com/tapstate/agentic-ops/pull/7\t7\n' "$fake_view_state" "$fake_view_merge"
          fi
          ;;
      esac
      exit 0
      ;;
    merge)
      if [ ! -f "$FAKE_GH_STATE_DIR/pr-merged" ]; then
        printf 'pr-merge\n' >> "$FAKE_GH_STATE_DIR/writes.log"
        fake_merge_dir="$(mktemp -d)"
        git clone "$FAKE_GH_REMOTE" "$fake_merge_dir/repo" >/dev/null 2>&1
        git -C "$fake_merge_dir/repo" config user.email agentic-ops-test@example.test
        git -C "$fake_merge_dir/repo" config user.name "AgenticOps Test"
        git -C "$fake_merge_dir/repo" switch main >/dev/null
        git -C "$fake_merge_dir/repo" merge --no-ff "origin/$(cat "$FAKE_GH_STATE_DIR/pr-branch")" -m "Merge release PR" >/dev/null
        git -C "$fake_merge_dir/repo" push origin main >/dev/null
        git -C "$fake_merge_dir/repo" rev-parse HEAD > "$FAKE_GH_STATE_DIR/merge-commit"
        touch "$FAKE_GH_STATE_DIR/pr-merged"
        rm -rf "$fake_merge_dir"
      fi
      exit 0
      ;;
  esac
fi

if [ "${1:-}" != "api" ]; then
  echo "unsupported fake gh command: $*" >&2
  exit 2
fi

case " $* " in
  *" --method PATCH "*)
    if [ -f "$FAKE_GH_STATE_DIR/deny-repository" ]; then
      echo "repository administration denied" >&2
      exit 1
    fi
    touch "$FAKE_GH_STATE_DIR/repository-configured"
    printf 'repository\n' >> "$FAKE_GH_STATE_DIR/writes.log"
    exit 0
    ;;
  *" --method POST "*|*" --method PUT "*)
    if [ -f "$FAKE_GH_STATE_DIR/deny-ruleset" ]; then
      echo "ruleset administration denied" >&2
      exit 1
    fi
    touch "$FAKE_GH_STATE_DIR/ruleset-configured"
    printf 'ruleset\n' >> "$FAKE_GH_STATE_DIR/writes.log"
    printf '{"id":42}\n'
    exit 0
    ;;
esac

case "$*" in
  *".default_branch"*)
    if [ -f "$FAKE_GH_STATE_DIR/wrong-default-branch" ]; then printf 'develop\n'; else printf 'main\n'; fi
    ;;
  *".allow_auto_merge"*)
    if [ -f "$FAKE_GH_STATE_DIR/repository-configured" ]; then printf 'true\n'; else printf 'false\n'; fi
    ;;
  *".allow_merge_commit"*)
    if [ -f "$FAKE_GH_STATE_DIR/deny-merge-commit" ]; then printf 'false\n'; else printf 'true\n'; fi
    ;;
  *"/rulesets/42"*)
    if [ -f "$FAKE_GH_STATE_DIR/ruleset-configured" ]; then
      printf 'active\tbranch\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t0\tfalse\tfalse\tfalse\n'
    fi
    ;;
  *"/rulesets"*)
    if [ -f "$FAKE_GH_STATE_DIR/ruleset-configured" ]; then printf '42\n'; fi
    ;;
  *)
    echo "unsupported fake gh api: $*" >&2
    exit 2
    ;;
esac
EOF
chmod 0755 "$fake_gh"

fake_merge_pr_manually() {
  fake_merge_dir="$(mktemp -d)"
  git clone "$FAKE_GH_REMOTE" "$fake_merge_dir/repo" >/dev/null 2>&1
  git -C "$fake_merge_dir/repo" config user.email agentic-ops-test@example.test
  git -C "$fake_merge_dir/repo" config user.name "AgenticOps Test"
  git -C "$fake_merge_dir/repo" switch main >/dev/null
  git -C "$fake_merge_dir/repo" merge --no-ff "origin/$(cat "$FAKE_GH_STATE_DIR/pr-branch")" -m "Manual Merge release PR" >/dev/null
  git -C "$fake_merge_dir/repo" push origin main >/dev/null
  git -C "$fake_merge_dir/repo" rev-parse HEAD > "$FAKE_GH_STATE_DIR/merge-commit"
  touch "$FAKE_GH_STATE_DIR/pr-merged"
  rm -rf "$fake_merge_dir"
}

workflow_remote="$tmp_dir/workflow-remote.git"
workflow_repo="$tmp_dir/workflow-repo"
git init --bare "$workflow_remote" >/dev/null
git clone "$workflow_remote" "$workflow_repo" >/dev/null 2>&1
git -C "$workflow_repo" config user.email agentic-ops-test@example.test
git -C "$workflow_repo" config user.name "AgenticOps Test"
printf '# workflow gate fixture\n' > "$workflow_repo/README.md"
git -C "$workflow_repo" add README.md
git -C "$workflow_repo" commit -m "initial" >/dev/null
git -C "$workflow_repo" branch -M main
git -C "$workflow_repo" push -u origin main >/dev/null
git -C "$workflow_repo" switch -c develop >/dev/null
mkdir -p "$workflow_repo/.githooks"
cp "$repo_root/.githooks/pre-commit" "$workflow_repo/.githooks/pre-commit"
cp "$repo_root/.githooks/pre-push" "$workflow_repo/.githooks/pre-push"

fake_gh_state="$tmp_dir/fake-gh-state"
mkdir -p "$fake_gh_state"
: > "$fake_gh_state/calls.log"
: > "$fake_gh_state/writes.log"

export AGENTIC_OPS_GH_BIN="$fake_gh"
export AGENTIC_OPS_RELEASE_REPOSITORY="tapstate/agentic-ops"
export FAKE_GH_STATE_DIR="$fake_gh_state"
export FAKE_GH_REMOTE="$workflow_remote"
# shellcheck source=scripts/lib/development-workflow.sh
. "$repo_root/scripts/lib/development-workflow.sh"

if workflow_check_or_configure check "$workflow_repo" >"$tmp_dir/check.out" 2>"$tmp_dir/check.err"; then
  echo "expected missing workflow configuration" >&2
  exit 1
fi
grep 'workflow_configuration_required' "$tmp_dir/check.err" >/dev/null

if printf 'n\n' | workflow_check_or_configure interactive "$workflow_repo" >"$tmp_dir/reject.out" 2>"$tmp_dir/reject.err"; then
  echo "expected rejected workflow configuration" >&2
  exit 1
fi
grep 'workflow_configuration_rejected' "$tmp_dir/reject.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"
test -z "$(git -C "$workflow_repo" config --get core.hooksPath || true)"

touch "$fake_gh_state/deny-repository"
if workflow_check_or_configure configure "$workflow_repo" >"$tmp_dir/deny-repository.out" 2>"$tmp_dir/deny-repository.err"; then
  echo "expected repository configuration permission failure" >&2
  exit 1
fi
if ! grep 'workflow_configuration_permission_denied' "$tmp_dir/deny-repository.err" >/dev/null; then
  echo "missing repository permission failure code" >&2
  exit 1
fi
rm -f "$fake_gh_state/deny-repository"

touch "$fake_gh_state/deny-ruleset"
if workflow_check_or_configure configure "$workflow_repo" >"$tmp_dir/deny-ruleset.out" 2>"$tmp_dir/deny-ruleset.err"; then
  echo "expected ruleset configuration permission failure" >&2
  exit 1
fi
if ! grep 'workflow_configuration_permission_denied' "$tmp_dir/deny-ruleset.err" >/dev/null; then
  echo "missing ruleset permission failure code" >&2
  exit 1
fi
rm -f "$fake_gh_state/deny-ruleset"

workflow_check_or_configure configure "$workflow_repo"
test "$(git -C "$workflow_repo" config --get core.hooksPath)" = ".githooks"
git -C "$workflow_repo" ls-remote --exit-code --heads origin develop >/dev/null
test -f "$fake_gh_state/repository-configured"
test -f "$fake_gh_state/ruleset-configured"
writes_before="$(wc -l < "$fake_gh_state/writes.log" | tr -d ' ')"
workflow_check_or_configure configure "$workflow_repo"
writes_after="$(wc -l < "$fake_gh_state/writes.log" | tr -d ' ')"
test "$writes_after" = "$writes_before"
workflow_check_or_configure check "$workflow_repo"

touch "$fake_gh_state/deny-auth-status"
workflow_check_or_configure check "$workflow_repo"
grep '^api user$' "$fake_gh_state/calls.log" >/dev/null

touch "$fake_gh_state/deny-api-user"
if workflow_check_or_configure check "$workflow_repo" >"$tmp_dir/auth-failed.out" 2>"$tmp_dir/auth-failed.err"; then
  echo "expected unavailable GitHub authentication to fail" >&2
  exit 1
fi
grep 'workflow_github_auth_required' "$tmp_dir/auth-failed.err" >/dev/null
rm -f "$fake_gh_state/deny-auth-status" "$fake_gh_state/deny-api-user"

: > "$fake_gh_state/writes.log"
rm -f "$fake_gh_state/repository-configured" "$fake_gh_state/ruleset-configured"
if ! workflow_check_or_configure soft "$workflow_repo" >"$tmp_dir/soft-gate.out" 2>"$tmp_dir/soft-gate.err"; then
  cat "$tmp_dir/soft-gate.err" >&2
  echo "expected explicit soft gate to allow missing Auto-merge and ruleset" >&2
  exit 1
fi
grep '"protection_mode":"soft"' "$tmp_dir/soft-gate.out" >/dev/null
test ! -s "$fake_gh_state/writes.log"

touch "$fake_gh_state/deny-merge-commit"
if workflow_check_or_configure soft "$workflow_repo" >"$tmp_dir/soft-merge.out" 2>"$tmp_dir/soft-merge.err"; then
  echo "expected soft gate to require Merge commit" >&2
  exit 1
fi
grep 'workflow_soft_gate_required' "$tmp_dir/soft-merge.err" >/dev/null
rm -f "$fake_gh_state/deny-merge-commit"

touch "$fake_gh_state/wrong-default-branch"
if workflow_check_or_configure soft "$workflow_repo" >"$tmp_dir/soft-default.out" 2>"$tmp_dir/soft-default.err"; then
  echo "expected soft gate to require main as default branch" >&2
  exit 1
fi
grep 'workflow_soft_gate_required' "$tmp_dir/soft-default.err" >/dev/null
rm -f "$fake_gh_state/wrong-default-branch"

if workflow_check_or_configure check "$workflow_repo" >"$tmp_dir/no-auto-downgrade.out" 2>"$tmp_dir/no-auto-downgrade.err"; then
  echo "expected hard gate to remain the default" >&2
  exit 1
fi
grep 'workflow_configuration_required' "$tmp_dir/no-auto-downgrade.err" >/dev/null
touch "$fake_gh_state/repository-configured" "$fake_gh_state/ruleset-configured"

if [ ! -f "$repo_root/scripts/lib/release-common.sh" ]; then
  echo "missing release common functions" >&2
  exit 1
fi
if [ ! -x "$repo_root/scripts/release.sh" ]; then
  echo "missing release entrypoint" >&2
  exit 1
fi

mkdir -p "$workflow_repo/scripts/lib"
cp "$repo_root/scripts/release.sh" "$workflow_repo/scripts/release.sh"
cp "$repo_root/scripts/lib/release-common.sh" "$workflow_repo/scripts/lib/release-common.sh"
cp "$repo_root/scripts/lib/development-workflow.sh" "$workflow_repo/scripts/lib/development-workflow.sh"
cat > "$workflow_repo/scripts/build.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p install-resources/darwin-arm64 install-resources/darwin-amd64 install-resources/linux-arm64 install-resources/linux-amd64
for target in darwin-arm64 darwin-amd64 linux-arm64 linux-amd64; do
  printf 'binary:%s:%s\n' "$target" "$(git rev-parse --short HEAD)" > "install-resources/$target/agentic-cli"
  chmod 0755 "install-resources/$target/agentic-cli"
done
mkdir -p install-resources
printf 'fixture checksums\n' > install-resources/checksums.txt
printf '{"ok":true,"operation":"build"}\n'
EOF
for verification_script in \
  scripts/test-resources.sh \
  scripts/test-build.sh \
  scripts/test-install.sh \
  tests/e2e/ao-profile-flow.sh \
  tests/e2e/local-fake-flow.sh \
  tests/e2e/local-install-flow.sh \
  tests/e2e/problem-resolution-flow.sh; do
  mkdir -p "$workflow_repo/$(dirname "$verification_script")"
  verification_name="$(printf '%s' "$verification_script" | tr '/' '-')"
  cat > "$workflow_repo/$verification_script" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' '$verification_name' >> "\${FAKE_VERIFY_LOG:?}"
EOF
  chmod 0755 "$workflow_repo/$verification_script"
done
printf '.local/\n' > "$workflow_repo/.gitignore"
chmod 0755 "$workflow_repo/scripts/release.sh" "$workflow_repo/scripts/build.sh" "$workflow_repo/scripts/lib/release-common.sh" "$workflow_repo/scripts/lib/development-workflow.sh"
git -C "$workflow_repo" add scripts tests .githooks .gitignore
git -C "$workflow_repo" commit -m "add release fixture" >/dev/null

git -C "$workflow_repo" remote set-url origin git@github.com:tapstate/agentic-ops.git
git -C "$workflow_repo" config "url.$workflow_remote.insteadOf" git@github.com:tapstate/agentic-ops.git

if (cd "$workflow_repo" && scripts/release.sh prepare --version 0.3) >"$tmp_dir/invalid-version.out" 2>"$tmp_dir/invalid-version.err"; then
  echo "expected invalid release version to fail" >&2
  exit 1
fi
grep 'invalid_release_version' "$tmp_dir/invalid-version.err" >/dev/null

prepare_head="$(git -C "$workflow_repo" rev-parse HEAD)"
remote_develop_before="$(git -C "$workflow_repo" rev-parse refs/remotes/origin/develop)"
if ! (cd "$workflow_repo" && scripts/release.sh prepare --version v0.3) >"$tmp_dir/prepare.out" 2>"$tmp_dir/prepare.err"; then
  cat "$tmp_dir/prepare.err" >&2
  echo "expected release prepare to succeed" >&2
  exit 1
fi
grep '"operation":"release_prepare"' "$tmp_dir/prepare.out" >/dev/null
test "$(git -C "$workflow_repo" cat-file -t refs/tags/v0.3)" = "tag"
test "$(git -C "$workflow_repo" rev-list -n 1 v0.3)" = "$prepare_head"
test "$(git -C "$workflow_repo" rev-parse HEAD)" = "$prepare_head"
test "$(git -C "$workflow_repo" rev-parse refs/remotes/origin/develop)" = "$remote_develop_before"
test -f "$workflow_repo/install-resources/darwin-arm64/agentic-cli"
test -f "$workflow_repo/install-resources/darwin-amd64/agentic-cli"
test -f "$workflow_repo/install-resources/linux-arm64/agentic-cli"
test -f "$workflow_repo/install-resources/linux-amd64/agentic-cli"
test -f "$workflow_repo/install-resources/checksums.txt"

git -C "$workflow_repo" add install-resources
git -C "$workflow_repo" commit -m "commit generated assets" >/dev/null
(cd "$workflow_repo" && scripts/release.sh prepare --version v0.3) >"$tmp_dir/prepare-again.out" 2>"$tmp_dir/prepare-again.err"
grep '"operation":"release_prepare"' "$tmp_dir/prepare-again.out" >/dev/null
git -C "$workflow_repo" merge-base --is-ancestor refs/tags/v0.3 HEAD
test -z "$(git -C "$workflow_repo" ls-remote --tags origin refs/tags/v0.3)"

remote_develop_before_publish="$(git -C "$workflow_repo" rev-parse refs/remotes/origin/develop)"
: > "$fake_gh_state/writes.log"
if (cd "$workflow_repo" && scripts/release.sh publish --version v0.3 --confirm-release) >"$tmp_dir/dirty-publish.out" 2>"$tmp_dir/dirty-publish.err"; then
  echo "expected dirty publish to fail" >&2
  exit 1
fi
grep 'dirty_worktree' "$tmp_dir/dirty-publish.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"
test "$(git -C "$workflow_repo" rev-parse refs/remotes/origin/develop)" = "$remote_develop_before_publish"

git -C "$workflow_repo" add install-resources
git -C "$workflow_repo" commit -m "refresh generated assets" >/dev/null
publish_head="$(git -C "$workflow_repo" rev-parse HEAD)"

if (cd "$workflow_repo" && scripts/release.sh publish --version v0.4 --confirm-release) >"$tmp_dir/missing-tag.out" 2>"$tmp_dir/missing-tag.err"; then
  echo "expected missing publish tag to fail" >&2
  exit 1
fi
grep 'release_tag_missing' "$tmp_dir/missing-tag.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"

fixture_tree="$(git -C "$workflow_repo" write-tree)"
fixture_unrelated_commit="$(printf 'unrelated release baseline\n' | git -C "$workflow_repo" commit-tree "$fixture_tree")"
git -C "$workflow_repo" tag -a v0.5 "$fixture_unrelated_commit" -m "unrelated baseline"
if (cd "$workflow_repo" && scripts/release.sh publish --version v0.5 --confirm-release) >"$tmp_dir/unrelated-tag.out" 2>"$tmp_dir/unrelated-tag.err"; then
  echo "expected unrelated publish tag to fail" >&2
  exit 1
fi
grep 'release_tag_conflict' "$tmp_dir/unrelated-tag.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"

fake_bin="$tmp_dir/fake-bin"
mkdir -p "$fake_bin"
cat > "$fake_bin/go" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'go-test\n' >> "${FAKE_VERIFY_LOG:?}"
if [ "${FAKE_GO_FAIL:-false}" = "true" ]; then
  exit 1
fi
EOF
chmod 0755 "$fake_bin/go"
export PATH="$fake_bin:$PATH"
export FAKE_VERIFY_LOG="$tmp_dir/verification.log"
: > "$FAKE_VERIFY_LOG"
if scripts_output="$(cd "$workflow_repo" && FAKE_GO_FAIL=true scripts/release.sh publish --version v0.3 --confirm-release 2>&1)"; then
  echo "expected failed verification to stop publish" >&2
  exit 1
fi
printf '%s\n' "$scripts_output" | grep 'release_verification_failed' >/dev/null
test ! -s "$fake_gh_state/writes.log"
test "$(git -C "$workflow_repo" rev-parse refs/remotes/origin/develop)" = "$remote_develop_before_publish"

: > "$FAKE_VERIFY_LOG"
if (cd "$workflow_repo" && scripts/release.sh publish --version v0.3) >"$tmp_dir/unconfirmed.out" 2>"$tmp_dir/unconfirmed.err"; then
  echo "expected unconfirmed noninteractive publish to fail" >&2
  exit 1
fi
grep 'release_confirmation_required' "$tmp_dir/unconfirmed.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"

: > "$FAKE_VERIFY_LOG"
(cd "$workflow_repo" && scripts/release.sh publish --version v0.3 --confirm-release) >"$tmp_dir/publish.out" 2>"$tmp_dir/publish.err"
grep '"operation":"release_publish"' "$tmp_dir/publish.out" >/dev/null
test -f "$fake_gh_state/pr-created"
test -f "$fake_gh_state/pr-merged"
grep '^pr-create$' "$fake_gh_state/writes.log" >/dev/null
grep '^pr-merge$' "$fake_gh_state/writes.log" >/dev/null
remote_develop_after_publish="$(git --git-dir="$workflow_remote" rev-parse refs/heads/develop)"
remote_main_after_publish="$(git --git-dir="$workflow_remote" rev-parse refs/heads/main)"
test "$remote_develop_after_publish" = "$publish_head"
git --git-dir="$workflow_remote" merge-base --is-ancestor "$publish_head" "$remote_main_after_publish"
test "$(git --git-dir="$workflow_remote" rev-parse refs/tags/v0.3^{})" = "$(git -C "$workflow_repo" rev-list -n 1 v0.3)"
for expected_verification in \
  go-test \
  scripts-test-resources.sh \
  scripts-test-build.sh \
  scripts-test-install.sh \
  tests-e2e-ao-profile-flow.sh \
  tests-e2e-local-fake-flow.sh \
  tests-e2e-local-install-flow.sh \
  tests-e2e-problem-resolution-flow.sh; do
  grep "^$expected_verification$" "$FAKE_VERIFY_LOG" >/dev/null
done
audit_file="$workflow_repo/.local/release-runs/release-v0.3-$publish_head.json"
test -f "$audit_file"
grep '"status":"completed"' "$audit_file" >/dev/null
if grep -Ei 'token|secret|credential' "$audit_file" >/dev/null; then
  echo "release audit contains sensitive field names" >&2
  exit 1
fi

remote_tag_after_publish="$(git --git-dir="$workflow_remote" rev-parse refs/tags/v0.3^{})"
: > "$fake_gh_state/writes.log"
: > "$FAKE_VERIFY_LOG"
(cd "$workflow_repo" && scripts/release.sh publish --version v0.3 --confirm-release) >"$tmp_dir/publish-resume.out" 2>"$tmp_dir/publish-resume.err"
grep '"operation":"release_publish"' "$tmp_dir/publish-resume.out" >/dev/null
test ! -s "$fake_gh_state/writes.log"
test "$(git --git-dir="$workflow_remote" rev-parse refs/heads/main)" = "$remote_main_after_publish"
test "$(git --git-dir="$workflow_remote" rev-parse refs/tags/v0.3^{})" = "$remote_tag_after_publish"

rm -f "$fake_gh_state/repository-configured" "$fake_gh_state/ruleset-configured"
rm -f "$fake_gh_state/pr-created" "$fake_gh_state/pr-merged" "$fake_gh_state/pr-head" "$fake_gh_state/pr-fixed-head" "$fake_gh_state/pr-branch" "$fake_gh_state/merge-commit"
: > "$fake_gh_state/writes.log"
printf 'soft release\n' > "$workflow_repo/soft-release.txt"
git -C "$workflow_repo" add soft-release.txt
git -C "$workflow_repo" commit -m "soft release change" >/dev/null

if ! (cd "$workflow_repo" && scripts/release.sh prepare --version v0.6 --allow-soft-gate) >"$tmp_dir/soft-prepare.out" 2>"$tmp_dir/soft-prepare.err"; then
  cat "$tmp_dir/soft-prepare.err" >&2
  echo "expected explicit soft release prepare to succeed" >&2
  exit 1
fi
grep '"protection_mode":"soft"' "$tmp_dir/soft-prepare.out" >/dev/null
git -C "$workflow_repo" add install-resources
git -C "$workflow_repo" commit -m "commit soft release assets" >/dev/null
soft_publish_head="$(git -C "$workflow_repo" rev-parse HEAD)"

: > "$FAKE_VERIFY_LOG"
set +e
(cd "$workflow_repo" && scripts/release.sh publish --version v0.6 --allow-soft-gate --confirm-release) >"$tmp_dir/soft-publish-wait.out" 2>"$tmp_dir/soft-publish-wait.err"
soft_wait_status=$?
set -e
if [ "$soft_wait_status" != "2" ]; then
  cat "$tmp_dir/soft-publish-wait.err" >&2
  echo "expected soft publish to pause with status 2, got $soft_wait_status" >&2
  exit 1
fi
grep '"status":"waiting_for_manual_merge"' "$tmp_dir/soft-publish-wait.out" >/dev/null
grep '"protection_mode":"soft"' "$tmp_dir/soft-publish-wait.out" >/dev/null
grep '"agentic_next_action":"merge_pr_with_merge_commit_then_rerun"' "$tmp_dir/soft-publish-wait.out" >/dev/null
test "$(cat "$fake_gh_state/pr-branch")" = "release/v0.6"
test "$(git --git-dir="$workflow_remote" rev-parse refs/heads/release/v0.6)" = "$soft_publish_head"
test -z "$(git --git-dir="$workflow_remote" show-ref --tags v0.6 || true)"
if grep '^pr-merge$' "$fake_gh_state/writes.log" >/dev/null; then
  echo "soft publish must not invoke automatic PR merge" >&2
  exit 1
fi
soft_wait_audit="$workflow_repo/.local/release-runs/release-v0.6-$soft_publish_head.json"
grep '"status":"waiting_for_manual_merge"' "$soft_wait_audit" >/dev/null
grep '"protection_mode":"soft"' "$soft_wait_audit" >/dev/null

fake_merge_pr_manually
: > "$fake_gh_state/writes.log"
(cd "$workflow_repo" && scripts/release.sh publish --version v0.6 --allow-soft-gate --confirm-release) >"$tmp_dir/soft-publish-complete.out" 2>"$tmp_dir/soft-publish-complete.err"
grep '"operation":"release_publish"' "$tmp_dir/soft-publish-complete.out" >/dev/null
grep '"protection_mode":"soft"' "$tmp_dir/soft-publish-complete.out" >/dev/null
test "$(git --git-dir="$workflow_remote" rev-parse refs/tags/v0.6^{})" = "$(git -C "$workflow_repo" rev-list -n 1 v0.6)"
test "$(grep -c '^go-test$' "$FAKE_VERIFY_LOG")" = "2"
grep '"status":"completed"' "$soft_wait_audit" >/dev/null
grep '"protection_mode":"soft"' "$soft_wait_audit" >/dev/null
test "$(git --git-dir="$workflow_remote" rev-parse refs/heads/release/v0.6)" = "$soft_publish_head"
touch "$fake_gh_state/repository-configured" "$fake_gh_state/ruleset-configured"

if [ ! -x "$repo_root/scripts/hotfix.sh" ]; then
  echo "missing hotfix entrypoint" >&2
  exit 1
fi

hotfix_remote="$tmp_dir/hotfix-remote.git"
hotfix_repo="$tmp_dir/hotfix-repo"
git init --bare "$hotfix_remote" >/dev/null
git clone "$hotfix_remote" "$hotfix_repo" >/dev/null 2>&1
git -C "$hotfix_repo" config user.email agentic-ops-test@example.test
git -C "$hotfix_repo" config user.name "Harsen Lin"
printf '# hotfix fixture\n' > "$hotfix_repo/README.md"
printf '.local/\ninstall-resources/\n' > "$hotfix_repo/.gitignore"
mkdir -p "$hotfix_repo/scripts/lib" "$hotfix_repo/.githooks"
cp "$repo_root/scripts/hotfix.sh" "$hotfix_repo/scripts/hotfix.sh"
cp "$repo_root/scripts/lib/release-common.sh" "$hotfix_repo/scripts/lib/release-common.sh"
cp "$repo_root/scripts/lib/development-workflow.sh" "$hotfix_repo/scripts/lib/development-workflow.sh"
cp "$repo_root/.githooks/pre-commit" "$hotfix_repo/.githooks/pre-commit"
cp "$repo_root/.githooks/pre-push" "$hotfix_repo/.githooks/pre-push"
cat > "$hotfix_repo/scripts/build.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p install-resources
printf 'hotfix assets:%s\n' "$(git rev-parse --short HEAD)" > install-resources/checksums.txt
printf '{"ok":true,"operation":"build"}\n'
EOF
chmod 0755 "$hotfix_repo/scripts/hotfix.sh" "$hotfix_repo/scripts/build.sh" "$hotfix_repo/scripts/lib/release-common.sh" "$hotfix_repo/scripts/lib/development-workflow.sh"
git -C "$hotfix_repo" add README.md .gitignore scripts .githooks
git -C "$hotfix_repo" commit -m "initial hotfix fixture" >/dev/null
git -C "$hotfix_repo" branch -M main
git -C "$hotfix_repo" tag -a v0.3 -m "v0.3 baseline"
git -C "$hotfix_repo" push -u origin main >/dev/null
git -C "$hotfix_repo" push origin refs/tags/v0.3 >/dev/null
git -C "$hotfix_repo" switch -c develop >/dev/null
git -C "$hotfix_repo" push -u origin develop >/dev/null
git -C "$hotfix_repo" switch main >/dev/null
git -C "$hotfix_repo" remote set-url origin git@github.com:tapstate/agentic-ops.git
git -C "$hotfix_repo" config "url.$hotfix_remote.insteadOf" git@github.com:tapstate/agentic-ops.git
git -C "$hotfix_repo" config core.hooksPath .githooks

if (cd "$hotfix_repo" && scripts/hotfix.sh create --jira-id ao-123 --user harsen) >"$tmp_dir/invalid-jira.out" 2>"$tmp_dir/invalid-jira.err"; then
  echo "expected lowercase Jira ID to fail" >&2
  exit 1
fi
grep 'invalid_jira_id' "$tmp_dir/invalid-jira.err" >/dev/null

(cd "$hotfix_repo" && scripts/hotfix.sh create --jira-id AO-123 --user harsen) >"$tmp_dir/hotfix-create.out" 2>"$tmp_dir/hotfix-create.err"
grep '"operation":"hotfix_create"' "$tmp_dir/hotfix-create.out" >/dev/null
test "$(git -C "$hotfix_repo" branch --show-current)" = "harsen/AO-123/fix-main"
test "$(git -C "$hotfix_repo" rev-parse HEAD)" = "$(git -C "$hotfix_repo" rev-parse refs/remotes/origin/main)"

if (cd "$hotfix_repo" && scripts/hotfix.sh create --jira-id AO-123 --user harsen) >"$tmp_dir/existing-hotfix.out" 2>"$tmp_dir/existing-hotfix.err"; then
  echo "expected duplicate hotfix branch to fail" >&2
  exit 1
fi
grep 'hotfix_branch_exists' "$tmp_dir/existing-hotfix.err" >/dev/null

git -C "$hotfix_repo" switch develop >/dev/null
if (cd "$hotfix_repo" && scripts/hotfix.sh prepare) >"$tmp_dir/invalid-hotfix-branch.out" 2>"$tmp_dir/invalid-hotfix-branch.err"; then
  echo "expected non-hotfix prepare to fail" >&2
  exit 1
fi
grep 'invalid_hotfix_branch' "$tmp_dir/invalid-hotfix-branch.err" >/dev/null

git -C "$hotfix_repo" switch harsen/AO-123/fix-main >/dev/null
hotfix_tag_count_before="$(git -C "$hotfix_repo" tag --list | wc -l | tr -d ' ')"
(cd "$hotfix_repo" && scripts/hotfix.sh prepare) >"$tmp_dir/hotfix-prepare.out" 2>"$tmp_dir/hotfix-prepare.err"
grep '"operation":"hotfix_prepare"' "$tmp_dir/hotfix-prepare.out" >/dev/null
grep '"version":"v0.3"' "$tmp_dir/hotfix-prepare.out" >/dev/null
test -f "$hotfix_repo/install-resources/checksums.txt"
test "$(git -C "$hotfix_repo" tag --list | wc -l | tr -d ' ')" = "$hotfix_tag_count_before"
test -z "$(git -C "$hotfix_repo" ls-remote --heads origin refs/heads/harsen/AO-123/fix-main)"

for verification_script in \
  scripts/test-resources.sh \
  scripts/test-build.sh \
  scripts/test-install.sh \
  tests/e2e/ao-profile-flow.sh \
  tests/e2e/local-fake-flow.sh \
  tests/e2e/local-install-flow.sh \
  tests/e2e/problem-resolution-flow.sh; do
  mkdir -p "$hotfix_repo/$(dirname "$verification_script")"
  verification_name="$(printf '%s' "$verification_script" | tr '/' '-')"
  cat > "$hotfix_repo/$verification_script" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' '$verification_name' >> "\${FAKE_VERIFY_LOG:?}"
EOF
  chmod 0755 "$hotfix_repo/$verification_script"
done
printf 'fix AO-123\n' > "$hotfix_repo/fix.txt"
git -C "$hotfix_repo" add scripts tests fix.txt
git -C "$hotfix_repo" commit -m "fix AO-123" >/dev/null
hotfix_publish_head="$(git -C "$hotfix_repo" rev-parse HEAD)"
hotfix_remote_tags_before="$(git --git-dir="$hotfix_remote" show-ref --tags)"
export FAKE_GH_REMOTE="$hotfix_remote"
rm -f "$fake_gh_state/pr-created" "$fake_gh_state/pr-merged" "$fake_gh_state/pr-head" "$fake_gh_state/pr-fixed-head" "$fake_gh_state/pr-branch" "$fake_gh_state/merge-commit"
: > "$fake_gh_state/writes.log"
: > "$FAKE_VERIFY_LOG"

if hotfix_failed_output="$(cd "$hotfix_repo" && FAKE_GO_FAIL=true scripts/hotfix.sh publish --confirm-release 2>&1)"; then
  echo "expected failed Hotfix verification to stop publish" >&2
  exit 1
fi
printf '%s\n' "$hotfix_failed_output" | grep 'release_verification_failed' >/dev/null
test ! -s "$fake_gh_state/writes.log"
test -z "$(git -C "$hotfix_repo" ls-remote --heads origin refs/heads/harsen/AO-123/fix-main)"

: > "$FAKE_VERIFY_LOG"
if (cd "$hotfix_repo" && scripts/hotfix.sh publish) >"$tmp_dir/hotfix-unconfirmed.out" 2>"$tmp_dir/hotfix-unconfirmed.err"; then
  echo "expected unconfirmed Hotfix publish to fail" >&2
  exit 1
fi
grep 'release_confirmation_required' "$tmp_dir/hotfix-unconfirmed.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"

touch "$fake_gh_state/deny-pr-create"
if (cd "$hotfix_repo" && scripts/hotfix.sh publish --confirm-release) >"$tmp_dir/hotfix-pr-denied.out" 2>"$tmp_dir/hotfix-pr-denied.err"; then
  echo "expected denied Hotfix PR creation to fail" >&2
  exit 1
fi
grep 'release_pr_create_failed' "$tmp_dir/hotfix-pr-denied.err" >/dev/null
test "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/harsen/AO-123/fix-main)" = "$hotfix_publish_head"
test ! -f "$fake_gh_state/pr-created"
rm -f "$fake_gh_state/deny-pr-create"

: > "$fake_gh_state/writes.log"
: > "$FAKE_VERIFY_LOG"
if ! (cd "$hotfix_repo" && scripts/hotfix.sh publish --confirm-release) >"$tmp_dir/hotfix-publish.out" 2>"$tmp_dir/hotfix-publish.err"; then
  cat "$tmp_dir/hotfix-publish.err" >&2
  echo "expected Hotfix publish to succeed" >&2
  exit 1
fi
grep '"operation":"hotfix_publish"' "$tmp_dir/hotfix-publish.out" >/dev/null
grep '"jira_id":"AO-123"' "$tmp_dir/hotfix-publish.out" >/dev/null
grep '"version":"v0.3"' "$tmp_dir/hotfix-publish.out" >/dev/null
grep '"agentic_next_action":"sync_hotfix_to_develop"' "$tmp_dir/hotfix-publish.out" >/dev/null
test -f "$fake_gh_state/pr-created"
test -f "$fake_gh_state/pr-merged"
test "$(cat "$fake_gh_state/pr-branch")" = "harsen/AO-123/fix-main"
hotfix_remote_main="$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)"
git --git-dir="$hotfix_remote" merge-base --is-ancestor "$hotfix_publish_head" "$hotfix_remote_main"
test "$(git --git-dir="$hotfix_remote" show-ref --tags)" = "$hotfix_remote_tags_before"
if grep 'refs/tags/' "$fake_gh_state/calls.log" >/dev/null; then
  echo "Hotfix publish attempted a tag operation" >&2
  exit 1
fi
hotfix_audit="$hotfix_repo/.local/release-runs/hotfix-AO-123-$hotfix_publish_head.json"
test -f "$hotfix_audit"
grep '"status":"completed"' "$hotfix_audit" >/dev/null
grep '"next_action":"sync_hotfix_to_develop"' "$hotfix_audit" >/dev/null

: > "$fake_gh_state/writes.log"
: > "$FAKE_VERIFY_LOG"
(cd "$hotfix_repo" && scripts/hotfix.sh publish --confirm-release) >"$tmp_dir/hotfix-resume.out" 2>"$tmp_dir/hotfix-resume.err"
grep '"operation":"hotfix_publish"' "$tmp_dir/hotfix-resume.out" >/dev/null
test ! -s "$fake_gh_state/writes.log"
test "$(git --git-dir="$hotfix_remote" show-ref --tags)" = "$hotfix_remote_tags_before"

rm -f "$fake_gh_state/repository-configured" "$fake_gh_state/ruleset-configured"
rm -f "$fake_gh_state/pr-created" "$fake_gh_state/pr-merged" "$fake_gh_state/pr-closed" "$fake_gh_state/pr-head" "$fake_gh_state/pr-fixed-head" "$fake_gh_state/pr-branch" "$fake_gh_state/merge-commit"
: > "$fake_gh_state/writes.log"
(cd "$hotfix_repo" && scripts/hotfix.sh create --jira-id AO-124 --user harsen) >"$tmp_dir/soft-hotfix-create.out" 2>"$tmp_dir/soft-hotfix-create.err"
printf 'fix AO-124\n' > "$hotfix_repo/fix-124.txt"
git -C "$hotfix_repo" add fix-124.txt
git -C "$hotfix_repo" commit -m "fix AO-124" >/dev/null
soft_hotfix_head="$(git -C "$hotfix_repo" rev-parse HEAD)"

if ! (cd "$hotfix_repo" && scripts/hotfix.sh prepare --allow-soft-gate) >"$tmp_dir/soft-hotfix-prepare.out" 2>"$tmp_dir/soft-hotfix-prepare.err"; then
  cat "$tmp_dir/soft-hotfix-prepare.err" >&2
  echo "expected explicit soft Hotfix prepare to succeed" >&2
  exit 1
fi
grep '"protection_mode":"soft"' "$tmp_dir/soft-hotfix-prepare.out" >/dev/null

: > "$FAKE_VERIFY_LOG"
set +e
(cd "$hotfix_repo" && scripts/hotfix.sh publish --allow-soft-gate --confirm-release) >"$tmp_dir/soft-hotfix-wait.out" 2>"$tmp_dir/soft-hotfix-wait.err"
soft_hotfix_wait_status=$?
set -e
if [ "$soft_hotfix_wait_status" != "2" ]; then
  cat "$tmp_dir/soft-hotfix-wait.err" >&2
  echo "expected soft Hotfix publish to pause with status 2, got $soft_hotfix_wait_status" >&2
  exit 1
fi
grep '"status":"waiting_for_manual_merge"' "$tmp_dir/soft-hotfix-wait.out" >/dev/null
grep '"protection_mode":"soft"' "$tmp_dir/soft-hotfix-wait.out" >/dev/null
test "$(cat "$fake_gh_state/pr-branch")" = "harsen/AO-124/fix-main"
test "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/harsen/AO-124/fix-main)" = "$soft_hotfix_head"
if grep '^pr-merge$' "$fake_gh_state/writes.log" >/dev/null; then
  echo "soft Hotfix must not invoke automatic PR merge" >&2
  exit 1
fi
soft_hotfix_audit="$hotfix_repo/.local/release-runs/hotfix-AO-124-$soft_hotfix_head.json"
grep '"status":"waiting_for_manual_merge"' "$soft_hotfix_audit" >/dev/null
grep '"jira_id":"AO-124"' "$soft_hotfix_audit" >/dev/null

fake_merge_pr_manually
: > "$fake_gh_state/writes.log"
(cd "$hotfix_repo" && scripts/hotfix.sh publish --allow-soft-gate --confirm-release) >"$tmp_dir/soft-hotfix-complete.out" 2>"$tmp_dir/soft-hotfix-complete.err"
grep '"operation":"hotfix_publish"' "$tmp_dir/soft-hotfix-complete.out" >/dev/null
grep '"protection_mode":"soft"' "$tmp_dir/soft-hotfix-complete.out" >/dev/null
test "$(grep -c '^go-test$' "$FAKE_VERIFY_LOG")" = "2"
grep '"status":"completed"' "$soft_hotfix_audit" >/dev/null
grep '"protection_mode":"soft"' "$soft_hotfix_audit" >/dev/null
test "$(git --git-dir="$hotfix_remote" show-ref --tags)" = "$hotfix_remote_tags_before"
touch "$fake_gh_state/repository-configured" "$fake_gh_state/ruleset-configured"

# shellcheck source=scripts/lib/release-common.sh
. "$repo_root/scripts/lib/release-common.sh"

RELEASE_PR_URL="https://github.com/tapstate/agentic-ops/pull/7"
RELEASE_VERIFIED_AT="2026-08-01T00:00:00Z"
rm -f "$fake_gh_state/pr-merged" "$fake_gh_state/pr-closed"
printf '%s\n' "$zero_sha" > "$fake_gh_state/pr-head"
printf '%s\n' "$soft_hotfix_head" > "$fake_gh_state/pr-fixed-head"
if release_wait_for_manual_merge "$hotfix_repo" "tapstate/agentic-ops" hotfix_publish v0.3 "$soft_hotfix_head" "harsen/AO-124/fix-main" AO-124 >"$tmp_dir/head-drift.out" 2>"$tmp_dir/head-drift.err"; then
  echo "expected soft gate to reject PR HEAD drift" >&2
  exit 1
fi
grep 'release_pr_head_drift' "$tmp_dir/head-drift.err" >/dev/null

printf '%s\n' "$soft_hotfix_head" > "$fake_gh_state/pr-head"
printf '%s\n' "$soft_hotfix_head" > "$fake_gh_state/pr-fixed-head"
touch "$fake_gh_state/pr-closed"
if release_wait_for_manual_merge "$hotfix_repo" "tapstate/agentic-ops" hotfix_publish v0.3 "$soft_hotfix_head" "harsen/AO-124/fix-main" AO-124 >"$tmp_dir/pr-closed.out" 2>"$tmp_dir/pr-closed.err"; then
  echo "expected soft gate to reject a closed unmerged PR" >&2
  exit 1
fi
grep 'release_pr_closed' "$tmp_dir/pr-closed.err" >/dev/null
rm -f "$fake_gh_state/pr-closed"

printf '%s\n' "$soft_hotfix_head" > "$fake_gh_state/pr-head"
printf '%s\n' "$zero_sha" > "$fake_gh_state/pr-fixed-head"
set +e
release_wait_for_manual_merge "$hotfix_repo" "tapstate/agentic-ops" hotfix_publish v0.3 "$soft_hotfix_head" "harsen/AO-124/fix-main" AO-124 >"$tmp_dir/fixed-head-drift.out" 2>"$tmp_dir/fixed-head-drift.err"
fixed_head_status=$?
set -e
if [ "$fixed_head_status" != "1" ]; then
  echo "expected PR evidence to preserve the original Hotfix HEAD" >&2
  exit 1
fi
grep 'release_pr_head_drift' "$tmp_dir/fixed-head-drift.err" >/dev/null
printf '%s\n' "$soft_hotfix_head" > "$fake_gh_state/pr-fixed-head"

git -C "$hotfix_repo" fetch origin main >/dev/null 2>&1
soft_merge_commit="$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)"
wrong_merge_head="$(git -C "$hotfix_repo" rev-parse "$soft_merge_commit^1")"
if release_verify_merge_commit "$hotfix_repo" "$wrong_merge_head" "$soft_merge_commit" >"$tmp_dir/merge-method.out" 2>"$tmp_dir/merge-method.err"; then
  echo "expected soft gate to reject a merge commit with a different second parent" >&2
  exit 1
fi
grep 'release_merge_method_invalid' "$tmp_dir/merge-method.err" >/dev/null

synthetic_tree="$(git -C "$hotfix_repo" write-tree)"
synthetic_merge="$(printf 'synthetic merge outside main\n' | git -C "$hotfix_repo" commit-tree "$synthetic_tree" -p "$wrong_merge_head" -p "$soft_hotfix_head")"
if release_verify_merge_commit "$hotfix_repo" "$soft_hotfix_head" "$synthetic_merge" >"$tmp_dir/merge-history.out" 2>"$tmp_dir/merge-history.err"; then
  echo "expected soft gate to require the Merge commit in origin/main history" >&2
  exit 1
fi
grep 'release_merge_commit_not_in_main' "$tmp_dir/merge-history.err" >/dev/null

touch "$fake_gh_state/deny-pr-list"
: > "$fake_gh_state/writes.log"
if release_find_or_create_pr "tapstate/agentic-ops" "harsen/AO-124/fix-main" main "$soft_hotfix_head" v0.3 hotfix AO-124 soft >"$tmp_dir/pr-list-failed.out" 2>"$tmp_dir/pr-list-failed.err"; then
  echo "expected PR list failure to stop before PR creation" >&2
  exit 1
fi
grep 'release_pr_list_failed' "$tmp_dir/pr-list-failed.err" >/dev/null
test ! -s "$fake_gh_state/writes.log"
rm -f "$fake_gh_state/deny-pr-list"

rm -f "$fake_gh_state/pr-merged"
RELEASE_PR_URL="https://github.com/tapstate/agentic-ops/pull/7"
sleep() { :; }
if release_wait_for_merge "tapstate/agentic-ops" >"$tmp_dir/merge-timeout.out" 2>"$tmp_dir/merge-timeout.err"; then
  echo "expected merge wait timeout" >&2
  exit 1
fi
unset -f sleep
grep 'release_merge_timeout' "$tmp_dir/merge-timeout.err" >/dev/null
touch "$fake_gh_state/pr-merged"

no_tag_remote="$tmp_dir/no-tag-remote.git"
no_tag_repo="$tmp_dir/no-tag-repo"
git init --bare "$no_tag_remote" >/dev/null
git clone "$no_tag_remote" "$no_tag_repo" >/dev/null 2>&1
git -C "$no_tag_repo" config user.email agentic-ops-test@example.test
git -C "$no_tag_repo" config user.name "AgenticOps Test"
printf 'no tag\n' > "$no_tag_repo/README.md"
git -C "$no_tag_repo" add README.md
git -C "$no_tag_repo" commit -m "no tag fixture" >/dev/null
git -C "$no_tag_repo" branch -M main
git -C "$no_tag_repo" push -u origin main >/dev/null
git -C "$no_tag_repo" switch -c tester/AO-999/fix-main >/dev/null
if release_find_iteration_tag "$no_tag_repo" >"$tmp_dir/no-tag.out" 2>"$tmp_dir/no-tag.err"; then
  echo "expected missing iteration tag to fail" >&2
  exit 1
fi
grep 'iteration_tag_missing' "$tmp_dir/no-tag.err" >/dev/null

sync_remote="$tmp_dir/sync-remote.git"
sync_seed="$tmp_dir/sync-seed"
sync_local="$tmp_dir/sync-local"
sync_branch="tester/AO-100/fix-main"
git init --bare "$sync_remote" >/dev/null
git clone "$sync_remote" "$sync_seed" >/dev/null 2>&1
git -C "$sync_seed" config user.email agentic-ops-test@example.test
git -C "$sync_seed" config user.name "AgenticOps Test"
printf 'initial\n' > "$sync_seed/sync.txt"
git -C "$sync_seed" add sync.txt
git -C "$sync_seed" commit -m "initial sync fixture" >/dev/null
git -C "$sync_seed" branch -M "$sync_branch"
git -C "$sync_seed" push -u origin "$sync_branch" >/dev/null
git clone --branch "$sync_branch" "$sync_remote" "$sync_local" >/dev/null 2>&1
git -C "$sync_local" config user.email agentic-ops-test@example.test
git -C "$sync_local" config user.name "AgenticOps Test"
printf 'remote ahead\n' >> "$sync_seed/sync.txt"
git -C "$sync_seed" add sync.txt
git -C "$sync_seed" commit -m "remote ahead" >/dev/null
git -C "$sync_seed" push origin "$sync_branch" >/dev/null
if release_require_synced_hotfix_branch "$sync_local" "$sync_branch" >"$tmp_dir/hotfix-behind.out" 2>"$tmp_dir/hotfix-behind.err"; then
  echo "expected behind Hotfix branch to fail" >&2
  exit 1
fi
grep 'hotfix_branch_behind_remote' "$tmp_dir/hotfix-behind.err" >/dev/null
printf 'local diverged\n' > "$sync_local/local.txt"
git -C "$sync_local" add local.txt
git -C "$sync_local" commit -m "local diverged" >/dev/null
if release_require_synced_hotfix_branch "$sync_local" "$sync_branch" >"$tmp_dir/hotfix-diverged.out" 2>"$tmp_dir/hotfix-diverged.err"; then
  echo "expected diverged Hotfix branch to fail" >&2
  exit 1
fi
grep 'hotfix_branch_diverged' "$tmp_dir/hotfix-diverged.err" >/dev/null

printf '{"ok":true,"operation":"test_release_workflow","cases":52}\n'
