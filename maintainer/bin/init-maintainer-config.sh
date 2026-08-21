#!/usr/bin/env bash
# 初始化 maintainer 工作面 Jira 配置：校验源头仓库身份、准备本地状态目录、
# 校验 Connection 定义，并引导配置维护者 Jira 凭证（写入 maintainer/.local/.env，0600）。
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SOURCE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
CONNECTION_ID="${AGENTIC_OPS_MAINTAINER_CONNECTION_ID:-tapdata-cloud}"
CONNECTIONS_DIR="$SOURCE_ROOT/maintainer/standards/connections"
LOCAL_DIR="$SOURCE_ROOT/maintainer/.local"
ENV_FILE="$LOCAL_DIR/.env"
IDENTITY_FILE="$LOCAL_DIR/identity.yaml"
PYTHON_BIN="$SOURCE_ROOT/maintainer/.venv/bin/python"

fail() {
  printf 'AgenticOps：%s\n' "$1" >&2
  exit 1
}

# 1. 源头仓库身份校验（与 ao-maint workspace 解析同源）
[ -f "$SOURCE_ROOT/.agentic-ops-source" ] || fail "缺少 .agentic-ops-source 标记，必须在 AgenticOps 源头仓库或 worktree 中初始化"
[ -d "$SOURCE_ROOT/maintainer" ] || fail "缺少 maintainer/ 目录"
[ -f "$SOURCE_ROOT/maintainer/AGENTS.md" ] || fail "缺少 maintainer/AGENTS.md AI 入口"

ORIGIN_URL="$(git -C "$SOURCE_ROOT" config --get-all remote.origin.url 2>/dev/null || true)"
case "$ORIGIN_URL" in
  git@github.com:tapstate/agentic-ops.git|ssh://git@github.com/tapstate/agentic-ops|https://github.com/tapstate/agentic-ops)
    ;;
  *)
    fail "origin 不是固定官方仓库 tapstate/agentic-ops（当前：${ORIGIN_URL:-<未配置>}）"
    ;;
esac

# 2. Connection 定义校验
[ -f "$CONNECTIONS_DIR/$CONNECTION_ID.yaml" ] || fail "缺少 maintainer Connection 定义：$CONNECTIONS_DIR/$CONNECTION_ID.yaml"

# 3. 本地状态目录
mkdir -p "$LOCAL_DIR/jira-plans"
printf 'AgenticOps：maintainer 本地状态目录已就绪：%s\n' "$LOCAL_DIR"

# 4. 引导 Jira 凭证
if [ ! -x "$PYTHON_BIN" ]; then
  fail "维护 Runtime 尚未准备，请先执行 uv sync --locked --project maintainer"
fi

printf 'AgenticOps：开始配置 maintainer Jira 账户（Connection: %s）\n' "$CONNECTION_ID"

if [ -f "$ENV_FILE" ] && [ -s "$ENV_FILE" ]; then
  printf 'AgenticOps：检测到已有凭证文件 %s，如需修改请使用 ao-maint jira auth set\n' "$ENV_FILE"
else
  export PYTHONPATH="$SOURCE_ROOT/maintainer/runtime/src"
  exec "$PYTHON_BIN" -m ao_maint --source-root "$SOURCE_ROOT" jira auth set --interactive
fi

# 5. Agent 身份确认（A 方案：初始化 maintainer 时确认身份）
if [ -f "$IDENTITY_FILE" ] && [ -s "$IDENTITY_FILE" ]; then
  printf 'AgenticOps：检测到已有 Agent 身份 %s，如需修改请使用 ao-maint install identity set --interactive\n' "$IDENTITY_FILE"
else
  printf 'AgenticOps：开始配置 maintainer Agent 身份（执行维护任务的 Agent 标识）\n'
  export PYTHONPATH="$SOURCE_ROOT/maintainer/runtime/src"
  exec "$PYTHON_BIN" -m ao_maint --source-root "$SOURCE_ROOT" install identity set --interactive
fi
