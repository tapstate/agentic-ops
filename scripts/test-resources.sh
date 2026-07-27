#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

tmp_checksums="$(mktemp)"
trap 'rm -f "$tmp_checksums"' EXIT

AGENTIC_OPS_CHECKSUMS_OUT="$tmp_checksums" bash scripts/update-checksums.sh >/dev/null
diff -u install-resources/checksums.txt "$tmp_checksums" >/dev/null

test -f install-resources/basic/manifest.json
test -f install-resources/basic/projects/tapdata/profile.yaml
test -f install-resources/basic/projects/tapdata/tools.yaml
test -f install-resources/basic/projects/tapdata/runbooks/README.md
test -f install-resources/basic/projects/tapdata/runbooks/build-test-and-local-run.md
test -f install-resources/basic/projects/tapdata/runbooks/common-lib-upgrade.md
test -f install-resources/basic/projects/tapdata/admission/defect-fix.yaml
test -f install-resources/basic/projects/tapdata/templates/defect/admission-analysis-comment.md
test -f install-resources/basic/projects/tapdata/templates/defect/description-sections.yaml
test -f install-resources/basic/projects/tapdata/templates/defect/fix-plan-comment.md
test -f install-resources/basic/projects/tapdata/templates/defect/completion-form.yaml
test -f install-resources/basic/contracts/operations/add-task-comment.yaml
test -f install-resources/basic/contracts/operations/update-task-description-sections.yaml
test -f install-resources/basic/contracts/operations/update-task-form.yaml
test -f install-resources/checksums.txt
test ! -d docs/superpowers
grep '^\.superpowers/$' .gitignore >/dev/null
grep '本地执行状态' AGENTS.md | grep '\.superpowers/' >/dev/null
grep '本地执行状态' docs/architecture/project-structure.md | grep '\.superpowers/' >/dev/null
grep 'basic/manifest.json' install-resources/checksums.txt >/dev/null
grep 'basic/projects/tapdata/runbooks/README.md' install-resources/checksums.txt >/dev/null
grep 'basic/projects/tapdata/runbooks/build-test-and-local-run.md' install-resources/checksums.txt >/dev/null
grep 'basic/projects/tapdata/runbooks/common-lib-upgrade.md' install-resources/checksums.txt >/dev/null
grep 'basic/projects/tapdata/templates/defect/fix-plan-comment.md' install-resources/checksums.txt >/dev/null
grep 'darwin-arm64/agentic-cli' install-resources/checksums.txt >/dev/null

tapdata_assets="install-resources/basic/projects/tapdata"
if grep -ER '/Users/[^/[:space:]]+|58\.251\.34\.123' "$tapdata_assets"; then
  echo "tapdata project assets must not contain maintainer paths or internal service addresses" >&2
  exit 1
fi

grep '目标分支' "$tapdata_assets/standards/development-rules.md" >/dev/null
grep -- '-DskipTests' "$tapdata_assets/standards/development-rules.md" >/dev/null
grep 'update-task-description-sections' "$tapdata_assets/admission/defect-fix.yaml" >/dev/null
grep 'add-task-comment' "$tapdata_assets/admission/defect-fix.yaml" >/dev/null
grep 'update-task-form' "$tapdata_assets/admission/defect-fix.yaml" >/dev/null

if grep -ERn '研发负责人|developer_owner|development-leads|development-lead|DL-[0-9]|dl-[0-9]' \
  AGENTS.md README.md agent-init.md agent-guides.md docs install-resources/basic packages plans skills; then
  echo "superseded development role names must not remain in current assets" >&2
  exit 1
fi

printf '{"ok":true,"operation":"test_resources"}\n'
