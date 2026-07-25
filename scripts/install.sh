#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
REPO_URL="${AGENTIC_OPS_REPO_URL:-git@github.com:tapstate/agentic-ops.git}"
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

confirm_update() {
  local current_ref=""
  local answer=""

  current_ref="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
  echo "AgenticOps is already installed at $INSTALL_DIR" >&2
  echo "Current ref: $current_ref" >&2
  echo "Target: origin/$BRANCH latest" >&2

  case "${AGENTIC_OPS_ASSUME_YES:-}" in
    1|true|TRUE|yes|YES|y|Y)
      echo "update confirmed by AGENTIC_OPS_ASSUME_YES" >&2
      return
      ;;
    0|false|FALSE|no|NO|n|N)
      echo "update cancelled by AGENTIC_OPS_ASSUME_YES" >&2
      exit 2
      ;;
  esac

  if [ ! -r /dev/tty ]; then
    echo "update cancelled: confirmation required; rerun with AGENTIC_OPS_ASSUME_YES=1 to update non-interactively" >&2
    exit 2
  fi

  if ! printf 'Update existing AgenticOps installation at %s? [y/N] ' "$INSTALL_DIR" > /dev/tty 2>/dev/null; then
    echo "update cancelled: confirmation required; rerun with AGENTIC_OPS_ASSUME_YES=1 to update non-interactively" >&2
    exit 2
  fi
  if ! IFS= read -r answer < /dev/tty 2>/dev/null; then
    echo "update cancelled: confirmation required; rerun with AGENTIC_OPS_ASSUME_YES=1 to update non-interactively" >&2
    exit 2
  fi
  case "$answer" in
    y|Y|yes|YES)
      echo "update confirmed by user" >&2
      ;;
    *)
      echo "update cancelled by user" >&2
      exit 2
      ;;
  esac
}

path_contains_dir() {
  local dir="$1"
  case ":${PATH:-}:" in
    *":$dir:"*) return 0 ;;
    *) return 1 ;;
  esac
}

shell_profile_path() {
  local shell_name=""

  shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    zsh|"") printf '%s\n' "$HOME/.zshrc" ;;
    bash) printf '%s\n' "$HOME/.bashrc" ;;
    *) printf '%s\n' "$HOME/.profile" ;;
  esac
}

path_profile_line() {
  if [ "$INSTALL_DIR" = "$HOME/.agentic-ops" ]; then
    printf '%s\n' 'export PATH="$HOME/.agentic-ops/bin:$PATH"'
  else
    printf 'export PATH="%s:$PATH"\n' "$bin_dir"
  fi
}

ensure_path_profile() {
  local profile="$1"
  local line="$2"

  mkdir -p "$(dirname "$profile")"
  touch "$profile"
  printf '\n%s\n' "$line" >> "$profile"
  echo "PATH entry added to shell profile: $profile" >&2
  return 0
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
  confirm_update
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

path_configured="false"
path_profile="$(shell_profile_path)"
path_profile_line_value="$(path_profile_line)"
path_profile_configured="false"
path_profile_updated="false"

if [ -f "$path_profile" ] && grep -qxF "$path_profile_line_value" "$path_profile"; then
  path_profile_configured="true"
fi

if path_contains_dir "$bin_dir"; then
  path_configured="true"
else
  echo "agentic-cli is installed but not on PATH: $bin_dir/agentic-cli" >&2
  echo "For this shell, run: case \":\$PATH:\" in *\":$bin_dir:\"*) ;; *) export PATH=\"$bin_dir:\$PATH\" ;; esac" >&2
  echo "This installer cannot modify the parent shell PATH when it is run through a pipe." >&2
fi

if [ "$path_profile_configured" = "true" ]; then
  echo "PATH entry already exists in shell profile: $path_profile" >&2
elif ensure_path_profile "$path_profile" "$path_profile_line_value"; then
  path_profile_configured="true"
  path_profile_updated="true"
fi

if [ "$path_configured" = "false" ]; then
  echo "Open a new terminal or run: source \"$path_profile\"" >&2
fi

printf '{"ok":true,"operation":"%s","install_dir":"%s","bin":"%s","target":"%s","current_ref":"%s","source":"managed_clone","path_configured":%s,"path_entry":"%s","path_profile":"%s","path_profile_configured":%s,"path_profile_updated":%s,"next_action":"workspace_init"}\n' "$operation" "$INSTALL_DIR" "$bin_dir/agentic-cli" "$target" "$current_ref" "$path_configured" "$bin_dir" "$path_profile" "$path_profile_configured" "$path_profile_updated"
