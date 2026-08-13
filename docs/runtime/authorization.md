# AgenticOps 授权管理

## 目的

`agentic-cli auth jira` 是 Jira 授权的统一入口。用户通过命令查看、设置、修改、删除和验证授权，避免手工编辑环境文件导致变量名、scope、文件权限或凭证来源错误。

## 命令

```sh
agentic-cli auth jira list
agentic-cli auth jira show --connection-id tapdata-cloud
agentic-cli auth jira set --connection-id tapdata-cloud --scope user --interactive
agentic-cli auth jira remove --connection-id tapdata-cloud --scope user --field token
agentic-cli auth jira verify --connection-id tapdata-cloud
```

`set` 重复执行即为修改。token 只允许交互式隐藏输入或 `--token-stdin`，不提供 token 命令行参数。

## Scope 与优先级

```text
进程环境变量
> 项目工作空间 .agentic-ops/.env
> ~/.agentic-ops/user/.env
```

- `user` 适合同一 Jira 身份跨项目复用。
- `workspace` 只适合当前业务项目，必须在 `project_execution` 模式使用。
- `show --scope effective` 返回实际来源；它只显示配置状态和脱敏 email。

## 安全门禁

- Runtime 从 Connection 定义读取准确变量名，不允许用户猜测。
- 凭证文件使用锁、原子替换和 `0600` 权限。
- 输出和诊断不得包含 token、Authorization header 或原始认证响应。
- `verify` 必须回读 Jira 当前用户；认证、站点或 API 能力不满足时禁止真实任务写入。
- Connection 与 Project Profile 不一致仍返回 `jira_workspace_mismatch`，授权成功不能绕过工作空间绑定。

## TAP-12289 验收

TAP-12289 是本阶段真实 Tapdata 验收任务。验收顺序：

1. 配置并验证 `tapdata-cloud` 授权。
2. 在独立项目 AI 工作空间绑定 `tapdata` Profile，初始化 TAP-12289 状态。
3. 只读核对 Issue ID、项目、类型、经办人、状态和现有 ownership 评论。
4. 对阶段评论与 Worklog 分别执行 `plan -> apply -> readback`。
5. 验证重复执行不会创建第二条记录，`sync.json`、`decisions.ndjson` 和 `journal.ndjson` 可恢复。

Description 只允许修改明确确认的 AgenticOps 受管章节；不得覆盖 TAP-12289 的原始缺陷日志。
