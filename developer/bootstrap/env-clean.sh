#!/usr/bin/env bash
# AgenticOps 环境变量清理脚本
#
# 清理 AGENTIC_OPS_* 身份覆盖与实验残留环境变量
# （AGENTIC_OPS_BRANCH / AGENTIC_OPS_HOME / AGENTIC_OPS_REPO_URL / AGENTIC_OPS_TEST_* 等）。
# 这些变量会被 install.sh / update.sh / rollback.sh / ao-work 的安装身份门禁拒绝
# （install_identity_override_forbidden），执行安装、验证安装和运行时命令前必须先清理。
#
# 用法（必须 source 执行；直接 bash 执行是子进程，unset 不影响当前 shell）：
#   . developer/bootstrap/env-clean.sh
# 或
#   source developer/bootstrap/env-clean.sh

_ao_cleaned=""
while IFS= read -r _ao_name; do
  unset "$_ao_name" 2>/dev/null || true
  _ao_cleaned="$_ao_cleaned$_ao_name "
done < <(env | sed -n 's/^\(AGENTIC_OPS_[^=]*\)=.*/\1/p')
unset _ao_name

if [ -n "$_ao_cleaned" ]; then
  printf 'AgenticOps：已清理环境变量：%s\n' "$_ao_cleaned"
else
  printf 'AgenticOps：环境无 AGENTIC_OPS_* 变量，无需清理\n'
fi
unset _ao_cleaned

return 0 2>/dev/null || exit 0
