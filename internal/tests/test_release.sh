#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT

fail() { printf '发布流程验证失败：%s\n' "$1" >&2; exit 1; }

for script in \
  .githooks/pre-commit .githooks/pre-push .githooks/reference-transaction \
  internal/bin/story-gate internal/release/release.sh internal/release/hotfix.sh \
  internal/release/history-rewrite.sh internal/release/lib/development-workflow.sh \
  internal/release/lib/release-common.sh; do
  bash -n "$repo_root/$script" || fail "$script Shell 语法无效"
done

(
  . "$repo_root/internal/release/lib/release-common.sh"
  release_validate_version v1.0 >/dev/null
  if release_validate_version 1.0 >/dev/null 2>&1; then exit 1; fi
  release_validate_jira_id AO-123 >/dev/null
  if release_validate_jira_id ao-123 >/dev/null 2>&1; then exit 1; fi
) || fail "版本或 Jira 编号校验不符合合同"

fixture="$test_root/repo"
mkdir -p \
  "$fixture/.githooks" "$fixture/internal/bin" "$fixture/internal/story_gate" \
  "$fixture/internal/tests" "$fixture/docs/user-stories/v1"
cp "$repo_root/.githooks/pre-commit" "$fixture/.githooks/pre-commit"
cp "$repo_root/.githooks/pre-push" "$fixture/.githooks/pre-push"
cp "$repo_root/.githooks/reference-transaction" "$fixture/.githooks/reference-transaction"
printf 'source\n' > "$fixture/.agentic-ops-source"
printf 'schema_version: 1\n' > "$fixture/internal/story_gate/review-policy.yaml"
printf 'schema_version: 1\n' > "$fixture/internal/story_gate/stories.yaml"
printf '[project]\nname="fixture"\nversion="0.0.0"\n' > "$fixture/internal/pyproject.toml"
printf 'version = 1\n' > "$fixture/internal/uv.lock"
printf 'test\n' > "$fixture/docs/user-stories/v1/story.md"
cat > "$fixture/internal/bin/story-gate" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${STORY_GATE_LOG:?}"
EOF
chmod 0755 "$fixture/.githooks/"* "$fixture/internal/bin/story-gate"

git -C "$fixture" init -q -b develop
git -C "$fixture" config user.email test@example.test
git -C "$fixture" config user.name Test
git -C "$fixture" add .
git -C "$fixture" commit -qm baseline

story_log="$test_root/story.log"
printf 'change\n' > "$fixture/product.txt"
git -C "$fixture" add product.txt
(cd "$fixture" && STORY_GATE_LOG="$story_log" ./.githooks/pre-commit)
grep -Fxq 'impact --change-source staged' "$story_log" ||
  fail "pre-commit 未调用统一 story-gate"

git -C "$fixture" commit -qm candidate
head_sha="$(git -C "$fixture" rev-parse HEAD)"
printf 'refs/heads/develop %s refs/heads/develop %040d\n' "$head_sha" 0 |
  (cd "$fixture" && STORY_GATE_LOG="$story_log" ./.githooks/pre-push)
grep -Fq "impact --change-source range --head $head_sha" "$story_log" ||
  fail "pre-push 未校验待推送 commit range"

if printf 'refs/heads/develop %s refs/heads/main %040d\n' "$head_sha" 0 |
  (cd "$fixture" && STORY_GATE_LOG="$story_log" ./.githooks/pre-push) >/dev/null 2>&1; then
  fail "pre-push 不得允许普通直推 main"
fi

git -C "$fixture" branch -M main
printf 'main change\n' > "$fixture/main.txt"
git -C "$fixture" add main.txt
if (cd "$fixture" && STORY_GATE_LOG="$story_log" ./.githooks/pre-commit) >/dev/null 2>&1; then
  fail "pre-commit 不得允许 main 直接提交"
fi

if "$repo_root/internal/release/release.sh" prepare --version 1.0 >/dev/null 2>&1; then
  fail "release.sh 不得接受非 vX.Y 版本"
fi

printf 'AgenticOps 发布工作流验证通过\n'
