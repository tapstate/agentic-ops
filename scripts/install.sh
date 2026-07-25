#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
REPO_URL="${AGENTIC_OPS_REPO_URL:-https://github.com/tapstate/agentic-ops.git}"
BRANCH="${AGENTIC_OPS_BRANCH:-main}"

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

target="${target_os}-${target_arch}"
install_resources_dir="$INSTALL_DIR/install-resources"
source_binary="$install_resources_dir/$target/agentic-cli"
bin_dir="$INSTALL_DIR/bin"
local_dir="$INSTALL_DIR/.local"
previous_ref=""
rollback_enabled="0"

checksum_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

verify_checksums() {
  local checksums="$install_resources_dir/checksums.txt"
  local expected=""
  local actual=""
  local rel=""
  local file=""

  if [ ! -f "$checksums" ]; then
    echo "checksums not found: $checksums" >&2
    exit 1
  fi

  while read -r expected rel; do
    if [ -z "${expected:-}" ] || [ -z "${rel:-}" ]; then
      continue
    fi
    file="$install_resources_dir/$rel"
    if [ ! -f "$file" ]; then
      echo "checksum file target not found: $rel" >&2
      exit 1
    fi
    actual="$(checksum_file "$file")"
    if [ "$actual" != "$expected" ]; then
      echo "checksum mismatch for: $rel" >&2
      exit 1
    fi
  done < "$checksums"
}

copy_binary() {
  if [ ! -f "$source_binary" ]; then
    echo "platform binary not found: $source_binary" >&2
    exit 1
  fi
  mkdir -p "$bin_dir"
  install -m 0755 "$source_binary" "$bin_dir/agentic-cli"
}

write_local_state() {
  local current_ref="$1"
  local operation="$2"
  local installed_at=""

  installed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$local_dir"
  printf '%s\n' "$current_ref" > "$local_dir/current-ref"
  printf '{"ok":true,"operation":"%s","repo_url":"%s","branch":"%s","target":"%s","current_ref":"%s","installed_at":"%s","bin":"%s"}\n' "$operation" "$REPO_URL" "$BRANCH" "$target" "$current_ref" "$installed_at" "$bin_dir/agentic-cli" > "$local_dir/install-log.json"
}

rollback() {
  if [ "$rollback_enabled" != "1" ] || [ -z "$previous_ref" ]; then
    return
  fi
  trap - ERR
  set +e
  git -C "$INSTALL_DIR" checkout -f "$previous_ref" >/dev/null 2>&1
  copy_binary >/dev/null 2>&1
  printf '%s\n' "$previous_ref" > "$local_dir/current-ref"
  echo "install failed; rolled back to $previous_ref" >&2
}

trap rollback ERR

if ! command -v git >/dev/null 2>&1; then
  echo "git is required to install AgenticOps" >&2
  exit 1
fi

operation="install"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  if [ -e "$INSTALL_DIR" ]; then
    if [ -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
      echo "install dir exists but is not an AgenticOps git clone: $INSTALL_DIR" >&2
      exit 1
    fi
  fi
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" >/dev/null
else
  operation="update"
  mkdir -p "$local_dir"
  previous_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  printf '%s\n' "$previous_ref" > "$local_dir/previous-ref"
  rollback_enabled="1"

  if [ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]; then
    stash_message="agentic-ops-update-$(date -u +%Y%m%dT%H%M%SZ)"
    git -C "$INSTALL_DIR" stash push -m "$stash_message" >/dev/null
    git -C "$INSTALL_DIR" rev-parse --verify refs/stash > "$local_dir/update-stash"
  fi

  git -C "$INSTALL_DIR" fetch origin "$BRANCH" >/dev/null
  git -C "$INSTALL_DIR" checkout -B "$BRANCH" "origin/$BRANCH" >/dev/null
fi

if [ ! -d "$install_resources_dir/basic" ]; then
  echo "install resources not found: $install_resources_dir/basic" >&2
  exit 1
fi

verify_checksums
copy_binary
current_ref="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
write_local_state "$current_ref" "$operation"

printf '{"ok":true,"operation":"%s","install_dir":"%s","bin":"%s","target":"%s","current_ref":"%s","source":"managed_clone","next_action":"workspace_init"}\n' "$operation" "$INSTALL_DIR" "$bin_dir/agentic-cli" "$target" "$current_ref"
