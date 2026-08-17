#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT

source_repo="$test_root/source"
test_home="$test_root/home"
verify_script="$source_repo/developer/bootstrap/install-verify-branch.sh"
official_repo_url="git@github.com:tapstate/agentic-ops.git"

mkdir -p "$test_home" "$source_repo"

tar \
  --exclude .git \
  --exclude .agentic-ops \
  --exclude .superpowers \
  --exclude .venv \
  --exclude .local \
  --exclude dist \
  -cf - -C "$repo_root" . | tar -C "$source_repo" -xf -

git -C "$source_repo" init -b main >/dev/null
git -C "$source_repo" config user.email agentic-ops-test@example.test
git -C "$source_repo" config user.name "AgenticOps Test"
git -C "$source_repo" add .
git -C "$source_repo" commit -m "test source" >/dev/null
git -C "$source_repo" checkout -B develop >/dev/null

test_python="${AGENTIC_OPS_TEST_PYTHON:-$repo_root/.venv/bin/python}"
if [ ! -x "$test_python" ]; then
  test_python="$(command -v python3)"
fi

fake_uv="$test_root/fake-uv"
cat > "$fake_uv" <<'UV'
#!/usr/bin/env bash
set -euo pipefail

project_dir=""
if [ "$1" = "sync" ]; then
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --project)
        shift
        project_dir="$1"
        ;;
    esac
    shift || true
  done

  if [ -z "$project_dir" ]; then
    exit 1
  fi

  mkdir -p "$project_dir/.venv/bin"
  cat > "$project_dir/.venv/bin/python" <<PY
#!/usr/bin/env bash
set -euo pipefail
exec "${AGENTIC_OPS_TEST_REAL_PYTHON}" "\$@"
PY
  chmod 0755 "$project_dir/.venv/bin/python"
  exit 0
fi

exec "${AGENTIC_OPS_TEST_REAL_PYTHON}" "${UV_BIN:-$(command -v uv)}" "$@"
UV
chmod 0755 "$fake_uv"

transport_git_dir="$test_root/transport-git"
real_git="$(command -v git)"
mkdir -p "$transport_git_dir"
cat > "$transport_git_dir/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
subcommand=""
skip_next=0
for argument in "$@"; do
  if [ "$skip_next" = "1" ]; then
    skip_next=0
    continue
  fi
  case "$argument" in
    -C|-c|--git-dir|--work-tree) skip_next=1 ;;
    --git-dir=*|--work-tree=*|-c*) ;;
    -*) ;;
    *) subcommand="$argument"; break ;;
  esac
done
case "$subcommand" in
  clone|fetch)
    exec "${AO_TEST_REAL_GIT:?}" \
      -c "url.${AO_TEST_FIXTURE_REPOSITORY:?}.insteadOf=${AO_TEST_OFFICIAL_REPOSITORY:?}" \
      "$@"
    ;;
  ls-remote)
    if [ "${*#*--get-url}" != "$*" ]; then
      exec "${AO_TEST_REAL_GIT:?}" "$@"
    fi
    exec "${AO_TEST_REAL_GIT:?}" \
      -c "url.${AO_TEST_FIXTURE_REPOSITORY:?}.insteadOf=${AO_TEST_OFFICIAL_REPOSITORY:?}" \
      "$@"
    ;;
  *) exec "${AO_TEST_REAL_GIT:?}" "$@" ;;
esac
EOF
chmod 0755 "$transport_git_dir/git"

run_local_verify() {
  local label="$1"
  local expect_success="$2"
  shift 2

  local output="$test_root/${label}.out"
  local error="$test_root/${label}.err"

  if HOME="$test_home" \
    AGENTIC_OPS_UV="$fake_uv" \
    AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
    "$@" >"$output" 2>"$error"; then
    if [ "$expect_success" != "pass" ]; then
      echo "预期失败但实际成功：$label" >&2
      exit 1
    fi
  else
    if [ "$expect_success" != "fail" ]; then
      echo "预期成功但实际失败：$label" >&2
      printf 'stdout:\n'; cat "$output" >&2
      printf 'stderr:\n'; cat "$error" >&2
      exit 1
    fi
  fi
}

# --- 本地模式（--source-worktree，仅验证安装流程，不可运行） ---

run_local_verify test_success pass \
  bash "$verify_script" \
    --source-worktree "$source_repo" \
    --source-branch develop \
    --install-home "$test_root/verify-success" \
    --log "$test_root/verify-success.log" \
    --json \
    --keep

grep -q '"ok":true' "$test_root/test_success.out"
grep -q '"operation":"bootstrap_verify"' "$test_root/test_success.out"
test -f "$test_root/verify-success/.agentic-ops/verification-only"
test -f "$test_root/verify-success.log"

run_local_verify test_invalid_branch fail \
  bash "$verify_script" \
    --source-worktree "$source_repo" \
    --source-branch no-such-branch \
    --install-home "$test_root/verify-fail" \
    --log "$test_root/verify-fail.log" \
    --json

grep -q '"code":"source_branch_not_found"' "$test_root/test_invalid_branch.out"

run_local_verify test_invalid_source_dir fail \
  bash "$verify_script" \
    --source-worktree "$test_root/not-a-repo" \
    --source-branch develop \
    --install-home "$test_root/not-a-repo-result" \
    --json

grep -q '"code":"source_worktree_not_found\|source_worktree_not_git"' "$test_root/test_invalid_source_dir.out"

run_local_verify test_forbid_home fail \
  bash "$verify_script" \
    --source-worktree "$source_repo" \
    --source-branch develop \
    --install-home "$test_home/.agentic-ops" \
    --json

grep -q '"code":"verification_home_forbidden"' "$test_root/test_forbid_home.out"

# --- 远程模式（默认，可运行的验证安装） ---

remote_home="$test_root/verify-remote"
remote_log="$test_root/verify-remote.log"

if HOME="$test_home" \
  AGENTIC_OPS_UV="$fake_uv" \
  AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
  AO_TEST_REAL_GIT="$real_git" \
  AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
  AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
  PATH="$transport_git_dir:$PATH" \
  bash "$verify_script" \
    --source-branch develop \
    --install-home "$remote_home" \
    --log "$remote_log" \
    --json \
    --keep \
    >"$test_root/remote.out" 2>"$test_root/remote.err"; then
  :
else
  echo "远程验证安装应成功" >&2
  printf 'stdout:\n'; cat "$test_root/remote.out" >&2
  printf 'stderr:\n'; cat "$test_root/remote.err" >&2
  exit 1
fi

grep -q '"ok":true' "$test_root/remote.out"
test -f "$remote_home/.agentic-ops/verification-only"
grep -q '"source": "remote"' "$remote_home/.agentic-ops/verification-only"
test -f "$remote_log"
test -x "$remote_home/bin/ao-work"
test "$(git -C "$remote_home" config --get remote.origin.url)" = "$official_repo_url"
test ! -e "$remote_home/maintainer"

# 可运行验证：安装后的 ao-work 能通过安装身份校验并输出能力目录。
if env -i \
  PATH="$(dirname "$real_git"):$PATH" \
  HOME="$test_home" \
  "$remote_home/bin/ao-work" capability list \
    >"$test_root/remote-capability.out" 2>"$test_root/remote-capability.err"; then
  :
else
  echo "远程验证安装的 ao-work 应可运行 capability list" >&2
  cat "$test_root/remote-capability.out" >&2
  cat "$test_root/remote-capability.err" >&2
  exit 1
fi
grep -q '"operation":"capability_list"' "$test_root/remote-capability.out"

run_local_verify test_remote_invalid_branch fail \
  env HOME="$test_home" \
    AGENTIC_OPS_UV="$fake_uv" \
    AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
    AO_TEST_REAL_GIT="$real_git" \
    AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
    AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
    PATH="$transport_git_dir:$PATH" \
    bash "$verify_script" \
      --source-branch no-such-branch \
      --install-home "$test_root/verify-remote-fail" \
      --json

grep -q '"code":"source_branch_not_found"' "$test_root/test_remote_invalid_branch.out"

printf '{"ok":true,"operation":"install_verify_boundary"}\n'
