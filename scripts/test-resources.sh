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
test -f install-resources/checksums.txt
grep 'basic/manifest.json' install-resources/checksums.txt >/dev/null
grep 'darwin-arm64/agentic-cli' install-resources/checksums.txt >/dev/null

printf '{"ok":true,"operation":"test_resources"}\n'
