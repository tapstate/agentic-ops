#!/usr/bin/env bash
set -euo pipefail

# 本测试会在 fixture 内运行独立发布验证；不得继承外层完整验证用于
# 防递归的标记，否则 fixture 会错误跳过发布工作流固定验证。
unset AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/../.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT
real_git="$(command -v git)"

fail() {
  printf '发布工作流验证失败：%s\n' "$1" >&2
  exit 1
}

fake_gh="$test_root/gh"
ruleset_status="$test_root/ruleset-status.txt"
printf 'active\tbranch\ttrue\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\ttrue\ttrue\ttrue\n' \
  > "$ruleset_status"
export FAKE_RULESET_STATUS_FILE="$ruleset_status"
cat > "$fake_gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
  exit 0
fi
if [ "${1:-}" != "api" ]; then
  printf 'unsupported fake gh command: %s\n' "$*" >&2
  exit 2
fi
if [[ "$*" == *"/rulesets/42"* ]]; then
  cat "${FAKE_RULESET_STATUS_FILE:?}"
  exit 0
fi
case "$*" in
  *"/rulesets"*)
    printf '42\n'
    ;;
  *".default_branch"*)
    printf 'main\n'
    ;;
  *".allow_auto_merge"*)
    printf 'true\n'
    ;;
  *".allow_merge_commit"*)
    printf 'true\n'
    ;;
  *)
    printf 'unsupported fake gh api: %s\n' "$*" >&2
    exit 2
    ;;
esac
EOF
chmod 0755 "$fake_gh"

# 远端读取失败与 develop 明确不存在必须分开处理；读取失败时不得建议创建分支。
unreadable_remote="$test_root/unreadable-remote"
git init "$unreadable_remote" >/dev/null
git -C "$unreadable_remote" remote add origin "$test_root/does-not-exist.git"
if (
  . "$repo_root/maintainer/scripts/lib/development-workflow.sh"
  workflow_check_develop "$unreadable_remote"
); then
  fail "无法读取 origin 时不得报告 develop 已存在"
else
  unreadable_status=$?
fi
test "$unreadable_status" -eq 2 ||
  fail "无法读取 origin 时必须返回独立状态 2"

fake_uv="$test_root/uv"
cat > "$fake_uv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
project=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--project" ]; then
    shift
    project="$1"
  fi
  shift
done
[ -n "$project" ]
"${AGENTIC_OPS_TEST_REAL_PYTHON:?}" -m venv --clear --system-site-packages \
  "$project/.venv"
venv_site="$("$project/.venv/bin/python" -c \
  'import site; print(site.getsitepackages()[0])')"
"${AGENTIC_OPS_TEST_REAL_PYTHON:?}" -c \
  'import site; print("\n".join(site.getsitepackages()))' \
  > "$venv_site/agentic-ops-test-dependencies.pth"
printf '%s\n' "$project" >> "${FAKE_UV_LOG:?}"
EOF
chmod 0755 "$fake_uv"
fake_uv_log="$test_root/uv.log"
: > "$fake_uv_log"
test_real_python="${AGENTIC_OPS_TEST_PYTHON:-$(command -v python3)}"
export AGENTIC_OPS_UV="$fake_uv"
export AGENTIC_OPS_TEST_REAL_PYTHON="$test_real_python"
export FAKE_UV_LOG="$fake_uv_log"

# 首次迁移时，候选提交即使已经带上新门禁，也不能在 origin/main 缺少
# 可信基线时自证。只允许受保护 main 的人工审查 PR 先完成基线升级。
legacy_remote="$test_root/legacy-remote.git"
legacy_fixture="$test_root/legacy-repo"
git init --bare "$legacy_remote" >/dev/null
git clone "$legacy_remote" "$legacy_fixture" >/dev/null 2>&1
git -C "$legacy_fixture" config user.email agentic-ops-test@example.test
git -C "$legacy_fixture" config user.name "AgenticOps Test"
printf 'legacy main without story gate\n' > "$legacy_fixture/README.md"
git -C "$legacy_fixture" add README.md
git -C "$legacy_fixture" commit -m "legacy main" >/dev/null
git -C "$legacy_fixture" branch -M main
git -C "$legacy_fixture" push -u origin main >/dev/null
git -C "$legacy_fixture" switch -c develop >/dev/null
printf 'candidate introduces story gate\n' >> "$legacy_fixture/README.md"
git -C "$legacy_fixture" add README.md
git -C "$legacy_fixture" commit -m "candidate migration" >/dev/null
legacy_head="$(git -C "$legacy_fixture" rev-parse HEAD)"
if (
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  release_verify_story_gate "$legacy_fixture" origin/main "$legacy_head"
) >"$test_root/legacy-baseline.out" 2>"$test_root/legacy-baseline.err"; then
  fail "origin/main 缺少新门禁时不得让迁移候选自证"
fi
grep -q 'release_story_gate_baseline_upgrade_required' \
  "$test_root/legacy-baseline.err" || {
  cat "$test_root/legacy-baseline.err" >&2
  fail "首次门禁迁移未返回两阶段基线升级失败码"
}

remote="$test_root/remote.git"
fixture="$test_root/repo"
git init --bare "$remote" >/dev/null
git clone "$remote" "$fixture" >/dev/null 2>&1
git -C "$fixture" config user.email agentic-ops-test@example.test
git -C "$fixture" config user.name "AgenticOps Test"

mkdir -p \
  "$fixture/.githooks" \
  "$fixture/maintainer/scripts/lib" \
  "$fixture/maintainer/runtime/src/ao_maint/story_gate" \
  "$fixture/maintainer/standards/git" \
  "$fixture/developer/tests/bootstrap"
cp "$repo_root/.githooks/pre-commit" "$fixture/.githooks/pre-commit"
cp "$repo_root/.githooks/pre-push" "$fixture/.githooks/pre-push"
cp "$repo_root/maintainer/scripts/release.sh" "$fixture/maintainer/scripts/release.sh"
cp "$repo_root/maintainer/scripts/hotfix.sh" "$fixture/maintainer/scripts/hotfix.sh"
cp "$repo_root/maintainer/scripts/lib/release-common.sh" \
  "$fixture/maintainer/scripts/lib/release-common.sh"
cp "$repo_root/maintainer/scripts/lib/development-workflow.sh" \
  "$fixture/maintainer/scripts/lib/development-workflow.sh"

for verification in \
  maintainer/scripts/test-python-runtime.sh \
  maintainer/scripts/test-resources.sh \
  developer/tests/bootstrap/test_install_boundary.sh \
  maintainer/scripts/test-release-workflow.sh; do
  mkdir -p "$fixture/$(dirname "$verification")"
  verification_id="$(printf '%s' "$verification" | tr '/' '-')"
cat > "$fixture/$verification" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '$verification_id' >> "\${FAKE_VERIFY_LOG:?}"
if [ "\${FAKE_VERIFY_FAIL_ID:-}" = '$verification_id' ]; then
  printf 'fixture verification failure: %s\n' '$verification_id' >&2
  exit 17
fi
EOF
  chmod 0755 "$fixture/$verification"
done

printf '# release fixture\n' > "$fixture/README.md"
printf '.local/\n.venv/\n' > "$fixture/.gitignore"
printf 'maintainer\n' > "$fixture/.agentic-ops-source"
mkdir -p "$fixture/maintainer/standards/stories" "$fixture/maintainer/bin"
printf '# maintainer fixture\n' > "$fixture/maintainer/AGENTS.md"
printf '[project]\nname = "ao-maint-fixture"\nversion = "0.0.0"\n' \
  > "$fixture/maintainer/pyproject.toml"
printf 'version = 1\n' > "$fixture/maintainer/uv.lock"
printf '# trusted baseline fixture marker\n' \
  > "$fixture/maintainer/runtime/src/ao_maint/story_gate/service.py"
printf 'schema_version: 1\nstory_categories: [maintainer, developer]\nstories: []\n' \
  > "$fixture/maintainer/standards/stories/project-quality.yaml"
printf 'schema_version: 1\ndefault_target_branch: develop\nprotected_branches: [main]\ncommit_review_branches: [develop]\npr_review_branches: []\nspecial_branch_patterns: []\n' \
  > "$fixture/maintainer/standards/git/story-review-policy.yaml"
story_gate_log="$test_root/story-gate.log"
: > "$story_gate_log"
export FAKE_STORY_GATE_LOG="$story_gate_log"
cat > "$fixture/maintainer/bin/ao-maint" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [ -n "${FAKE_STORY_GATE_LOG:-}" ]; then
  printf '%s\n' "$*" >> "$FAKE_STORY_GATE_LOG"
fi
if [ "${FAKE_STORY_GATE_DENY:-0}" = "1" ]; then
  exit 9
fi
printf '{"ok":true,"operation":"story_impact","has_impact":false}\n'
EOF
chmod 0755 \
  "$fixture/.githooks/pre-commit" \
  "$fixture/.githooks/pre-push" \
  "$fixture/maintainer/bin/ao-maint" \
  "$fixture/maintainer/scripts/release.sh" \
  "$fixture/maintainer/scripts/hotfix.sh" \
  "$fixture/maintainer/scripts/lib/release-common.sh" \
  "$fixture/maintainer/scripts/lib/development-workflow.sh"
git -C "$fixture" add .
git -C "$fixture" commit -m "initial release fixture" >/dev/null
git -C "$fixture" branch -M main
git -C "$fixture" push -u origin main >/dev/null
git -C "$fixture" switch -c develop >/dev/null
printf 'develop\n' >> "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -m "develop release fixture" >/dev/null
git -C "$fixture" push -u origin develop >/dev/null
(
  . "$fixture/maintainer/scripts/lib/development-workflow.sh"
  workflow_install_trusted_hooks "$fixture"
)
(
  . "$fixture/maintainer/scripts/lib/development-workflow.sh"
  workflow_check_hooks "$fixture"
) || fail "测试仓库未启用 Git common directory 可信 Hook launcher"

# main 在候选准备之后前进时，发布故事门禁仍必须使用最新 main 的可信
# Runtime，但只审查 main 与固定候选的 merge-base 之后的候选范围。
release_story_range_base="$(git -C "$fixture" rev-parse origin/main)"
git -C "$fixture" switch main >/dev/null
printf 'independent main update\n' > "$fixture/MAIN-ONLY.md"
git -C "$fixture" add MAIN-ONLY.md
git -C "$fixture" -c core.hooksPath=/dev/null commit -m "independent main fixture update" >/dev/null
git -C "$fixture" -c core.hooksPath=/dev/null push origin main >/dev/null
git -C "$fixture" switch develop >/dev/null
git -C "$fixture" fetch origin main >/dev/null
release_story_head="$(git -C "$fixture" rev-parse HEAD)"
: > "$story_gate_log"
if ! (
  . "$fixture/maintainer/scripts/lib/release-common.sh"
  release_verify_story_gate "$fixture" origin/main "$release_story_head"
) >"$test_root/diverged-story-gate.out" 2>"$test_root/diverged-story-gate.err"; then
  cat "$test_root/diverged-story-gate.err" >&2
  fail "main 前进后发布故事门禁必须接受固定候选的 merge-base 范围"
fi
grep -Eq "^--source-root .+/candidate story impact --change-source range --base $release_story_range_base --head $release_story_head$" \
  "$story_gate_log" || fail "分叉发布候选未以 merge-base 作为故事门禁范围"

pre_push_head="$(git -C "$fixture" rev-parse HEAD)"
pre_push_main="$(git --git-dir="$remote" rev-parse refs/heads/main)"
zero_sha="0000000000000000000000000000000000000000"
if printf 'refs/heads/develop %s refs/heads/main %s\n' "$pre_push_head" "$pre_push_main" |
  (cd "$fixture" && .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-main.out" 2>"$test_root/pre-push-main.err"; then
  fail "pre-push 必须拒绝任何直接写入 main 的引用"
fi
grep -q 'direct push to main is prohibited' "$test_root/pre-push-main.err" ||
  fail "pre-push 拒绝 main 时没有稳定提示"
if ! printf 'refs/heads/develop %s refs/heads/main %s\n' "$pre_push_head" "$pre_push_main" |
  (cd "$fixture" && AGENTIC_OPS_SPECIAL_PUSH=hotfix .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-hotfix-main.out" 2>"$test_root/pre-push-hotfix-main.err"; then
  fail "Hotfix 专用执行标识必须允许原子直推 main"
fi
if ! printf '%s %s refs/heads/main %s\n%s %s refs/heads/develop %s\n' \
  "$pre_push_head" "$pre_push_head" "$pre_push_main" \
  "$pre_push_head" "$pre_push_head" "$pre_push_head" |
  (cd "$fixture" && AGENTIC_OPS_SPECIAL_PUSH=hotfix .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-hotfix-sha.out" 2>"$test_root/pre-push-hotfix-sha.err"; then
  fail "Hotfix 必须允许 commit-tree 生成的 SHA 原子更新 main/develop"
fi
if printf '%s %s refs/tags/hotfix-invalid %s\n' \
  "$pre_push_head" "$pre_push_head" "$zero_sha" |
  (cd "$fixture" && AGENTIC_OPS_SPECIAL_PUSH=hotfix .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-hotfix-tag.out" 2>"$test_root/pre-push-hotfix-tag.err"; then
  fail "Hotfix 专用执行标识不得写入 main/develop 之外的引用"
fi
grep -q 'hotfix_push_target_invalid' "$test_root/pre-push-hotfix-tag.err" ||
  fail "Hotfix 非法目标未返回稳定失败码"
if ! printf 'refs/heads/develop %s refs/heads/develop %s\n' "$pre_push_head" "$pre_push_head" |
  (cd "$fixture" && .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-develop.out" 2>"$test_root/pre-push-develop.err"; then
  fail "pre-push 不得阻断 develop"
fi
if printf 'refs/tags/v0.0-test %s refs/tags/v0.0-test %s\n' "$pre_push_head" "$zero_sha" |
  (cd "$fixture" && .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-tag.out" 2>"$test_root/pre-push-tag.err"; then
  fail "普通 git push 不得绕过版本化发布流程推送 Tag"
fi
grep -q 'story_review_channel_protected' "$test_root/pre-push-tag.err" ||
  fail "pre-push 拒绝普通 Tag 推送时没有稳定提示"
if ! printf 'refs/tags/v0.0-test %s refs/tags/v0.0-test %s\n' "$pre_push_head" "$zero_sha" |
  (cd "$fixture" && AGENTIC_OPS_SPECIAL_PUSH=release .githooks/pre-push origin "$remote") \
    >"$test_root/pre-push-release-tag.out" 2>"$test_root/pre-push-release-tag.err"; then
  fail "版本化发布上下文必须允许推送 Tag"
fi

registry_backup="$test_root/project-quality.yaml"
cp "$fixture/maintainer/standards/stories/project-quality.yaml" "$registry_backup"
rm "$fixture/maintainer/standards/stories/project-quality.yaml"
if (cd "$fixture" && .githooks/pre-commit) >"$test_root/missing-registry.out" 2>"$test_root/missing-registry.err"; then
  fail "AgenticOps 源头缺少故事注册表时必须阻断提交"
fi
grep -q 'story quality registry is missing' "$test_root/missing-registry.err" ||
  fail "故事注册表缺失没有稳定阻断信息"
cp "$registry_backup" "$fixture/maintainer/standards/stories/project-quality.yaml"
[ -z "$(git -C "$fixture" status --porcelain)" ] ||
  fail "故事门禁 fixture 必须作为初始提交的一部分，恢复注册表后仓库应保持干净"

git -C "$fixture" rm --cached maintainer/standards/stories/project-quality.yaml >/dev/null
cp "$registry_backup" "$fixture/maintainer/standards/stories/project-quality.yaml"
if (cd "$fixture" && .githooks/pre-commit) >"$test_root/index-missing-registry.out" 2>"$test_root/index-missing-registry.err"; then
  fail "注册表只在工作树恢复、但 staged snapshot 已删除时必须阻断"
fi
grep -q 'required gate asset is missing' "$test_root/index-missing-registry.err" ||
  fail "staged snapshot 删除注册表没有稳定阻断信息"
git -C "$fixture" add maintainer/standards/stories/project-quality.yaml
[ -z "$(git -C "$fixture" status --porcelain)" ] ||
  fail "staged registry 门禁测试结束后 fixture 应保持干净"

git -C "$fixture" rm .agentic-ops-source >/dev/null
if (cd "$fixture" && .githooks/pre-commit) >"$test_root/deleted-marker.out" 2>"$test_root/deleted-marker.err"; then
  fail "暂存删除 AgenticOps source marker 时不得绕过故事门禁"
fi
grep -q 'source marker is missing or invalid' "$test_root/deleted-marker.err" ||
  fail "暂存删除 source marker 没有稳定阻断信息"
git -C "$fixture" restore --staged .agentic-ops-source
git -C "$fixture" restore .agentic-ops-source

printf 'developer\n' > "$fixture/.agentic-ops-source"
git -C "$fixture" add .agentic-ops-source
if (cd "$fixture" && .githooks/pre-commit) >"$test_root/changed-marker.out" 2>"$test_root/changed-marker.err"; then
  fail "暂存改写 AgenticOps source marker 时不得绕过故事门禁"
fi
grep -q 'source marker is missing or invalid' "$test_root/changed-marker.err" ||
  fail "暂存改写 source marker 没有稳定阻断信息"
git -C "$fixture" restore --staged .agentic-ops-source
git -C "$fixture" restore .agentic-ops-source
[ -z "$(git -C "$fixture" status --porcelain)" ] ||
  fail "source marker 门禁测试结束后 fixture 应保持干净"

# 可复现 sentinel：若 Hook 直接对 maintainer/.local 执行 mkdir/cp，祖先
# 符号链接会把批准或证据写到仓库外。门禁必须在创建快照和复制记录前拒绝，
# 且外部 sentinel 目录不能出现任何文件。
hook_state_sentinel="$test_root/hook-state-sentinel"
mkdir -p "$hook_state_sentinel"
ln -s "$hook_state_sentinel" "$fixture/maintainer/.local"
printf 'hook local-state symlink probe\n' >> "$fixture/README.md"
git -C "$fixture" add README.md
if (cd "$fixture" && .githooks/pre-commit) \
  >"$test_root/hook-state-symlink.out" \
  2>"$test_root/hook-state-symlink.err"; then
  fail "pre-commit 不得跟随 maintainer/.local 祖先符号链接"
fi
grep -q 'story_gate_local_state_unsafe' \
  "$test_root/hook-state-symlink.err" || {
  cat "$test_root/hook-state-symlink.err" >&2
  fail "pre-commit 未对本地故事状态链接返回稳定失败码"
}
if find "$hook_state_sentinel" -mindepth 1 -print -quit | grep -q .; then
  fail "pre-commit 在仓库外 sentinel 写入了故事状态"
fi
git -C "$fixture" restore --staged README.md
git -C "$fixture" restore README.md
rm -f "$fixture/maintainer/.local"

# leaf 也必须是普通 JSON；FIFO、目录、socket 等特殊文件不能被静默跳过。
mkdir -p "$fixture/maintainer/.local/story-approvals"
mkfifo "$fixture/maintainer/.local/story-approvals/fifo.json"
if (cd "$fixture" && .githooks/pre-commit) \
  >"$test_root/hook-state-special.out" \
  2>"$test_root/hook-state-special.err"; then
  fail "pre-commit 不得接受故事状态目录中的特殊文件"
fi
grep -q 'story_gate_local_state_unsafe' \
  "$test_root/hook-state-special.err" ||
  fail "pre-commit 特殊文件负测没有稳定失败码"
rm -f "$fixture/maintainer/.local/story-approvals/fifo.json"
rmdir "$fixture/maintainer/.local/story-approvals" "$fixture/maintainer/.local"
[ -z "$(git -C "$fixture" status --porcelain)" ] ||
  fail "本地故事状态安全负测结束后 fixture 应保持干净"

# 审计复现：HEAD 中 ao-maint 明确拒绝，工作树只把它未暂存改成成功，index
# 只包含 README。Hook 必须在执行工作树 launcher 前识别信任差异并拒绝。
hook_audit="$test_root/hook-audit"
git init "$hook_audit" >/dev/null
git -C "$hook_audit" config user.email agentic-ops-test@example.test
git -C "$hook_audit" config user.name "AgenticOps Test"
mkdir -p \
  "$hook_audit/.githooks" \
  "$hook_audit/maintainer/bin" \
  "$hook_audit/maintainer/standards/git" \
  "$hook_audit/maintainer/standards/stories"
cp "$repo_root/.githooks/pre-commit" "$hook_audit/.githooks/pre-commit"
cp "$repo_root/.githooks/pre-push" "$hook_audit/.githooks/pre-push"
printf 'maintainer\n' > "$hook_audit/.agentic-ops-source"
printf '# audit fixture\n' > "$hook_audit/README.md"
printf 'schema_version: 1\nstory_categories: [maintainer, developer]\nstories: []\n' \
  > "$hook_audit/maintainer/standards/stories/project-quality.yaml"
printf 'schema_version: 1\ndefault_target_branch: develop\nprotected_branches: [main]\ncommit_review_branches: [develop]\npr_review_branches: []\nspecial_branch_patterns: []\n' \
  > "$hook_audit/maintainer/standards/git/story-review-policy.yaml"
cat > "$hook_audit/maintainer/bin/ao-maint" <<'EOF'
#!/usr/bin/env bash
exit 9
EOF
chmod 0755 \
  "$hook_audit/.githooks/pre-commit" \
  "$hook_audit/.githooks/pre-push" \
  "$hook_audit/maintainer/bin/ao-maint"
git -C "$hook_audit" add .
git -C "$hook_audit" commit -m "audit baseline rejects" >/dev/null
git -C "$hook_audit" branch -M develop
cat > "$hook_audit/maintainer/bin/ao-maint" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
printf 'only staged change\n' >> "$hook_audit/README.md"
git -C "$hook_audit" add README.md
[ "$(git -C "$hook_audit" diff --cached --name-only)" = "README.md" ] ||
  fail "Hook 攻击复现的 index 必须只包含 README"
[ "$(git -C "$hook_audit" diff --name-only)" = "maintainer/bin/ao-maint" ] ||
  fail "Hook 攻击复现必须只留下未暂存 launcher 篡改"
if (cd "$hook_audit" && .githooks/pre-commit) \
  >"$test_root/hook-audit.out" 2>"$test_root/hook-audit.err"; then
  fail "pre-commit 不得执行未暂存篡改为成功的 worktree gate"
fi
grep -q 'story gate implementation has unstaged changes' \
  "$test_root/hook-audit.err" || {
  cat "$test_root/hook-audit.err" >&2
  fail "pre-commit 未明确拒绝门禁实现的未暂存篡改"
}

# release 也从真实 Git fixture 建立 baseline/candidate worktree。源工作区的
# .local 若指向仓库外，必须在任何 candidate 状态复制前失败且 sentinel 不变。
release_state_sentinel="$test_root/release-state-sentinel"
mkdir -p "$release_state_sentinel"
ln -s "$release_state_sentinel" "$fixture/maintainer/.local"
release_state_head="$(git -C "$fixture" rev-parse HEAD)"
if (
  . "$fixture/maintainer/scripts/lib/release-common.sh"
  release_verify_story_gate "$fixture" origin/main "$release_state_head"
) >"$test_root/release-state-symlink.out" \
  2>"$test_root/release-state-symlink.err"; then
  fail "release 故事门禁不得跟随 maintainer/.local 祖先符号链接"
fi
grep -q 'release_story_gate_local_state_unsafe' \
  "$test_root/release-state-symlink.err" || {
  cat "$test_root/release-state-symlink.err" >&2
  fail "release 未对本地故事状态链接返回稳定失败码"
}
if find "$release_state_sentinel" -mindepth 1 -print -quit | grep -q .; then
  fail "release 在仓库外 sentinel 写入了故事状态"
fi
rm -f "$fixture/maintainer/.local"
[ -z "$(git -C "$fixture" status --porcelain)" ] ||
  fail "release 本地状态链接负测结束后 fixture 应保持干净"

# 两类正常发布审计都必须共用同一安全写边界。这里直接调用真实 writer：祖先
# symlink 不得把 release-runs 或 JSON 写进仓库外 sentinel；中间目录位置被
# FIFO 占用时也必须先失败，不能继续创建叶子或报告审计完成。
invoke_audit_writer() {
  local audit_kind="$1"
  local audit_root="$2"
  (
    . "$repo_root/maintainer/scripts/lib/release-common.sh"
    RELEASE_VERIFIED_AT="2026-08-14T00:00:00Z"
    RELEASE_PR_NUMBER="123"
    RELEASE_PR_URL="https://github.example.test/tapstate/agentic-ops/pull/123"
    RELEASE_MERGE_COMMIT="2222222222222222222222222222222222222222"
    RELEASE_TAG_COMMIT="3333333333333333333333333333333333333333"
    case "$audit_kind" in
      release_completed)
        release_write_audit \
          "$audit_root" v9.9 1111111111111111111111111111111111111111 hard
        ;;
      release_waiting)
        release_write_waiting_audit \
          "$audit_root" release_publish v9.9 \
          1111111111111111111111111111111111111111 release/v9.9
        ;;
      *)
        exit 99
        ;;
    esac
  )
}

for audit_kind in release_completed release_waiting; do
  audit_symlink_root="$test_root/audit-$audit_kind-symlink"
  audit_external_sentinel="$test_root/audit-$audit_kind-external"
  mkdir -p "$audit_symlink_root" "$audit_external_sentinel"
  printf 'sentinel unchanged\n' > "$audit_external_sentinel/marker.txt"
  ln -s "$audit_external_sentinel" "$audit_symlink_root/.local"
  if invoke_audit_writer "$audit_kind" "$audit_symlink_root" \
    >"$test_root/audit-$audit_kind-symlink.out" \
    2>"$test_root/audit-$audit_kind-symlink.err"; then
    fail "$audit_kind 不得跟随 .local 祖先符号链接"
  fi
  grep -q 'release_audit_path_unsafe' \
    "$test_root/audit-$audit_kind-symlink.err" || {
    cat "$test_root/audit-$audit_kind-symlink.err" >&2
    fail "$audit_kind 的审计 symlink 负测没有稳定失败码"
  }
  [ "$(find "$audit_external_sentinel" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" = "1" ] &&
    [ "$(cat "$audit_external_sentinel/marker.txt")" = "sentinel unchanged" ] ||
    fail "$audit_kind 向仓库外审计 sentinel 写入了内容"

  audit_special_root="$test_root/audit-$audit_kind-special"
  mkdir -p "$audit_special_root/.local"
  mkfifo "$audit_special_root/.local/release-runs"
  if invoke_audit_writer "$audit_kind" "$audit_special_root" \
    >"$test_root/audit-$audit_kind-special.out" \
    2>"$test_root/audit-$audit_kind-special.err"; then
    fail "$audit_kind 不得接受特殊文件占用 release-runs"
  fi
  grep -q 'release_audit_path_unsafe' \
    "$test_root/audit-$audit_kind-special.err" || {
    cat "$test_root/audit-$audit_kind-special.err" >&2
    fail "$audit_kind 的审计特殊文件负测没有稳定失败码"
  }
  [ -p "$audit_special_root/.local/release-runs" ] ||
    fail "$audit_kind 不得替换审计特殊文件 sentinel"

  audit_positive_root="$test_root/audit-$audit_kind-positive"
  mkdir -p "$audit_positive_root"
  invoke_audit_writer "$audit_kind" "$audit_positive_root" ||
    fail "$audit_kind 无法安全创建发布审计 JSON"
  [ "$(find "$audit_positive_root/.local/release-runs" -mindepth 1 -maxdepth 1 -type f | wc -l | tr -d ' ')" = "1" ] ||
    fail "$audit_kind 必须且只能创建一个普通审计文件"
  if find "$audit_positive_root/.local/release-runs" -mindepth 1 -maxdepth 1 -type l -print -quit | grep -q .; then
    fail "$audit_kind 不得把审计记录创建为符号链接"
  fi
  grep -q '"status":"' "$audit_positive_root/.local/release-runs/"*.json ||
    fail "$audit_kind 创建的审计 JSON 缺少状态"
done

git -C "$fixture" remote set-url origin git@github.com:tapstate/agentic-ops.git

# 发布生产代码必须看到并校验官方 origin。离线 fixture 只在实际远端传输的
# 单个 Git 进程中注入临时映射，不能把 insteadOf 持久化到仓库或 HOME。
git_wrapper_dir="$test_root/git-wrapper"
mkdir -p "$git_wrapper_dir"
cat > "$git_wrapper_dir/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

real_git="${AGENTIC_OPS_TEST_REAL_GIT:?}"
official_url="${AGENTIC_OPS_TEST_OFFICIAL_URL:?}"
fixture_remote="${AGENTIC_OPS_TEST_FIXTURE_REMOTE:?}"
command_name=""
skip_next="false"
for argument in "$@"; do
  if [ "$skip_next" = "true" ]; then
    skip_next="false"
    continue
  fi
  case "$argument" in
    -C|-c|--git-dir|--work-tree|--namespace)
      skip_next="true"
      ;;
    --git-dir=*|--work-tree=*|--namespace=*|--literal-pathspecs|--no-optional-locks)
      ;;
    -*)
      ;;
    *)
      command_name="$argument"
      break
      ;;
  esac
done

case "$command_name" in
  fetch|push)
    exec "$real_git" -c "url.$fixture_remote.insteadOf=$official_url" "$@"
    ;;
  ls-remote)
    for argument in "$@"; do
      if [ "$argument" = "--get-url" ]; then
        exec "$real_git" "$@"
      fi
    done
    exec "$real_git" -c "url.$fixture_remote.insteadOf=$official_url" "$@"
    ;;
  *)
    exec "$real_git" "$@"
    ;;
esac
EOF
chmod 0755 "$git_wrapper_dir/git"
export AGENTIC_OPS_TEST_REAL_GIT="$real_git"
export AGENTIC_OPS_TEST_OFFICIAL_URL="git@github.com:tapstate/agentic-ops.git"
export AGENTIC_OPS_TEST_FIXTURE_REMOTE="$remote"
export PATH="$git_wrapper_dir:$PATH"

# 新提交后必须撤销旧批准；只把该远端字段漂移为 false 时，硬门禁检查
# 必须失败，不能误认为 Ruleset 仍可信。
printf 'active\tbranch\ttrue\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\tfalse\ttrue\ttrue\n' \
  > "$ruleset_status"
if (
  . "$fixture/maintainer/scripts/lib/development-workflow.sh"
  AGENTIC_OPS_GH_BIN="$fake_gh"
  workflow_check_main_ruleset tapstate/agentic-ops
); then
  fail "Ruleset dismiss_stale_reviews_on_push=false 时不得通过硬门禁"
fi
printf 'active\tbranch\ttrue\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\ttrue\ttrue\ttrue\n' \
  > "$ruleset_status"
(
  . "$fixture/maintainer/scripts/lib/development-workflow.sh"
  AGENTIC_OPS_GH_BIN="$fake_gh"
  workflow_check_main_ruleset tapstate/agentic-ops
) || fail "恢复 dismiss_stale_reviews_on_push=true 后 Ruleset 应通过"

# Ruleset 若把 main 同时放进 exclude，或 include 变成宽泛模式，实际保护范围
# 已漂移，必须失败；不能只检查 include 数组里“出现过 main”。
printf 'active\tbranch\ttrue\tfalse\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\ttrue\ttrue\ttrue\n' \
  > "$ruleset_status"
if (
  . "$fixture/maintainer/scripts/lib/development-workflow.sh"
  AGENTIC_OPS_GH_BIN="$fake_gh"
  workflow_check_main_ruleset tapstate/agentic-ops
); then
  fail "Ruleset exclude 命中 main 时不得通过硬门禁"
fi
printf 'active\tbranch\tfalse\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\ttrue\ttrue\ttrue\n' \
  > "$ruleset_status"
if (
  . "$fixture/maintainer/scripts/lib/development-workflow.sh"
  AGENTIC_OPS_GH_BIN="$fake_gh"
  workflow_check_main_ruleset tapstate/agentic-ops
); then
  fail "Ruleset include 不是精确 main 时不得通过硬门禁"
fi
printf 'active\tbranch\ttrue\ttrue\t0\ttrue\ttrue\ttrue\ttrue\t1\tfalse\ttrue\ttrue\ttrue\n' \
  > "$ruleset_status"

git -C "$fixture" config "url.$remote.insteadOf" "$AGENTIC_OPS_TEST_OFFICIAL_URL"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
    maintainer/scripts/release.sh prepare --version v9.7
) >"$test_root/persistent-rewrite.out" 2>"$test_root/persistent-rewrite.err"; then
  fail "持久化 url.*.insteadOf 必须被发布身份校验阻断"
fi
grep -q 'release_transport_rewrite_forbidden' "$test_root/persistent-rewrite.err" || {
  cat "$test_root/persistent-rewrite.err" >&2
  fail "持久化 url.*.insteadOf 未返回稳定失败码"
}
git -C "$fixture" config --unset-all "url.$remote.insteadOf"

if (
  cd "$fixture"
  AGENTIC_OPS_RELEASE_REPOSITORY="attacker/repository" \
    AGENTIC_OPS_GH_BIN="$fake_gh" \
    maintainer/scripts/release.sh prepare --version v9.7
) >"$test_root/repository-override.out" 2>"$test_root/repository-override.err"; then
  fail "AGENTIC_OPS_RELEASE_REPOSITORY 身份覆盖必须被阻断"
fi
grep -q 'release_identity_override_forbidden' "$test_root/repository-override.err" || {
  cat "$test_root/repository-override.err" >&2
  fail "AGENTIC_OPS_RELEASE_REPOSITORY 覆盖未返回稳定失败码"
}
test -z "$(git -C "$fixture" tag --list v9.7)" ||
  fail "发布身份负测不得创建版本 Tag"

if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
    maintainer/scripts/release.sh prepare --version 0.3
) >"$test_root/invalid.out" 2>"$test_root/invalid.err"; then
  fail "非法版本应被阻断"
fi
grep -q 'invalid_release_version' "$test_root/invalid.err" ||
  fail "非法版本未返回稳定失败码"

prepare_head="$(git -C "$fixture" rev-parse HEAD)"

# prepare 必须先完成固定 HEAD 的完整验证。任一检查失败时既不能报告成功，
# 也不能留下发布前 Tag。
failed_prepare_verify_log="$test_root/failed-prepare-verify.log"
: > "$failed_prepare_verify_log"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  FAKE_VERIFY_LOG="$failed_prepare_verify_log" \
  FAKE_VERIFY_FAIL_ID="maintainer-scripts-test-resources.sh" \
    maintainer/scripts/release.sh prepare --version v9.6
) >"$test_root/failed-prepare.out" 2>"$test_root/failed-prepare.err"; then
  fail "release prepare 固定验证失败时不得成功"
fi
grep -q 'release_verification_failed' "$test_root/failed-prepare.err" || {
  cat "$test_root/failed-prepare.err" >&2
  fail "release prepare 验证失败未返回稳定失败码"
}
grep -q '"failed_check":"resource_contracts"' "$test_root/failed-prepare.err" ||
  fail "release prepare 验证失败未输出精确失败项"
grep -q '"exit_code":17' "$test_root/failed-prepare.err" ||
  fail "release prepare 验证失败未输出子命令退出码"
grep -q '重新执行原命令' "$test_root/failed-prepare.err" ||
  fail "release prepare 验证失败仍错误要求固定从 publish 重试"
failed_verification_log="$(sed -n 's/.*"log_file":"\([^"]*\)".*/\1/p' "$test_root/failed-prepare.err" | tail -n 1)"
test -n "$failed_verification_log" && test -f "$failed_verification_log" ||
  fail "release prepare 验证失败未保留可读取日志"
grep -q 'fixture verification failure: maintainer-scripts-test-resources.sh' \
  "$failed_verification_log" ||
  fail "release prepare 验证日志不包含具体子测试失败"
rm -f "$failed_verification_log"
test -z "$(git -C "$fixture" tag --list v9.6)" ||
  fail "release prepare 验证失败前不得创建版本 Tag"
expected_failed_prepare="$(printf '%s\n' \
  maintainer-scripts-test-python-runtime.sh \
  maintainer-scripts-test-resources.sh)"
[ "$(cat "$failed_prepare_verify_log")" = "$expected_failed_prepare" ] ||
  fail "release prepare 未按固定顺序在首个失败处停止"

prepare_verify_log="$test_root/prepare-verify.log"
: > "$prepare_verify_log"
: > "$fake_uv_log"
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  FAKE_VERIFY_LOG="$prepare_verify_log" \
    maintainer/scripts/release.sh prepare --version v9.9
) >"$test_root/prepare.out"
grep -q '"operation":"release_prepare"' "$test_root/prepare.out" ||
  fail "release prepare 未完成"
grep -q '"delivery":"python_source_and_developer_assets"' "$test_root/prepare.out" ||
  fail "release prepare 未声明 Python 交付集合"
test -z "$(git -C "$fixture" tag --list v9.9)" ||
  fail "release prepare 不得在 main Merge commit 前创建 Tag"
expected_prepare="$(printf '%s\n' \
  maintainer-scripts-test-python-runtime.sh \
  maintainer-scripts-test-resources.sh \
  developer-tests-bootstrap-test_install_boundary.sh \
  maintainer-scripts-test-release-workflow.sh)"
[ "$(cat "$prepare_verify_log")" = "$expected_prepare" ] ||
  fail "release prepare 未执行固定完整验证或顺序错误"
[ "$(wc -l < "$fake_uv_log" | tr -d ' ')" = "2" ] ||
  fail "release prepare 必须准备两个锁定 Python Runtime"
[ -z "$(git -C "$fixture" status --porcelain)" ] ||
  fail "release prepare 不得生成构建产物"
[ ! -e "$fixture/install-resources" ] ||
  fail "release prepare 不得生成旧平台安装资源"

# 软门禁必须在 prepare 固定 release 分支；随后 develop 的提交不能替换该候选。
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  FAKE_VERIFY_LOG="$prepare_verify_log" \
    maintainer/scripts/release.sh prepare --version v9.5 --allow-soft-gate
) >"$test_root/soft-prepare.out"
[ "$(git -C "$fixture" rev-parse refs/heads/release/v9.5)" = "$prepare_head" ] ||
  fail "软门禁 prepare 未固定 release 分支到已验证 HEAD"
test -z "$(git -C "$fixture" tag --list v9.5)" ||
  fail "软门禁 prepare 不得提前创建 Tag"

# 模拟 prepare 后经审查提交的发布变更，使本地 develop 明确领先远端；
# 后续即可通过远端引用判断最终确认前是否发生了 push。
printf 'pending publish\n' >> "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -m "pending release fixture" >/dev/null

# 模拟候选提交通过 --no-verify 把自己的 gate 改成无条件成功。publish 必须
# 仍执行 origin/main 快照中的基线 gate，不能执行这个 candidate launcher。
cat > "$fixture/maintainer/bin/ao-maint" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
git -C "$fixture" add maintainer/bin/ao-maint
git -C "$fixture" -c core.hooksPath=/dev/null commit \
  -m "malicious candidate gate always succeeds" >/dev/null
publish_head="$(git -C "$fixture" rev-parse HEAD)"
remote_develop_before_publish="$(git --git-dir="$remote" rev-parse refs/heads/develop)"
remote_main_before_publish="$(git --git-dir="$remote" rev-parse refs/heads/main)"
story_gate_range_base="$(git -C "$fixture" merge-base "$remote_main_before_publish" "$publish_head")"
[ "$publish_head" != "$remote_develop_before_publish" ] ||
  fail "确认门禁测试需要本地 develop 领先远端"

if rg -n 'release_require_command[[:space:]]+go|release_build_assets|scripts/build\.sh' \
  "$repo_root/maintainer/scripts/release.sh" \
  "$repo_root/maintainer/scripts/hotfix.sh" \
  "$repo_root/maintainer/scripts/lib/release-common.sh"; then
  fail "发布入口仍依赖 Go 构建"
fi

verify_log="$test_root/verify.log"
: > "$verify_log"
: > "$fake_uv_log"
(
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  FAKE_VERIFY_LOG="$verify_log" \
    release_run_full_verification "$fixture" "$prepare_head"
)
expected="$(printf '%s\n' \
  maintainer-scripts-test-python-runtime.sh \
  maintainer-scripts-test-resources.sh \
  developer-tests-bootstrap-test_install_boundary.sh \
  maintainer-scripts-test-release-workflow.sh)"
[ "$(cat "$verify_log")" = "$expected" ] ||
  fail "完整验证清单或顺序不正确"
[ "$(wc -l < "$fake_uv_log" | tr -d ' ')" = "2" ] ||
  fail "完整验证必须分别准备 maintainer 与 developer 锁定 Runtime"
grep -qx 'maintainer' "$fake_uv_log" ||
  fail "完整验证未准备 maintainer Runtime"
grep -qx 'developer' "$fake_uv_log" ||
  fail "完整验证未准备 developer Runtime"

: > "$verify_log"
: > "$fake_uv_log"
(
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
    FAKE_VERIFY_LOG="$verify_log" \
    release_run_full_verification "$fixture" "$prepare_head"
)
expected_without_recursive="$(printf '%s\n' \
  maintainer-scripts-test-python-runtime.sh \
  maintainer-scripts-test-resources.sh \
  developer-tests-bootstrap-test_install_boundary.sh)"
[ "$(cat "$verify_log")" = "$expected_without_recursive" ] ||
  fail "发布工作流递归保护无效"
[ "$(wc -l < "$fake_uv_log" | tr -d ' ')" = "2" ] ||
  fail "递归保护不得跳过两个锁定 Runtime 的准备"

verify_log="$test_root/publish-verify.log"
: > "$verify_log"
: > "$story_gate_log"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
  FAKE_STORY_GATE_DENY=1 \
    maintainer/scripts/release.sh publish --version v9.9
) >"$test_root/blocked-story-gate.out" 2>"$test_root/blocked-story-gate.err"; then
  fail "release publish 必须在故事门禁拒绝时停止"
fi
grep -q 'release_story_gate_blocked' "$test_root/blocked-story-gate.err" ||
  fail "release publish 未返回故事门禁稳定失败码"
grep -Eq "^--source-root .+/candidate story impact --change-source range --base $story_gate_range_base --head $publish_head$" \
  "$story_gate_log" ||
  fail "release publish 未用 origin/main 基线 Runtime 校验固定 candidate 快照"
[ ! -s "$verify_log" ] ||
  fail "故事门禁失败后不得进入完整验证"
[ "$(git --git-dir="$remote" rev-parse refs/heads/develop)" = "$remote_develop_before_publish" ] ||
  fail "故事门禁失败后不得推送 develop"
test -z "$(git --git-dir="$remote" show-ref --tags v9.9 || true)" ||
  fail "故事门禁失败后不得推送版本 Tag"

# 恢复 candidate launcher 后，主线发布可以继续由 origin/main 基线检查。
# 净 diff 不再包含 launcher，证明后续成功不是候选 gate 自证。
git -C "$fixture" show \
  "$remote_main_before_publish:maintainer/bin/ao-maint" \
  > "$fixture/maintainer/bin/ao-maint"
chmod 0755 "$fixture/maintainer/bin/ao-maint"
git -C "$fixture" add maintainer/bin/ao-maint
git -C "$fixture" -c core.hooksPath=/dev/null commit \
  -m "restore candidate gate after trust test" >/dev/null

# 即使可信基线本身接受影响与证据，修改 Hook / gate / release 信任根也不得
# 走自动发布，必须独立走受保护 main 的人工审查 PR。
printf '\n# candidate trust-root change\n' >> "$fixture/.githooks/pre-push"
git -C "$fixture" add .githooks/pre-push
git -C "$fixture" -c core.hooksPath=/dev/null commit \
  -m "candidate modifies trusted hook source" >/dev/null
trust_root_head="$(git -C "$fixture" rev-parse HEAD)"
: > "$verify_log"
: > "$story_gate_log"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh publish --version v9.9
) >"$test_root/trust-root-change.out" 2>"$test_root/trust-root-change.err"; then
  fail "修改门禁信任根时不得自动发布"
fi
grep -q 'release_story_gate_trust_root_changed' \
  "$test_root/trust-root-change.err" || {
  cat "$test_root/trust-root-change.err" >&2
  fail "门禁信任根变更未返回独立人工审查失败码"
}
grep -Eq "^--source-root .+/candidate story impact --change-source range --base $story_gate_range_base --head $trust_root_head$" \
  "$story_gate_log" ||
  fail "信任根变更仍必须先由 origin/main 基线门禁检查"
[ ! -s "$verify_log" ] ||
  fail "信任根变更被拒绝后不得进入候选完整验证"

git -C "$fixture" show \
  "$remote_main_before_publish:.githooks/pre-push" \
  > "$fixture/.githooks/pre-push"
chmod 0755 "$fixture/.githooks/pre-push"
git -C "$fixture" add .githooks/pre-push
git -C "$fixture" -c core.hooksPath=/dev/null commit \
  -m "restore trusted hook source after manual-review test" >/dev/null
publish_head="$(git -C "$fixture" rev-parse HEAD)"

# 在软门禁下 publish 只能消费 prepare 固定的 release 分支，不能从执行时的
# develop HEAD 临时创建候选。
git -C "$fixture" branch release/v9.9 "$publish_head"

: > "$verify_log"
: > "$story_gate_log"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh publish --version v9.9
) >"$test_root/unconfirmed-publish.out" 2>"$test_root/unconfirmed-publish.err"; then
  fail "非交互 release publish 缺少最终确认时必须阻断"
fi
grep -q 'release_confirmation_required' "$test_root/unconfirmed-publish.err" ||
  fail "release publish 未返回最终确认失败码"
grep -Eq "^--source-root .+/candidate story impact --change-source range --base $story_gate_range_base --head $publish_head$" \
  "$story_gate_log" ||
  fail "release publish 未在完整验证前执行可信基线故事门禁"
[ "$(wc -l < "$story_gate_log" | tr -d ' ')" = "1" ] ||
  fail "单次 release publish 应只执行一次故事门禁"
[ "$(git --git-dir="$remote" rev-parse refs/heads/develop)" = "$remote_develop_before_publish" ] ||
  fail "最终确认前不得推送 develop"
[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" = "$remote_main_before_publish" ] ||
  fail "release publish 不得直接修改受保护 main"
test -z "$(git --git-dir="$remote" show-ref --tags v9.9 || true)" ||
  fail "最终确认前不得推送版本 Tag"

if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$fake_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh publish --version v9.9 --allow-soft-gate
) >"$test_root/unconfirmed-soft-publish.out" 2>"$test_root/unconfirmed-soft-publish.err"; then
  fail "soft release publish 也必须取得最终确认"
fi
grep -q 'release_confirmation_required' "$test_root/unconfirmed-soft-publish.err" ||
  fail "soft release publish 未保留最终确认门禁"
test -z "$(git --git-dir="$remote" show-ref --heads refs/heads/release/v9.9 || true)" ||
  fail "最终确认前不得推送固定 release 分支"
[ "$(git --git-dir="$remote" rev-parse refs/heads/develop)" = "$remote_develop_before_publish" ] ||
  fail "soft release 最终确认前不得推送 develop"
[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" = "$remote_main_before_publish" ] ||
  fail "soft release 不得直接修改受保护 main"
test -z "$(git --git-dir="$remote" show-ref --tags v9.9 || true)" ||
  fail "soft release 最终确认前不得推送版本 Tag"

if (
  cd "$fixture"
  maintainer/scripts/hotfix.sh ao-11
) >"$test_root/invalid-hotfix.out" 2>"$test_root/invalid-hotfix.err"; then
  fail "Hotfix 必须拒绝非法 Jira 编号"
fi
grep -q 'invalid_jira_id' "$test_root/invalid-hotfix.err" ||
  fail "Hotfix 未返回 Jira 编号失败码"
if (
  cd "$fixture"
  maintainer/scripts/hotfix.sh publish --jira-id AO-11
) >"$test_root/legacy-hotfix-args.out" 2>"$test_root/legacy-hotfix-args.err"; then
  fail "Hotfix 必须拒绝旧 publish --jira-id 参数"
fi
grep -q 'invalid_hotfix_arguments' "$test_root/legacy-hotfix-args.err" ||
  fail "Hotfix 旧参数未返回稳定失败码"

# Hotfix 使用独立仓库验证 develop 到 main 的直合，不创建修复分支、PR、Tag，
# 也不调用 gh 或 Jira。必要 Git 一致性检查通过后直接原子更新两个远端分支。
hotfix_remote="$test_root/hotfix-remote.git"
hotfix_fixture="$test_root/hotfix-repo"
git init --bare "$hotfix_remote" >/dev/null
git clone "$hotfix_remote" "$hotfix_fixture" >/dev/null 2>&1
git -C "$hotfix_fixture" config user.email agentic-ops-test@example.test
git -C "$hotfix_fixture" config user.name "AgenticOps Test"
mkdir -p "$hotfix_fixture/.githooks" "$hotfix_fixture/maintainer/scripts/lib"
cp "$repo_root/.githooks/pre-push" "$hotfix_fixture/.githooks/pre-push"
cp "$repo_root/maintainer/scripts/hotfix.sh" "$hotfix_fixture/maintainer/scripts/hotfix.sh"
cp "$repo_root/maintainer/scripts/lib/release-common.sh" \
  "$hotfix_fixture/maintainer/scripts/lib/release-common.sh"
printf 'maintainer\n' > "$hotfix_fixture/.agentic-ops-source"
printf 'hotfix baseline\n' > "$hotfix_fixture/README.md"
chmod 0755 "$hotfix_fixture/.githooks/pre-push" "$hotfix_fixture/maintainer/scripts/hotfix.sh"
git -C "$hotfix_fixture" add .
git -C "$hotfix_fixture" commit -m "hotfix baseline" >/dev/null
git -C "$hotfix_fixture" branch -M main
git -C "$hotfix_fixture" push -u origin main >/dev/null
git -C "$hotfix_fixture" switch -c develop main >/dev/null
printf 'urgent fix\n' >> "$hotfix_fixture/README.md"
git -C "$hotfix_fixture" add README.md
git -C "$hotfix_fixture" commit -m "urgent fix" >/dev/null
hotfix_candidate="$(git -C "$hotfix_fixture" rev-parse HEAD)"
git -C "$hotfix_fixture" push -u origin develop >/dev/null
git -C "$hotfix_fixture" remote set-url origin "$AGENTIC_OPS_TEST_OFFICIAL_URL"
export AGENTIC_OPS_TEST_FIXTURE_REMOTE="$hotfix_remote"

printf 'dirty\n' >> "$hotfix_fixture/README.md"
if (
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-74
) >"$test_root/hotfix-dirty.out" 2>"$test_root/hotfix-dirty.err"; then
  fail "Hotfix 必须拒绝脏工作区"
fi
grep -q 'dirty_worktree' "$test_root/hotfix-dirty.err" ||
  fail "Hotfix 脏工作区未返回稳定失败码"
git -C "$hotfix_fixture" restore README.md

# 用户可从其它干净分支直接调用；脚本必须自行切换 develop、完成合并和同步。
git -C "$hotfix_fixture" switch main >/dev/null
(
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-74
) >"$test_root/hotfix-publish.out"
grep -q '"operation":"hotfix_publish"' "$test_root/hotfix-publish.out" ||
  fail "Hotfix 未完成 develop 到 main 的直合"
grep -q '"jira_interaction":false' "$test_root/hotfix-publish.out" ||
  fail "Hotfix 未声明不与 Jira 交互"
grep -q '"branch_created":false' "$test_root/hotfix-publish.out" ||
  fail "Hotfix 错误声明创建了分支"
hotfix_merge="$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)"
[ "$hotfix_merge" = "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/develop)" ] ||
  fail "Hotfix 未原子同步远端 main 与 develop"
[ "$(git -C "$hotfix_fixture" rev-parse develop)" = "$hotfix_merge" ] ||
  fail "Hotfix 未同步本地 develop"
[ "$(git -C "$hotfix_fixture" branch --show-current)" = "develop" ] ||
  fail "Hotfix 未自动切换到 develop"
[ "$(git -C "$hotfix_fixture" rev-parse "$hotfix_merge^2")" = "$hotfix_candidate" ] ||
  fail "Hotfix Merge commit 的第二父提交不是固定 develop 候选"
git -C "$hotfix_fixture" log -1 --format=%B "$hotfix_merge" |
  grep -q 'AO-74' || fail "Hotfix Merge commit 未写入 Jira 编号"
test -z "$(git -C "$hotfix_fixture" branch --list '*fix-main*')" ||
  fail "Hotfix 不得创建修复分支"
test -z "$(git -C "$hotfix_fixture" tag --list)" ||
  fail "Hotfix 不得创建或移动 Tag"

# 本地 develop 领先远端时，脚本必须把本地已提交变更直接纳入同一次原子
# main/develop 更新，不要求用户先单独 push。
printf 'local only\n' >> "$hotfix_fixture/README.md"
git -C "$hotfix_fixture" add README.md
git -C "$hotfix_fixture" commit -m "local only hotfix candidate" >/dev/null
local_hotfix_candidate="$(git -C "$hotfix_fixture" rev-parse HEAD)"
(
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-75
) >"$test_root/hotfix-local-ahead.out"
hotfix_merge="$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)"
[ "$hotfix_merge" = "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/develop)" ] ||
  fail "Hotfix 未原子推送本地领先的 develop"
[ "$(git --git-dir="$hotfix_remote" rev-parse "$hotfix_merge^2")" = "$local_hotfix_candidate" ] ||
  fail "Hotfix Merge commit 未包含本地 develop 已提交变更"

# 本地 develop 落后远端时，脚本必须先自动快进，再把远端新提交合入 main；
# 用户不需要提前执行 pull。
printf 'remote only\n' >> "$hotfix_fixture/README.md"
git -C "$hotfix_fixture" add README.md
git -C "$hotfix_fixture" commit -m "remote only hotfix candidate" >/dev/null
remote_hotfix_candidate="$(git -C "$hotfix_fixture" rev-parse HEAD)"
git -C "$hotfix_fixture" -c core.hooksPath=/dev/null push origin develop >/dev/null
git -C "$hotfix_fixture" reset --hard "$hotfix_merge" >/dev/null
(
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-77
) >"$test_root/hotfix-local-behind.out"
hotfix_merge="$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)"
[ "$hotfix_merge" = "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/develop)" ] ||
  fail "Hotfix 未在本地落后时原子同步 main/develop"
[ "$(git --git-dir="$hotfix_remote" rev-parse "$hotfix_merge^2")" = "$remote_hotfix_candidate" ] ||
  fail "Hotfix 未自动快进并包含远端 develop 新提交"
[ "$(git -C "$hotfix_fixture" rev-parse develop)" = "$hotfix_merge" ] ||
  fail "Hotfix 未在本地落后场景同步本地 develop"

# 已同步状态再次执行必须幂等返回，不创建新的 Merge commit。
(
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-77
) >"$test_root/hotfix-idempotent.out"
grep -q '"changed":false' "$test_root/hotfix-idempotent.out" ||
  fail "Hotfix 重复执行未按已同步状态幂等完成"

# main 独立前进后，Hotfix 仍应生成保留双方内容的双父 Merge commit，并原子
# 同步 main/develop；不能丢失 main 独有提交或只采用 develop 的 tree。
git -C "$hotfix_fixture" branch -f main "$hotfix_merge"
git -C "$hotfix_fixture" switch main >/dev/null
printf 'independent main\n' > "$hotfix_fixture/MAIN-ONLY.md"
git -C "$hotfix_fixture" add MAIN-ONLY.md
git -C "$hotfix_fixture" commit -m "independent main after hotfix" >/dev/null
diverged_main="$(git -C "$hotfix_fixture" rev-parse HEAD)"
AGENTIC_OPS_SPECIAL_PUSH=hotfix git -C "$hotfix_fixture" push origin main >/dev/null
git -C "$hotfix_fixture" switch develop >/dev/null
(
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-76
) >"$test_root/hotfix-diverged.out"
diverged_merge="$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)"
[ "$diverged_merge" = "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/develop)" ] ||
  fail "Hotfix 未原子同步已分叉的 main/develop"
[ "$(git --git-dir="$hotfix_remote" rev-parse "$diverged_merge^1")" = "$diverged_main" ] ||
  fail "Hotfix 分叉合并的第一父提交不是固定 main"
[ "$(git --git-dir="$hotfix_remote" rev-parse "$diverged_merge^2")" = "$hotfix_merge" ] ||
  fail "Hotfix 分叉合并的第二父提交不是固定 develop"
git --git-dir="$hotfix_remote" cat-file -e "$diverged_merge:MAIN-ONLY.md" ||
  fail "Hotfix 分叉合并丢失 main 独有内容"
git --git-dir="$hotfix_remote" cat-file -e "$diverged_merge:README.md" ||
  fail "Hotfix 分叉合并丢失 develop 内容"

# 本地与远端 develop 各自独立前进时属于真实分叉，脚本必须停止且不改写
# 任一远端引用。
printf 'local divergence\n' > "$hotfix_fixture/LOCAL-DIVERGENCE.md"
git -C "$hotfix_fixture" add LOCAL-DIVERGENCE.md
git -C "$hotfix_fixture" commit -m "local develop divergence" >/dev/null
local_diverged_candidate="$(git -C "$hotfix_fixture" rev-parse HEAD)"
git -C "$hotfix_fixture" reset --hard "$diverged_merge" >/dev/null
printf 'remote divergence\n' > "$hotfix_fixture/REMOTE-DIVERGENCE.md"
git -C "$hotfix_fixture" add REMOTE-DIVERGENCE.md
git -C "$hotfix_fixture" commit -m "remote develop divergence" >/dev/null
remote_diverged_candidate="$(git -C "$hotfix_fixture" rev-parse HEAD)"
git -C "$hotfix_fixture" -c core.hooksPath=/dev/null push origin develop >/dev/null
git -C "$hotfix_fixture" reset --hard "$local_diverged_candidate" >/dev/null
if (
  cd "$hotfix_fixture"
  maintainer/scripts/hotfix.sh AO-78
) >"$test_root/hotfix-develop-diverged.out" 2>"$test_root/hotfix-develop-diverged.err"; then
  fail "Hotfix 必须拒绝本地与远端 develop 真实分叉"
fi
grep -q 'hotfix_develop_diverged' "$test_root/hotfix-develop-diverged.err" ||
  fail "Hotfix develop 分叉未返回稳定失败码"
[ "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/main)" = "$diverged_merge" ] ||
  fail "Hotfix develop 分叉失败错误改写了 main"
[ "$(git --git-dir="$hotfix_remote" rev-parse refs/heads/develop)" = "$remote_diverged_candidate" ] ||
  fail "Hotfix develop 分叉失败错误改写了 develop"
export AGENTIC_OPS_TEST_FIXTURE_REMOTE="$remote"

# 已提前合入 main 的候选必须由 inspect 自动解析真实 PR，并通过两阶段
# recover 绑定完整确认包；确认前不得改变本地或远端 Tag。
git -C "$fixture" switch develop >/dev/null
recovery_candidate="$(git -C "$fixture" rev-parse HEAD)"
git -C "$fixture" switch main >/dev/null
git -C "$fixture" -c core.hooksPath=/dev/null merge --no-ff develop \
  -m "merge recovery candidate" >/dev/null
recovery_merge="$(git -C "$fixture" rev-parse HEAD)"
git -C "$fixture" branch -f develop "$recovery_merge"
git --git-dir="$remote" fetch "$fixture" \
  "$recovery_merge:refs/heads/main" >/dev/null
git --git-dir="$remote" update-ref refs/heads/develop "$recovery_merge"
git -C "$fixture" switch develop >/dev/null
git -C "$fixture" branch release/v9.7 "$recovery_merge"
git --git-dir="$remote" update-ref refs/heads/release/v9.7 "$recovery_merge"
git -C "$fixture" tag -a v9.7 "$recovery_merge" -m "wrong recovery tag"

recovery_gh="$test_root/recovery-gh"
cat > "$recovery_gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "pr" ] && { [ "${2:-}" = "list" ] || [ "${2:-}" = "view" ]; }; then
  printf '%s\t%s\tMERGED\tmain\t%s\t%s\n' \
    "${FAKE_RECOVERY_PR_NUMBER:?}" "${FAKE_RECOVERY_PR_URL:?}" \
    "${FAKE_RECOVERY_PR_HEAD:?}" "${FAKE_RECOVERY_MERGE_COMMIT:?}"
  exit 0
fi
exec "${FAKE_RECOVERY_BASE_GH:?}" "$@"
EOF
chmod 0755 "$recovery_gh"
export FAKE_RECOVERY_PR_NUMBER=77
export FAKE_RECOVERY_PR_URL=https://example.test/pull/77
export FAKE_RECOVERY_PR_HEAD="$recovery_candidate"
export FAKE_RECOVERY_MERGE_COMMIT="$recovery_merge"
export FAKE_RECOVERY_BASE_GH="$fake_gh"

failing_pr_gh="$test_root/failing-pr-gh"
cat > "$failing_pr_gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "list" ]; then
  printf 'simulated GitHub PR read failure\n' >&2
  exit 23
fi
exec "${FAKE_RECOVERY_BASE_GH:?}" "$@"
EOF
chmod 0755 "$failing_pr_gh"

if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$failing_pr_gh" \
    maintainer/scripts/release.sh inspect --version v9.7 --allow-soft-gate
) >"$test_root/recovery-pr-read-failed.out" 2>"$test_root/recovery-pr-read-failed.err"; then
  fail "inspect 在 merged PR 查询失败时不得报告成功"
fi
grep -q 'release_pr_state_read_failed' "$test_root/recovery-pr-read-failed.err" ||
  fail "inspect 未区分 merged PR 查询失败与 PR 缺失"
if grep -q 'release_merged_pr_missing' "$test_root/recovery-pr-read-failed.out"; then
  fail "inspect 把 merged PR 查询失败误报为 PR 缺失"
fi

(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
    maintainer/scripts/release.sh inspect --version v9.7 --allow-soft-gate
) >"$test_root/recovery-inspect.out"
grep -q '"state":"release_local_tag_repair_required"' "$test_root/recovery-inspect.out" ||
  fail "inspect 未识别提前合入和错误本地 Tag"
grep -q '"pr_number":"77"' "$test_root/recovery-inspect.out" ||
  fail "inspect 未输出自动解析的关联 PR"
grep -q -- '--merged-pr 77' "$test_root/recovery-inspect.out" ||
  fail "inspect 仍输出需要用户猜测的 PR 占位符"

if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh prepare --version v9.7 --allow-soft-gate
) >"$test_root/already-main-prepare.out" 2>"$test_root/already-main-prepare.err"; then
  fail "prepare 不得重新处理已经位于 main 的候选"
fi
grep -q 'release_candidate_already_in_main' "$test_root/already-main-prepare.err" ||
  fail "prepare 未对已进入 main 的候选返回准确状态"
grep -q '尚未执行当前动作所需的固定完整验证' "$test_root/already-main-prepare.err" ||
  fail "prepare 在验证前错误宣称固定完整验证已通过"

if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh publish --version v9.7 \
      --allow-soft-gate --confirm-release
) >"$test_root/already-main-publish.out" 2>"$test_root/already-main-publish.err"; then
  fail "publish 不得为已经位于 main 的候选创建无差异 PR"
fi
grep -q 'release_candidate_already_in_main' "$test_root/already-main-publish.err" ||
  fail "publish 未在 GitHub PR 创建前返回准确状态"

wrong_tag_ref="$(git -C "$fixture" rev-parse refs/tags/v9.7)"
: > "$verify_log"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh recover --version v9.7 --merged-pr 77 --allow-soft-gate
) >"$test_root/recovery-plan.out" 2>"$test_root/recovery-plan.err"; then
  fail "recover 首次执行必须停在完整确认包"
fi
grep -q 'release_recovery_confirmation_required' "$test_root/recovery-plan.err" ||
  { cat "$test_root/recovery-plan.err" >&2; fail "recover 未返回两阶段确认失败码"; }
for item in '动作：' '目标：' '事实引用：' '验证：' '风险：' '不执行：' '后续门禁：'; do
  grep -q "$item" "$test_root/recovery-plan.err" ||
    fail "recover 确认包缺少：$item"
done
grep -q '远端 Tag=' "$test_root/recovery-plan.err" ||
  fail "recover 确认包缺少远端 Tag 事实引用"
test "$(git -C "$fixture" rev-parse refs/tags/v9.7)" = "$wrong_tag_ref" ||
  fail "确认前 recover 修改了本地 Tag"
test -z "$(git --git-dir="$remote" show-ref --tags v9.7 || true)" ||
  fail "确认前 recover 推送了远端 Tag"

recovery_digest="$(sed -n 's/.*--confirm-recovery \([0-9a-f][0-9a-f]*\).*/\1/p' "$test_root/recovery-plan.err" | tail -n 1)"
test -n "$recovery_digest" || fail "recover 未输出绑定确认包的完整继续命令"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh recover --version v9.7 --merged-pr 77 \
      --allow-soft-gate --confirm-release --confirm-recovery deadbeef
) >"$test_root/recovery-stale.out" 2>"$test_root/recovery-stale.err"; then
  fail "recover 不得接受未绑定当前确认包的确认值"
fi
grep -q 'release_recovery_confirmation_stale' "$test_root/recovery-stale.err" ||
  fail "recover 未拒绝过期或伪造的确认绑定"
test "$(git -C "$fixture" rev-parse refs/tags/v9.7)" = "$wrong_tag_ref" ||
  fail "无效确认修改了本地 Tag"
test -z "$(git --git-dir="$remote" show-ref --tags v9.7 || true)" ||
  fail "无效确认推送了远端 Tag"
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh recover --version v9.7 --merged-pr 77 \
      --allow-soft-gate --confirm-release --confirm-recovery "$recovery_digest"
) >"$test_root/recovery-complete.out" 2>"$test_root/recovery-complete.err"
test "$(git -C "$fixture" rev-list -n 1 v9.7)" = "$recovery_merge" ||
  fail "recover 未把本地 Tag 原子修复到 main Merge commit"
test "$(git --git-dir="$remote" rev-list -n 1 v9.7)" = "$recovery_merge" ||
  fail "recover 未推送正确的不可变远端 Tag"
grep -q '"operation":"release_recover"' "$test_root/recovery-complete.out" ||
  fail "recover 成功结果缺失"

# 正常发布与 recover 都必须把版本 Tag 放在已核验的 main Merge commit；
# 任何预先指向候选 HEAD 的本地 Tag 都不能被脚本静默移动。
(
  . "$fixture/maintainer/scripts/lib/release-common.sh"
  release_create_and_push_version_tag "$fixture" v9.4 "$recovery_merge"
)
test "$(git -C "$fixture" rev-list -n 1 v9.4)" = "$recovery_merge" ||
  fail "正常发布未在 main Merge commit 创建本地 Tag"
test "$(git --git-dir="$remote" rev-list -n 1 v9.4)" = "$recovery_merge" ||
  fail "正常发布未推送指向 main Merge commit 的 Tag"
git -C "$fixture" tag -a v9.3 "$recovery_candidate" -m "legacy pre-merge tag"
if (
  . "$fixture/maintainer/scripts/lib/release-common.sh"
  release_create_and_push_version_tag "$fixture" v9.3 "$recovery_merge"
) >"$test_root/local-tag-conflict.out" 2>"$test_root/local-tag-conflict.err"; then
  fail "正常发布不得静默移动预先存在的本地 Tag"
fi
grep -q 'release_local_tag_conflict' "$test_root/local-tag-conflict.err" ||
  fail "预先存在的本地 Tag 未返回稳定冲突码"
test -z "$(git --git-dir="$remote" show-ref --tags v9.3 || true)" ||
  fail "本地 Tag 冲突时不得推送远端 Tag"
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
    maintainer/scripts/release.sh inspect --version v9.7 --allow-soft-gate
) >"$test_root/recovery-completed-inspect.out"
grep -q '"state":"release_completed"' "$test_root/recovery-completed-inspect.out" ||
  fail "inspect 未识别已经完成的恢复发布"

# 远端 Tag 已正确时，本地 Tag 漂移仍必须可诊断并幂等修复；不得改写远端 Tag。
remote_tag_ref_before="$(git --git-dir="$remote" rev-parse refs/tags/v9.7)"
git -C "$fixture" tag -f -a v9.7 "$recovery_candidate" -m "drifted local recovery tag" >/dev/null
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
    maintainer/scripts/release.sh inspect --version v9.7 --allow-soft-gate
) >"$test_root/recovery-remote-tag-local-drift-inspect.out"
grep -q '"state":"release_local_tag_repair_required"' \
  "$test_root/recovery-remote-tag-local-drift-inspect.out" ||
  fail "inspect 未识别远端 Tag 正确但本地 Tag 漂移"
if (
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh recover --version v9.7 --merged-pr 77 --allow-soft-gate
) >"$test_root/recovery-idempotent-plan.out" 2>"$test_root/recovery-idempotent-plan.err"; then
  fail "远端 Tag 已正确时 recover 仍必须先展示确认包"
fi
idempotent_digest="$(sed -n 's/.*--confirm-recovery \([0-9a-f][0-9a-f]*\).*/\1/p' "$test_root/recovery-idempotent-plan.err" | tail -n 1)"
test -n "$idempotent_digest" || fail "幂等恢复未输出确认绑定"
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$recovery_gh" \
  AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING=1 \
  FAKE_VERIFY_LOG="$verify_log" \
    maintainer/scripts/release.sh recover --version v9.7 --merged-pr 77 \
      --allow-soft-gate --confirm-release --confirm-recovery "$idempotent_digest"
) >"$test_root/recovery-idempotent-complete.out" 2>"$test_root/recovery-idempotent-complete.err"
test "$(git -C "$fixture" rev-list -n 1 v9.7)" = "$recovery_merge" ||
  fail "远端 Tag 已正确时 recover 未修复本地 Tag"
test "$(git --git-dir="$remote" rev-parse refs/tags/v9.7)" = "$remote_tag_ref_before" ||
  fail "幂等恢复改写了正确的远端 Tag"

# 发布 PR 已创建但尚未人工合并时，inspect 必须返回等待态和同一发布继续命令。
printf 'waiting release candidate\n' >> "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" -c core.hooksPath=/dev/null commit -m "waiting release candidate" >/dev/null
waiting_candidate="$(git -C "$fixture" rev-parse HEAD)"
git --git-dir="$remote" fetch "$fixture" \
  "$waiting_candidate:refs/heads/develop" \
  "$waiting_candidate:refs/heads/release/v9.6" >/dev/null
git -C "$fixture" branch release/v9.6 "$waiting_candidate"
git -C "$fixture" tag -a v9.6 "$waiting_candidate" -m "waiting release tag"
waiting_gh="$test_root/waiting-gh"
cat > "$waiting_gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "list" ]; then
  printf '88\t%s\tOPEN\tmain\t%s\t\n' \
    "${FAKE_WAITING_PR_URL:?}" "${FAKE_WAITING_PR_HEAD:?}"
  exit 0
fi
exec "${FAKE_WAITING_BASE_GH:?}" "$@"
EOF
chmod 0755 "$waiting_gh"
export FAKE_WAITING_PR_URL=https://example.test/pull/88
export FAKE_WAITING_PR_HEAD="$waiting_candidate"
export FAKE_WAITING_BASE_GH="$fake_gh"
(
  cd "$fixture"
  AGENTIC_OPS_GH_BIN="$waiting_gh" \
    maintainer/scripts/release.sh inspect --version v9.6 --allow-soft-gate
) >"$test_root/release-waiting-inspect.out"
grep -q '"state":"release_waiting_manual_merge"' "$test_root/release-waiting-inspect.out" ||
  fail "inspect 未识别等待人工合并的发布 PR"
grep -q '"pr_number":"88"' "$test_root/release-waiting-inspect.out" ||
  fail "inspect 等待态缺少精确 PR 编号"
grep -q 'release.sh publish --version v9.6 --allow-soft-gate' \
  "$test_root/release-waiting-inspect.out" ||
  fail "inspect 等待态未给出同一 publish 继续命令"

# 软门禁普通发布创建 PR 后应每 5 秒轮询；Merge commit 仍必须由人工在
# GitHub 完成，脚本只在观察到该事实后自动继续。替换 sleep 以避免测试等待。
polling_gh="$test_root/polling-gh"
polling_bin="$test_root/polling-bin"
mkdir -p "$polling_bin"
cat > "$polling_gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  if [[ "$*" == *'--json state,mergeCommit,url,number,headRefOid'* ]]; then
    poll_count=0
    if [ -f "${FAKE_POLL_COUNT_FILE:?}" ]; then
      poll_count="$(cat "${FAKE_POLL_COUNT_FILE}")"
    fi
    poll_count=$((poll_count + 1))
    printf '%s\n' "$poll_count" > "${FAKE_POLL_COUNT_FILE}"
    if [ "$poll_count" -eq 1 ]; then
      printf 'OPEN\t-\t%s\t91\t%s\n' "${FAKE_POLL_PR_URL:?}" "${FAKE_POLL_HEAD:?}"
    else
      printf 'MERGED\t%s\t%s\t91\t%s\n' "${FAKE_POLL_MERGE_COMMIT:?}" "${FAKE_POLL_PR_URL:?}" "${FAKE_POLL_HEAD:?}"
    fi
    exit 0
  fi
  if [[ "$*" == *'--json body'* ]]; then
    printf '%s\n' "<!-- agentic-ops-fixed-head:${FAKE_POLL_HEAD:?} -->"
    exit 0
  fi
fi
printf 'unsupported polling gh command: %s\n' "$*" >&2
exit 2
EOF
chmod 0755 "$polling_gh"
cat > "$polling_bin/sleep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" = "5" ] || exit 3
printf '%s\n' "$1" >> "${FAKE_POLL_SLEEP_LOG:?}"
EOF
chmod 0755 "$polling_bin/sleep"
poll_count_file="$test_root/poll-count"
poll_sleep_log="$test_root/poll-sleep.log"
polling_head="$waiting_candidate"
polling_merge="$(git -C "$fixture" rev-parse HEAD)"
export FAKE_POLL_COUNT_FILE="$poll_count_file"
export FAKE_POLL_SLEEP_LOG="$poll_sleep_log"
export FAKE_POLL_HEAD="$polling_head"
export FAKE_POLL_MERGE_COMMIT="$polling_merge"
export FAKE_POLL_PR_URL=https://example.test/pull/91
(
  PATH="$polling_bin:$PATH"
  AGENTIC_OPS_GH_BIN="$polling_gh"
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  RELEASE_PR_URL="$FAKE_POLL_PR_URL"
  release_wait_for_soft_merge tapstate/agentic-ops "$polling_head"
  printf '%s\t%s\n' "$RELEASE_PR_STATE" "$RELEASE_MERGE_COMMIT"
) > "$test_root/soft-polling.out"
test "$(cat "$poll_count_file")" = "2" ||
  fail "软门禁发布未持续查询 PR 直至人工合并"
test "$(cat "$poll_sleep_log")" = "5" ||
  fail "软门禁发布轮询间隔不是 5 秒"
grep -q "^MERGED[[:space:]]$polling_merge$" "$test_root/soft-polling.out" ||
  fail "软门禁发布未在检测到人工 Merge commit 后自动继续"

# 非阻塞选项仍使用单次状态检查并返回等待态，供发布者稍后手动续跑。
rm -f "$poll_count_file"
manual_wait_status=0
(
  AGENTIC_OPS_GH_BIN="$polling_gh"
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  RELEASE_VERIFIED_AT="2026-08-22T00:00:00Z"
  RELEASE_PR_URL="$FAKE_POLL_PR_URL"
  release_wait_for_manual_merge "$fixture" tapstate/agentic-ops release_publish v9.6 "$polling_head" release/v9.6
) > "$test_root/manual-wait.out" || manual_wait_status=$?
test "$manual_wait_status" -eq 2 ||
  fail "--no-wait-for-merge 未保留人工续跑等待态"
grep -q '"status":"waiting_for_manual_merge"' "$test_root/manual-wait.out" ||
  fail "人工续跑等待态缺少结构化输出"

# 正常发布完成后必须由同一 publish 流程把 develop 快进到 main；若开发分支
# 在等待期间产生新的未发布提交，则不得用普通 merge 或改写历史来掩盖分叉。
sync_remote="$test_root/sync-remote.git"
sync_fixture="$test_root/sync-repo"
git init --bare "$sync_remote" >/dev/null
git clone "$sync_remote" "$sync_fixture" >/dev/null 2>&1
git -C "$sync_fixture" config user.email agentic-ops-test@example.test
git -C "$sync_fixture" config user.name "AgenticOps Test"
printf 'initial\n' > "$sync_fixture/README.md"
git -C "$sync_fixture" add README.md
git -C "$sync_fixture" commit -m "sync initial" >/dev/null
git -C "$sync_fixture" branch -M main
git -C "$sync_fixture" push -u origin main >/dev/null
git -C "$sync_fixture" switch -c develop >/dev/null
printf 'release candidate\n' >> "$sync_fixture/README.md"
git -C "$sync_fixture" add README.md
git -C "$sync_fixture" commit -m "sync release candidate" >/dev/null
sync_candidate="$(git -C "$sync_fixture" rev-parse HEAD)"
git -C "$sync_fixture" push -u origin develop >/dev/null
git -C "$sync_fixture" switch main >/dev/null
git -C "$sync_fixture" merge --no-ff develop -m "sync release merge" >/dev/null
sync_merge="$(git -C "$sync_fixture" rev-parse HEAD)"
git -C "$sync_fixture" push origin main >/dev/null
git -C "$sync_fixture" switch develop >/dev/null
(
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  release_sync_develop_to_main "$sync_fixture" "$sync_merge"
)
test "$(git -C "$sync_fixture" rev-parse develop)" = "$sync_merge" ||
  fail "发布完成后本地 develop 未快进到 main"
test "$(git --git-dir="$sync_remote" rev-parse refs/heads/develop)" = "$sync_merge" ||
  fail "发布完成后远端 develop 未快进到 main"

printf 'new development during release\n' >> "$sync_fixture/README.md"
git -C "$sync_fixture" add README.md
git -C "$sync_fixture" commit -m "sync diverged develop" >/dev/null
git -C "$sync_fixture" push origin develop >/dev/null
if (
  . "$repo_root/maintainer/scripts/lib/release-common.sh"
  release_sync_develop_to_main "$sync_fixture" "$sync_merge"
) > "$test_root/develop-sync-diverged.out" 2> "$test_root/develop-sync-diverged.err"; then
  fail "develop 分叉时不得自动同步或改写历史"
fi
grep -q 'release_develop_sync_not_fast_forward' "$test_root/develop-sync-diverged.err" ||
  fail "develop 分叉时未返回快进保护错误"

printf '{"ok":true,"operation":"test_release_workflow","delivery":"python"}\n'
