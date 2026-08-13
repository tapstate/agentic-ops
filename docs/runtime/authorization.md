# AgenticOps 授权管理

## 目的

`agentic-cli auth jira` 是 Jira 授权的统一入口。一个业务项目 AgenticOps 工作空间代表一名研发员，并只配置一个 Jira 账户；`~/.agentic-ops` 共享安装不承载研发员身份。

## 命令

```sh
agentic-cli auth jira list
agentic-cli auth jira show
agentic-cli auth jira set
agentic-cli auth jira remove --field token
agentic-cli auth jira verify
```

`set` 无参数时自动进入交互设置，重复执行即为修改。token 只允许交互式隐藏输入或 `--token-stdin`，不提供 token 命令行参数。

首次创建业务项目工作空间时，优先使用 `agentic-cli workspace init`。初始化入口会先确认 Project Profile 和 Jira 项目空间，再复用同一套工作空间授权规则完成凭证输入、身份验证和 Project 访问检查。

Connection 默认从当前项目 Profile 推导；没有 Profile 且安装中只有一个 Connection 时自动选择。只有维护或迁移场景遇到多个未绑定站点时，才使用隐藏的高级参数 `--connection-id`。

## 研发员工作空间账户

```text
完整的进程环境变量凭证对
> 当前业务项目工作空间 .agentic-ops/.env
```

- email 和 token 必须来自同一来源；不得跨来源拼接账户。
- 一台电脑上的不同业务项目工作空间各自保存账户，互不继承。
- `~/.agentic-ops/user/.env` 不作为研发员凭证来源。
- `show` 只显示账户层级、配置状态、来源和脱敏 email。

## 安全门禁

- Runtime 从 Connection 定义读取准确变量名，不允许用户猜测。
- 凭证文件使用锁、原子替换和 `0600` 权限。
- 输出和诊断不得包含 token、Authorization header 或原始认证响应。
- `verify` 必须回读 Jira 当前用户；认证、站点或 API 能力不满足时禁止真实任务写入。
- Connection 默认由 Project Profile 决定；旧工作空间若仍显式保存 `connection_id`，它只作为一致性校验。
- Connection 与 Project Profile 不一致仍返回 `jira_workspace_mismatch`，授权成功不能绕过项目站点绑定。

## TAP-12289 验收

TAP-12289 是本阶段真实 Tapdata 验收任务。验收顺序：

1. 使用零必填参数入口配置并验证 AgenticOps 研发员账户。
2. 在独立项目 AI 工作空间绑定 `tapdata` Profile，初始化 TAP-12289 状态。
3. 只读核对 Issue ID、项目、类型、经办人、状态和现有 ownership 评论。
4. 对阶段评论与 Worklog 分别执行 `plan -> apply -> readback`。
5. 验证重复执行不会创建第二条记录，`sync.json`、`decisions.ndjson` 和 `journal.ndjson` 可恢复。

Description 只允许修改明确确认的 AgenticOps 受管章节；不得覆盖 TAP-12289 的原始缺陷日志。
