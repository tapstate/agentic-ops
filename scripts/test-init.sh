#!/usr/bin/env bash
set -euo pipefail

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT

HOME="$tmp_home" bash scripts/init.sh > "$tmp_home/out.json"

grep '"ok":true' "$tmp_home/out.json"
test -x "$tmp_home/.agentic-ops/bin/agentic-cli"

target="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
case "$target" in
  darwin-x86_64) target="darwin-amd64" ;;
  linux-x86_64) target="linux-amd64" ;;
  *-aarch64) target="${target%-aarch64}-arm64" ;;
esac

make_release() {
  local release_root="$1"
  local version="$2"
  local release_dir="$release_root/$version"

  mkdir -p "$release_dir/bin" "$release_dir/assets"
  printf '#!/usr/bin/env sh\nif [ "$1" = "assets" ] && [ "$2" = "install" ]; then printf '"'"'{"ok":true,"operation":"assets_install"}\\n'"'"'; exit 0; fi\nprintf '"'"'{"ok":true,"operation":"version","version":"%s"}\\n'"'"'\n' "$version" > "$release_dir/bin/agentic-cli"
  chmod +x "$release_dir/bin/agentic-cli"
  tar -C "$release_dir/bin" -czf "$release_dir/agentic-cli_${version}_${target}.tar.gz" agentic-cli
  printf '{"version":"%s"}\n' "$version" > "$release_dir/assets/manifest.json"
  tar -C "$release_dir" -czf "$release_dir/agentic-ops-assets_${version}.tar.gz" assets
  rm -rf "$release_dir/bin" "$release_dir/assets"
}

release_root="$tmp_home/releases"
version="RES-v0.1.10-test123"
make_release "$release_root" "RES-v0.1.9-old123"
make_release "$release_root" "$version"

deploy_home="$tmp_home/deploy-home"
AGENTIC_OPS_RELEASE_DIR="$release_root" HOME="$deploy_home" bash scripts/init.sh > "$tmp_home/deploy.json"

grep "\"version\":\"$version\"" "$tmp_home/deploy.json"
test -x "$deploy_home/.agentic-ops/bin/agentic-cli"
"$deploy_home/.agentic-ops/bin/agentic-cli" --version | grep "\"version\":\"$version\""
