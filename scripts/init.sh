#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BIN_DIR="$INSTALL_DIR/bin"
VERSION="${AGENTIC_OPS_VERSION:-latest}"

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

cat > "$BIN_DIR/agent-task-ops" <<'SH'
#!/usr/bin/env sh
echo '{"ok":false,"operation":"install","code":"binary_not_installed","message":"agent-task-ops release binary has not been downloaded in this first-stage bootstrap"}'
exit 1
SH

chmod +x "$BIN_DIR/agent-task-ops"

printf '{"ok":true,"operation":"install","install_dir":"%s","bin":"%s","target":"%s-%s","version":"%s","next_action":"workspace_init"}\n' "$INSTALL_DIR" "$BIN_DIR/agent-task-ops" "$target_os" "$target_arch" "$VERSION"
