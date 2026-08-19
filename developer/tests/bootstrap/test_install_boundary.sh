#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT
if [ "${AGENTIC_OPS_KEEP_TEST_ROOT:-false}" = "true" ]; then
  trap 'printf "保留测试目录：%s\\n" "$test_root" >&2' EXIT
fi

source_repo="$test_root/source"
test_home="$test_root/home"
install_root="$test_root/installed-agentic-ops"
default_install_root="$test_home/.agentic-ops"
fake_uv="$test_root/fake-uv"
poison_root="$test_root/poison"
official_repo_url="git@github.com:tapstate/agentic-ops.git"

mkdir -p "$source_repo" "$test_home" "$poison_root/ao_work"
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

# 产品入口必须拒绝持久化的 url.*.insteadOf。离线测试仅在测试夹具的
# Git transport wrapper 中，为已知网络子命令注入一次性 -c 映射；身份读取、
# get-url 与 --get-url 始终走原始 Git，因此安装后的 raw/effective origin 都是官方地址。
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
  *) exec "${AO_TEST_REAL_GIT:?}" "$@" ;;
esac
EOF
chmod 0755 "$transport_git_dir/git"

test_python="${AGENTIC_OPS_TEST_PYTHON:-$repo_root/.venv/bin/python}"
if [ ! -x "$test_python" ]; then
  test_python="$(command -v python3)"
fi

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'project_root=""' \
  'while [ "$#" -gt 0 ]; do' \
  '  if [ "$1" = "--project" ]; then' \
  '    shift' \
  '    project_root="$1"' \
  '  fi' \
  '  shift' \
  'done' \
  'test -n "$project_root"' \
  'mkdir -p "$project_root/.venv/bin"' \
  'printf "#!/usr/bin/env bash\\nexec %q \\\"%s\\\"\\n" "$AGENTIC_OPS_TEST_REAL_PYTHON" '\''$@'\'' > "$project_root/.venv/bin/python"' \
  'chmod 0755 "$project_root/.venv/bin/python"' \
  > "$fake_uv"
chmod 0755 "$fake_uv"

HOME="$test_home" \
AGENTIC_OPS_HOME="$install_root" \
AGENTIC_OPS_UV="$fake_uv" \
AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
AO_TEST_REAL_GIT="$real_git" \
AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
PATH="$transport_git_dir:$PATH" \
  bash "$source_repo/developer/bootstrap/install.sh" >/dev/null

test -x "$install_root/bin/ao-work"
test ! -e "$install_root/bin/ao-maint"
test ! -e "$install_root/developer/.venv/bin/ao-maint"
test ! -e "$install_root/maintainer"
test ! -e "$install_root/.agentic-ops-source"
test ! -e "$install_root/developer/tests"
if find "$install_root/developer" \
    \( -name fixtures -o -name task_to_pr_producer.py -o -name '*fake*producer*' \) \
    -print -quit | grep . >/dev/null; then
  echo "developer 安装不得包含 tests、fixture 或 fake producer" >&2
  exit 1
fi
test ! -e "$install_root/shared/README.md"
test -f "$install_root/shared/integration/README.md"
test -f "$install_root/shared/integration/task-to-pr-manifest.schema.json"
test -f "$install_root/shared/integration/task-to-pr-event.schema.json"
test -f "$install_root/shared/integration/task-to-pr-result.schema.json"
test "$(git -C "$install_root" config --get remote.origin.url)" = "$official_repo_url"
if find "$install_root" -path '*/ao_maint' -print -quit | grep . >/dev/null; then
  echo "developer 安装不得包含 ao_maint Python 包" >&2
  exit 1
fi

sparse_paths="$(git -C "$install_root" sparse-checkout list)"
normalized_sparse_paths="$(printf '%s\n' "$sparse_paths" |
  sed -e 's#^/##' -e 's#/$##' | sed '/^$/d' | LC_ALL=C sort -u)"
expected_sparse_paths="$(printf '%s\n' \
  .python-version \
  developer/AGENTS.md \
  developer/bootstrap \
  developer/pyproject.toml \
  developer/rules \
  developer/runtime \
  developer/skills \
  developer/standards \
  developer/uv.lock \
  shared/integration \
  shared/standards | LC_ALL=C sort -u)"
if [ "$normalized_sparse_paths" != "$expected_sparse_paths" ]; then
  echo "developer 安装必须精确 checkout developer 生产资产、shared/integration、shared/standards 和 .python-version" >&2
  exit 1
fi
shared_distribution="$(
  cd "$install_root/shared"
  find . -mindepth 1 -print | sed 's#^\./##' | LC_ALL=C sort
)"
expected_shared_distribution="$(printf '%s\n' \
  integration \
  integration/README.md \
  integration/task-to-pr-event.schema.json \
  integration/task-to-pr-manifest.schema.json \
  integration/task-to-pr-result.schema.json \
  standards \
  standards/jira-comment-template.schema.json)"
if [ "$shared_distribution" != "$expected_shared_distribution" ]; then
  echo "developer 安装的 shared 可见树超出固定协议白名单" >&2
  exit 1
fi
if find "$install_root/shared" -type l -print -quit | grep . >/dev/null || \
  find "$install_root/shared" -type f -perm -111 -print -quit | grep . >/dev/null || \
  find "$install_root/shared" -type f \( \
    -name 'AGENTS.md' -o -name 'SKILL.md' -o -name '*.py' -o -name '*.sh' \
  \) -print -quit | grep . >/dev/null; then
  echo "developer 安装的 shared 不得包含符号链接、可执行文件、脚本或 AI 入口" >&2
  exit 1
fi

printf 'raise SystemExit("forbidden shared Python")\n' \
  > "$install_root/shared/integration/forbidden.py"
if "$install_root/bin/ao-work" capability list \
    >"$test_root/shared-visible-contamination.out" \
    2>"$test_root/shared-visible-contamination.err"; then
  echo "ao-work 必须拒绝 shared 可见树中的未跟踪非准入文件" >&2
  exit 1
fi
grep -q 'developer_shared_distribution_invalid' \
  "$test_root/shared-visible-contamination.out"
rm "$install_root/shared/integration/forbidden.py"

assert_invalid_shared_source_install() {
  local fixture_repo="$1"
  local label="$2"
  local rejected_install="$test_root/rejected-shared-$label"

  if HOME="$test_home" \
    AGENTIC_OPS_HOME="$rejected_install" \
    AGENTIC_OPS_UV="$fake_uv" \
    AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
    AO_TEST_REAL_GIT="$real_git" \
    AO_TEST_FIXTURE_REPOSITORY="$fixture_repo" \
    AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
    PATH="$transport_git_dir:$PATH" \
    bash "$source_repo/developer/bootstrap/install.sh" \
      >"$test_root/rejected-shared-$label.out" \
      2>"$test_root/rejected-shared-$label.err"; then
    echo "Bootstrap 必须拒绝提交树中的非法 shared 资产：$label" >&2
    exit 1
  fi
  grep -q 'developer_shared_source_invalid' \
    "$test_root/rejected-shared-$label.out"
}

shared_extra_source="$test_root/source-shared-extra"
git clone "$source_repo" "$shared_extra_source" >/dev/null 2>&1
git -C "$shared_extra_source" config user.email agentic-ops-test@example.test
git -C "$shared_extra_source" config user.name "AgenticOps Test"
mkdir "$shared_extra_source/shared/hidden"
printf '#!/usr/bin/env bash\n' > "$shared_extra_source/shared/hidden/entry.sh"
git -C "$shared_extra_source" add shared/hidden/entry.sh
git -C "$shared_extra_source" commit -m "add forbidden shared path" >/dev/null
assert_invalid_shared_source_install "$shared_extra_source" extra-path

shared_executable_source="$test_root/source-shared-executable"
git clone "$source_repo" "$shared_executable_source" >/dev/null 2>&1
git -C "$shared_executable_source" config user.email agentic-ops-test@example.test
git -C "$shared_executable_source" config user.name "AgenticOps Test"
git -C "$shared_executable_source" update-index --chmod=+x \
  shared/integration/task-to-pr-event.schema.json
git -C "$shared_executable_source" commit -m "make shared asset executable" >/dev/null
assert_invalid_shared_source_install "$shared_executable_source" executable

printf '%s\n' \
  'raise SystemExit("external PYTHONPATH poisoned ao-work")' \
  > "$poison_root/ao_work/__main__.py"

help_output="$test_root/help.json"
PYTHONPATH="$poison_root" "$install_root/bin/ao-work" --help > "$help_output"
grep '"operation":"help"' "$help_output" >/dev/null
if grep 'poisoned' "$help_output" >/dev/null; then
  echo "ao-work 受到外部 PYTHONPATH 污染" >&2
  exit 1
fi

capability_output="$test_root/capabilities.json"
AGENTIC_OPS_HOME="$test_root/must-not-select-this-directory" \
  "$install_root/bin/ao-work" capability list > "$capability_output"
grep '"operation":"capability_list"' "$capability_output" >/dev/null
grep '"id":"jira_comment"' "$capability_output" >/dev/null
if grep '"id":"task_state_init"' "$capability_output" >/dev/null; then
  echo "默认能力列表不得暴露内部 task state 命令" >&2
  exit 1
fi

gap_output="$test_root/capability-gap.json"
(
  cd "$test_root"
  "$install_root/bin/ao-work" capability show inspect_task
) > "$gap_output"
grep '"status":"capability_gap"' "$gap_output" >/dev/null
grep '"commands":\[\]' "$gap_output" >/dev/null

# 任意业务 Git 仓库初始化后必须拥有 Codex 标准 repo-scope Skill 入口；
# 内容来自安装根 developer Skills 的普通文件副本，不通过 symlink 指回安装根。
business_workspace="$test_root/business-workspace"
mkdir -p "$business_workspace"
git -C "$business_workspace" init -b main >/dev/null
AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
PYTHONPATH="$install_root/developer/runtime/src" \
  "$test_python" - "$install_root" "$business_workspace" <<'PY'
from pathlib import Path
import sys

from ao_work.workspace_init.service import WorkspaceInitializer

install = Path(sys.argv[1])
workspace = Path(sys.argv[2])
initializer = WorkspaceInitializer(workspace, install)


class Candidate:
    root = workspace

initializer._install_workspace_skills(Candidate())
PY
test -f "$business_workspace/.agents/skills/configure-authorization/SKILL.md"
test -f "$business_workspace/.agents/skills/initialize-project-workspace/SKILL.md"
test ! -e "$business_workspace/.agents/skills/guard-story-quality"
test ! -e "$business_workspace/maintainer"
if find "$business_workspace/.agents/skills" -type l -print -quit | grep . >/dev/null || \
  grep -RIl 'workplane:[[:space:]]*maintainer' \
    "$business_workspace/.agents/skills" | grep . >/dev/null; then
  echo "业务工作空间只能发现普通文件形式的 developer Skills" >&2
  exit 1
fi

fake_origin="$test_root/fake-origin"
mkdir -p "$fake_origin"
git -C "$install_root" remote set-url origin "$fake_origin"
if "$install_root/bin/ao-work" \
    --workspace-root "$test_root/no-workspace" \
    workspace inspect \
    >"$test_root/runtime-override.out" 2>"$test_root/runtime-override.err"; then
  echo "ao-work 不得接受非官方 origin" >&2
  exit 1
fi
grep -q 'install_origin_mismatch' "$test_root/runtime-override.out"
git -C "$install_root" remote set-url origin "$official_repo_url"

git -C "$install_root" config "url.$fake_origin.insteadOf" "$official_repo_url"
if "$install_root/bin/ao-work" \
    --workspace-root "$test_root/no-workspace" \
    workspace inspect \
    >"$test_root/runtime-rewrite.out" 2>"$test_root/runtime-rewrite.err"; then
  echo "ao-work 不得接受被 insteadOf 改写的实际传输地址" >&2
  exit 1
fi
grep -q 'install_transport_rewrite_forbidden' "$test_root/runtime-rewrite.out"
git -C "$install_root" config --unset-all "url.$fake_origin.insteadOf"

for identity_override in \
  AGENTIC_OPS_TEST_MODE \
  AGENTIC_OPS_TEST_LAUNCHER \
  AGENTIC_OPS_TEST_EXPECTED_REPOSITORY \
  AGENTIC_OPS_REPO_URL \
  AGENTIC_OPS_GITHUB_REPOSITORY \
  AGENTIC_OPS_BRANCH; do
  rejected_output="$test_root/rejected-$identity_override.out"
  if env \
    "HOME=$test_home" \
    "AGENTIC_OPS_HOME=$test_root/rejected-install" \
    "AGENTIC_OPS_UV=$fake_uv" \
    "$identity_override=override" \
    bash "$source_repo/developer/bootstrap/install.sh" \
      >"$rejected_output" 2>"$test_root/rejected-$identity_override.err"; then
    echo "安装不得接受身份覆盖环境变量：$identity_override" >&2
    exit 1
  fi
  grep -q 'install_identity_override_forbidden' "$rejected_output"
done

if AGENTIC_OPS_HOME="$test_root/rejected-branch-update" \
  AGENTIC_OPS_BRANCH=develop \
  bash "$source_repo/developer/bootstrap/update.sh" \
    >"$test_root/rejected-branch-update.out" 2>"$test_root/rejected-branch-update.err"; then
  echo "普通更新不得覆盖稳定 main 分支" >&2
  exit 1
fi
grep -q 'install_identity_override_forbidden' "$test_root/rejected-branch-update.out"

untrusted_install="$test_root/untrusted-install"
mkdir -p "$untrusted_install"
git -C "$untrusted_install" init -b main >/dev/null
git -C "$untrusted_install" remote add origin "$test_root/untrusted-origin"
git_wrapper_dir="$test_root/git-wrapper"
git_fetch_marker="$test_root/untrusted-fetch-called"
mkdir -p "$git_wrapper_dir"
cat > "$git_wrapper_dir/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" fetch "* ]]; then
  : > "${AGENTIC_OPS_TEST_FETCH_MARKER:?}"
fi
exec "${AGENTIC_OPS_TEST_REAL_GIT:?}" "$@"
EOF
chmod 0755 "$git_wrapper_dir/git"
if HOME="$test_home" \
  AGENTIC_OPS_HOME="$untrusted_install" \
  AGENTIC_OPS_TEST_FETCH_MARKER="$git_fetch_marker" \
  AGENTIC_OPS_TEST_REAL_GIT="$real_git" \
  PATH="$git_wrapper_dir:$PATH" \
  bash "$source_repo/developer/bootstrap/install.sh" \
    >"$test_root/untrusted-install.out" 2>"$test_root/untrusted-install.err"; then
  echo "错误 origin 的既有安装必须在 fetch 前阻断" >&2
  exit 1
fi
grep -q 'install_origin_mismatch' "$test_root/untrusted-install.out"
test ! -e "$git_fetch_marker"

test ! -e "$test_home/.zshrc"

remote_root="$test_root/remote"
mkdir -p "$remote_root/developer/bootstrap/lib"
cp "$source_repo/developer/bootstrap/install.sh" "$remote_root/install.sh"
cp "$source_repo/developer/bootstrap/lib/common.sh" "$remote_root/developer/bootstrap/lib/common.sh"

fake_tool_dir="$test_root/fake-tools"
mkdir -p "$fake_tool_dir"
fake_gh="$fake_tool_dir/gh"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'cat "$AGENTIC_OPS_TEST_REMOTE_COMMON"' \
  > "$fake_gh"
chmod 0755 "$fake_gh"

HOME="$test_home" \
SHELL=/bin/zsh \
AGENTIC_OPS_HOME="$default_install_root" \
AGENTIC_OPS_UV="$fake_uv" \
AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
AGENTIC_OPS_TEST_REMOTE_COMMON="$remote_root/developer/bootstrap/lib/common.sh" \
AO_TEST_REAL_GIT="$real_git" \
AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
PATH="$fake_tool_dir:$transport_git_dir:$PATH" \
  bash -s < "$remote_root/install.sh" >/dev/null

test -x "$default_install_root/bin/ao-work"
test ! -e "$default_install_root/maintainer"
test "$(git -C "$default_install_root" config --get remote.origin.url)" = "$official_repo_url"
test "$(grep -Fc 'export PATH="$HOME/.agentic-ops/bin:$PATH"' "$test_home/.zshrc")" -eq 1

HOME="$test_home" \
SHELL=/bin/zsh \
AGENTIC_OPS_HOME="$default_install_root" \
AGENTIC_OPS_UV="$fake_uv" \
AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
AGENTIC_OPS_TEST_REMOTE_COMMON="$remote_root/developer/bootstrap/lib/common.sh" \
AO_TEST_REAL_GIT="$real_git" \
AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
PATH="$fake_tool_dir:$transport_git_dir:$PATH" \
  bash -s < "$remote_root/install.sh" >/dev/null

test "$(grep -Fc 'export PATH="$HOME/.agentic-ops/bin:$PATH"' "$test_home/.zshrc")" -eq 1

printf 'update\n' >> "$source_repo/developer/AGENTS.md"
git -C "$source_repo" add developer/AGENTS.md
git -C "$source_repo" commit -m "test update" >/dev/null
update_ref="$(git -C "$source_repo" rev-parse HEAD)"

if HOME="$test_home" \
  SHELL=/bin/zsh \
  AGENTIC_OPS_HOME="$default_install_root" \
  AGENTIC_OPS_UV="$fake_uv" \
  AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
  AGENTIC_OPS_TEST_REMOTE_COMMON="$remote_root/developer/bootstrap/lib/common.sh" \
  AO_TEST_REAL_GIT="$real_git" \
  AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
  AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
  PATH="$fake_tool_dir:$transport_git_dir:$PATH" \
  bash -s < "$remote_root/install.sh" \
    >"$test_root/install-update.out" 2>"$test_root/install-update.err"; then
  echo "重复安装不得绕过独立更新确认" >&2
  exit 1
fi
grep -q 'install_update_required' "$test_root/install-update.out"
test "$(git -C "$default_install_root" rev-parse HEAD)" != "$update_ref"

if HOME="$test_home" \
  AGENTIC_OPS_HOME="$default_install_root" \
  AGENTIC_OPS_UV="$fake_uv" \
  AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
  AO_TEST_REAL_GIT="$real_git" \
  AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
  AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
  PATH="$transport_git_dir:$PATH" \
  bash "$default_install_root/developer/bootstrap/update.sh" \
    >"$test_root/update-unconfirmed.out" 2>"$test_root/update-unconfirmed.err"; then
  echo "非交互更新缺少确认时必须阻断" >&2
  exit 1
fi
grep -q 'update_confirmation_required' "$test_root/update-unconfirmed.out"
test "$(git -C "$default_install_root" rev-parse HEAD)" != "$update_ref"

HOME="$test_home" \
AGENTIC_OPS_HOME="$default_install_root" \
AGENTIC_OPS_UV="$fake_uv" \
AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
AGENTIC_OPS_ASSUME_YES=1 \
AO_TEST_REAL_GIT="$real_git" \
AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
PATH="$transport_git_dir:$PATH" \
  bash "$default_install_root/developer/bootstrap/update.sh" >/dev/null
test "$(git -C "$default_install_root" rev-parse HEAD)" = "$update_ref"
test "$(sed -n '1p' "$default_install_root/.local/previous-ref")" != "$update_ref"

HOME="$test_home" \
AGENTIC_OPS_HOME="$default_install_root" \
AGENTIC_OPS_UV="$fake_uv" \
AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
  bash "$default_install_root/developer/bootstrap/rollback.sh" >/dev/null
test "$(git -C "$default_install_root" rev-parse HEAD)" != "$update_ref"
test -x "$default_install_root/bin/ao-work"
test ! -e "$default_install_root/maintainer"

file_mode() {
  local path="$1"
  if stat -f '%Lp' "$path" >/dev/null 2>&1; then
    stat -f '%Lp' "$path"
  else
    stat -c '%a' "$path"
  fi
}

assert_update_rejected() {
  local install_dir="$1"
  local label="$2"
  local expected_code="$3"
  local output="$test_root/managed-path-$label.out"
  local error_output="$test_root/managed-path-$label.err"

  if HOME="$test_home" \
    AGENTIC_OPS_HOME="$install_dir" \
    AGENTIC_OPS_UV="$fake_uv" \
    AGENTIC_OPS_TEST_REAL_PYTHON="$test_python" \
    AGENTIC_OPS_ASSUME_YES=1 \
    AO_TEST_REAL_GIT="$real_git" \
    AO_TEST_FIXTURE_REPOSITORY="$source_repo" \
    AO_TEST_OFFICIAL_REPOSITORY="$official_repo_url" \
    PATH="$transport_git_dir:$PATH" \
    bash "$default_install_root/developer/bootstrap/update.sh" \
      >"$output" 2>"$error_output"; then
    echo "受管路径异常必须在更新写入前阻断：$label" >&2
    exit 1
  fi
  grep -q "$expected_code" "$output"
}

for managed_relative in .local bin developer/.venv; do
  managed_label="$(printf '%s' "$managed_relative" | tr '/.' '--')"
  managed_path="$default_install_root/$managed_relative"
  managed_backup="$test_root/managed-backup-$managed_label"
  external_directory="$test_root/external-directory-$managed_label"
  mv "$managed_path" "$managed_backup"
  mkdir "$external_directory"
  printf 'outside-unchanged\n' > "$external_directory/sentinel.txt"
  ln -s "$external_directory" "$managed_path"

  assert_update_rejected \
    "$default_install_root" "$managed_label-symlink" install_managed_path_invalid
  test "$(cat "$external_directory/sentinel.txt")" = "outside-unchanged"
  test "$(ls -A "$external_directory")" = "sentinel.txt"

  rm "$managed_path"
  mv "$managed_backup" "$managed_path"
done

for ref_name in current-ref previous-ref pending-rollback-ref; do
  ref_path="$default_install_root/.local/$ref_name"
  ref_backup="$test_root/ref-backup-$ref_name"
  ref_sentinel="$test_root/ref-sentinel-$ref_name"
  ref_existed=false
  if [ -e "$ref_path" ]; then
    mv "$ref_path" "$ref_backup"
    ref_existed=true
  fi
  printf 'outside-ref-unchanged\n' > "$ref_sentinel"
  ln -s "$ref_sentinel" "$ref_path"

  assert_update_rejected \
    "$default_install_root" "$ref_name-symlink" install_ref_path_invalid
  test "$(cat "$ref_sentinel")" = "outside-ref-unchanged"

  rm "$ref_path"
  if [ "$ref_existed" = "true" ]; then
    mv "$ref_backup" "$ref_path"
  fi
done

local_backup="$test_root/non-directory-local"
mv "$default_install_root/.local" "$local_backup"
mkfifo "$default_install_root/.local"
assert_update_rejected \
  "$default_install_root" local-special-file install_managed_path_invalid
rm "$default_install_root/.local"
mv "$local_backup" "$default_install_root/.local"

mkdir "$default_install_root/.local/pending-rollback-ref"
assert_update_rejected \
  "$default_install_root" pending-ref-directory install_ref_path_invalid
rmdir "$default_install_root/.local/pending-rollback-ref"

install_alias="$test_root/install-root-symlink"
ln -s "$default_install_root" "$install_alias"
assert_update_rejected \
  "$install_alias" install-root-symlink install_managed_path_invalid
rm "$install_alias"

current_head="$(git -C "$default_install_root" rev-parse HEAD)"
bash -c \
  '. "$1"; agentic_write_ref_atomic "$2" pending-rollback-ref "$3"' \
  bootstrap-ref-write \
  "$default_install_root/developer/bootstrap/lib/common.sh" \
  "$default_install_root" \
  "$current_head"
test "$(cat "$default_install_root/.local/pending-rollback-ref")" = "$current_head"
for ref_name in current-ref previous-ref pending-rollback-ref; do
  test "$(file_mode "$default_install_root/.local/$ref_name")" = "600"
done
bash -c \
  '. "$1"; agentic_remove_ref "$2" pending-rollback-ref' \
  bootstrap-ref-remove \
  "$default_install_root/developer/bootstrap/lib/common.sh" \
  "$default_install_root"
test ! -e "$default_install_root/.local/pending-rollback-ref"

# Bootstrap 的 stdout 合同必须始终是合法 JSON；动态路径或诊断中出现
# 引号、反斜杠、换行、回车和制表符也不能破坏机器输出。
json_contract_output="$test_root/bootstrap-json-contract.out"
bash -c \
  '. "$1"; agentic_bootstrap_json_success bootstrap_test install_dir "$2"' \
  bootstrap-json-success \
  "$default_install_root/developer/bootstrap/lib/common.sh" \
  $'path/with"quote\\slash\nline\rreturn\ttab' \
  > "$json_contract_output"
"$test_python" -c \
  'import json,sys; data=json.load(open(sys.argv[1])); assert data["install_dir"] == "path/with\"quote\\slash\nline\rreturn\ttab"' \
  "$json_contract_output"
if bash -c \
  '. "$1"; agentic_bootstrap_error code "$2" "$3"' \
  bootstrap-json-error \
  "$default_install_root/developer/bootstrap/lib/common.sh" \
  $'message "quoted"\\path\nnext' \
  $'action\tvalue' \
  > "$json_contract_output" 2>/dev/null; then
  echo "agentic_bootstrap_error 必须返回失败" >&2
  exit 1
fi
"$test_python" -c \
  'import json,sys; data=json.load(open(sys.argv[1])); assert data["code"] == "code"; assert "quoted" in data["message"]' \
  "$json_contract_output"

printf '{"ok":true,"operation":"developer_install_boundary"}\n'
