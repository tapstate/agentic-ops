#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BIN_DIR="$INSTALL_DIR/bin"
VERSION="${AGENTIC_OPS_VERSION:-latest}"
RELEASE_ROOT="${AGENTIC_OPS_RELEASE_DIR:-}"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"

case "$os" in
  darwin) target_os="darwin" ;;
  linux) target_os="linux" ;;
  *) echo "unsupported OS: $os" >&2; exit 1 ;;
esac

case "$arch" in
  arm64|aarch64) target_arch="arm64" ;;
  x86_64|amd64) target_arch="amd64" ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

mkdir -p "$BIN_DIR"

is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

select_latest_release_dir() {
  local root="$1"
  local candidate=""
  local name=""
  local version_core=""
  local version_numbers=""
  local major=""
  local iteration=""
  local index=""
  local extra=""
  local best_dir=""
  local best_major=-1
  local best_iteration=-1
  local best_index=-1

  while IFS= read -r candidate; do
    name="$(basename "$candidate")"
    version_core="${name#RES-v}"
    if [ "$version_core" = "$name" ]; then
      continue
    fi
    version_numbers="${version_core%%-*}"
    IFS=. read -r major iteration index extra <<< "$version_numbers"
    if [ -n "${extra:-}" ]; then
      continue
    fi
    if ! is_uint "$major" || ! is_uint "$iteration" || ! is_uint "$index"; then
      continue
    fi
    if [ "$major" -gt "$best_major" ] ||
      { [ "$major" -eq "$best_major" ] && [ "$iteration" -gt "$best_iteration" ]; } ||
      { [ "$major" -eq "$best_major" ] && [ "$iteration" -eq "$best_iteration" ] && [ "$index" -gt "$best_index" ]; }; then
      best_dir="$candidate"
      best_major="$major"
      best_iteration="$iteration"
      best_index="$index"
    fi
  done < <(find "$root" -maxdepth 1 -mindepth 1 -type d -name 'RES-v*' -print)

  printf '%s' "$best_dir"
}

if [ -n "$RELEASE_ROOT" ]; then
  if [ ! -d "$RELEASE_ROOT" ]; then
    echo "release directory not found: $RELEASE_ROOT" >&2
    exit 1
  fi

  if [ "$VERSION" = "latest" ]; then
    release_dir="$(select_latest_release_dir "$RELEASE_ROOT")"
  else
    release_dir="$RELEASE_ROOT/$VERSION"
  fi
  if [ -z "${release_dir:-}" ] || [ ! -d "$release_dir" ]; then
    echo "release version not found under: $RELEASE_ROOT" >&2
    exit 1
  fi

  release_version="$(basename "$release_dir")"
  binary_package="$release_dir/agentic-cli_${release_version}_${target_os}-${target_arch}.tar.gz"
  asset_package="$release_dir/agentic-ops-assets_${release_version}.tar.gz"
  if [ ! -f "$binary_package" ]; then
    echo "binary package not found: $binary_package" >&2
    exit 1
  fi
  if [ ! -f "$asset_package" ]; then
    echo "asset package not found: $asset_package" >&2
    exit 1
  fi

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT
  tar -xzf "$binary_package" -C "$tmp_dir"
  install -m 0755 "$tmp_dir/agentic-cli" "$BIN_DIR/agentic-cli"

  tar -xzf "$asset_package" -C "$tmp_dir"
  "$BIN_DIR/agentic-cli" assets install --source "$tmp_dir/assets" --install-dir "$INSTALL_DIR" --version "$release_version" >/dev/null

  printf '{"ok":true,"operation":"install","install_dir":"%s","bin":"%s","target":"%s-%s","version":"%s","source":"local_release","next_action":"workspace_init"}\n' "$INSTALL_DIR" "$BIN_DIR/agentic-cli" "$target_os" "$target_arch" "$release_version"
  exit 0
fi

cat > "$BIN_DIR/agentic-cli" <<'SH'
#!/usr/bin/env sh
echo '{"ok":false,"operation":"install","code":"binary_not_installed","message":"agentic-cli release binary has not been downloaded in this first-stage bootstrap"}'
exit 1
SH

chmod +x "$BIN_DIR/agentic-cli"

printf '{"ok":true,"operation":"install","install_dir":"%s","bin":"%s","target":"%s-%s","version":"%s","next_action":"workspace_init"}\n' "$INSTALL_DIR" "$BIN_DIR/agentic-cli" "$target_os" "$target_arch" "$VERSION"
