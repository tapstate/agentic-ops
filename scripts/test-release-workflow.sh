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
  exit 0
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
  *".default_branch"*) printf 'main\n' ;;
  *".allow_auto_merge"*)
    if [ -f "$FAKE_GH_STATE_DIR/repository-configured" ]; then printf 'true\n'; else printf 'false\n'; fi
    ;;
  *".allow_merge_commit"*) printf 'true\n' ;;
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
chmod 0755 "$workflow_repo/scripts/release.sh" "$workflow_repo/scripts/build.sh" "$workflow_repo/scripts/lib/release-common.sh" "$workflow_repo/scripts/lib/development-workflow.sh"
git -C "$workflow_repo" add scripts .githooks
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

printf '{"ok":true,"operation":"test_release_workflow","cases":10}\n'
