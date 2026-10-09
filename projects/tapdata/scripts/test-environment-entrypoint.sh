#!/usr/bin/env bash
set -euo pipefail
umask 077
export JAVA_VERSION="${JAVA_VERSION:-java17}"
export JAVA_HOME="${JAVA_HOME:-/opt/java/openjdk}"
export TAPDATA_HOME=/tapdata/apps
export TAPDATA_WORK_DIR=/tapdata/work
export HOME="${HOME:-/tapdata/home}"
mkdir -p "$TAPDATA_WORK_DIR" "$HOME"
# 更新仅替换应用目录；监控身份和日志位于独立的 WORK_DIR。
rm -rf /tapdata/apps
mkdir -p /tapdata/apps
cp -a /bundle/. /tapdata/apps/
# 原生打包入口创建此目录；Launcher 启动 TM 时会读取初始化脚本清单。
mkdir -p /tapdata/apps/etc/init
node_path="$(sed -n 's/.*"node_binary": "\([^"]*\)".*/\1/p' /tapdata/settings.json)"
node="/bundle/$node_path"
export PATH="$(dirname "$node"):$PATH"
"$node" /bootstrap/node.js configure
if [ -f /license/license.txt ]; then
  mkdir -p "$HOME/.tapdata"
  cp /license/license.txt /tapdata/apps/license.txt
  cp /license/license.txt "$TAPDATA_WORK_DIR/license.txt"
  cp /license/license.txt "$HOME/.tapdata/license.txt"
fi
cd /tapdata/apps
if [ -f connectors/dist.tar.gz ] && [ ! -d connectors/dist ]; then
  tar xzf connectors/dist.tar.gz -C /tapdata/apps/
fi
if [ -f components/apiserver.tar.gz ] && [ ! -d components/apiserver ]; then
  tar xzf components/apiserver.tar.gz -C /tapdata/apps/
fi
stop() {
  trap - TERM INT
  # 启动已写入 .workDir；本版 stop -f 不接受附加参数。
  ./tapdata stop -f >/dev/null 2>&1 || true
  "$node" /bootstrap/node.js stop || true
  exit 0
}
trap stop TERM INT
# Launcher 可能打印有效配置；只写私有日志，不送 Docker 标准输出。
components="$("$node" -e 'const s=require("/tapdata/settings.json"); console.log(["TM","FE","APIServer"].filter(c=>s.components.includes(c)).map(c=>({TM:"frontend",FE:"backend",APIServer:"apiserver"}[c])).join(" "))')"
for component in $components; do
  ./tapdata start "$component" --workDir "$TAPDATA_WORK_DIR" >>"$TAPDATA_WORK_DIR/launcher-start.log" 2>&1
done
startup_timeout="$("$node" -e 'console.log(require("/tapdata/settings.json").startup_timeout_seconds)' )"
deadline=$((SECONDS + startup_timeout))
while ((SECONDS < deadline)); do
  if "$node" /bootstrap/node.js health; then break; fi
  sleep 2 & wait $!
done
while true; do
  "$node" /bootstrap/node.js health || exit 1
  sleep 5 & wait $!
done
