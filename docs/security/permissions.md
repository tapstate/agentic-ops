# Agent 最小权限配置

原则：**权限系统里做不到的事，就不需要规则去"禁止"。** 门禁 hook 是第二道防线；第一道防线是凭证本身的最小化。

## Agent 文件系统权限

Source Pool 位于项目工作空间之外，只保存统一维护的主工作树；任务 worktree 位于 `<workspace>/.agenticops/worktrees/<issue-key>/<run-id>/`。Agent 从项目工作空间启动并在同一会话中继续任务，工作空间是 Agent 原生文件系统边界，Source Pool 不加入可写范围。任务状态操作继续显式绑定 workspace 和 issue。

- `repository context --issue-key <issue-key> --json` 在源码分析、实现或恢复前校验当前 run 的租约、规范路径、分支、`base_sha` 和目录摘要；失败时停止任务依赖步骤。
- 当前会话执行 `git commit`、`git push` 等 Git 副作用时，使用 `git -C <repository context 返回的 worktree> ...`。Tool Adapter 把该路径作为标准 Git 上下文；Gate 仅接受工作空间内、与当前 active 任务的 repository、work branch 和 prepared worktree 同时匹配的精确路径。
- 对直接可识别的创建、更新或评论 PR 等分支相关 GitHub 写操作，如需由 AgenticOps 关联任务并验证分支，Bash 调用应将 `workdir` 设为 `repository context` 返回的 task worktree，即使命令已传入 `--repo`。Codex Adapter 将该单次执行目录映射为标准 Git 上下文，Gate 再验证它精确匹配当前 task/run 的 prepared worktree；缺少 `workdir` 时，已识别命令以 `branch_context_required` 停止，不从 PR 正文或重新接管推断任务。命令内 `cd ... &&`、`GIT_DIR` 等无法可靠标准化上下文的形式属于宽门禁未命中路径，交由 Codex 原生权限处理，AgenticOps 不据此推断任务或分支。
- 当前会话能够访问工作空间内的其它 task worktree，因此 task/run 级边界由显式 issue key、授权绑定、Gate、工作分支和 Git 交付范围共同保证；不得把工作空间级沙箱误述为 task/run 级硬隔离。
- linked worktree 的 `.git` 指向主仓库 Git 元数据。平台仍可能要求批准 Git 元数据写入；Gate 也继续独立判断 commit、push、PR 等副作用。目录授权不是任务授权的替代品。

Source Pool 的 clone、fetch 和 worktree 创建/删除应通过确定性 Workflow 或用户从受控终端执行。Git 公共元数据仍位于 Source Pool 主工作树的 `.git/worktrees/`；若平台阻止相关写入，应请求精确目录/命令审批，不能把整个池永久加入全局可写根目录。

## GitHub

### 1. Agent 专用 Fine-grained PAT（推荐每个研发员一枚）

在 GitHub → Settings → Developer settings → Fine-grained tokens 创建，**Repository access 只勾选该研发员负责的仓库**。权限矩阵：

| Permission | 级别 | 用途 | 备注 |
|---|---|---|---|
| Metadata | Read | 必选（所有 token 强制） | |
| Contents | Read & Write | clone / push 工作分支 | 保护分支由 Ruleset 挡 |
| Pull requests | Read & Write | 建 PR、评论、回复 review | |
| Issues | Read & Write | issue 评论（可选） | 不需要可降 Read |
| Actions | Read | 读 CI run / artifact / 日志 | **不给 Write**：不能改 workflow |
| Checks | Read | 读 required checks 状态 | |
| Commit statuses | Read | 读 status | |
| Administration | ——不授予—— | | agent 永远不能改分支保护 |
| Workflows | ——不授予—— | | 不能改 `.github/workflows` |

### 2. Git SSH（用于 clone / fetch / push）

Git SSH 是 Git 传输的替代凭据方式，不是 AgenticOps 的任务授权，也不替代 GitHub MCP、`gh` 或浏览器 API 所需的 OAuth / PAT。无论使用 PAT 还是 SSH，Gate、Rulesets 和任务授权的判定保持不变。

每位研发员、每台设备使用独立、带口令的密钥。私钥只留在设备，不得写入仓库、`.agenticops/`、Agent 配置、环境变量、聊天记录或 CI 变量。仓库角色和组织 SSO 仍由 GitHub 服务端决定；具体配置、验证和撤销见 [Git SSH 授权指引](git-ssh-access.md)。

### 3. 服务器侧强制（不可绕过的硬门禁）

- **Rulesets / 分支保护**（`main`、`develop`、`release/*`）：
  - 禁止直接 push、禁止 force push、禁止删除
  - 至少 1 个独立人工 review、最后 pusher 不能自批
  - required status checks
  - 无 bypass 名单（agent 的账号绝不在 bypass 里）
  - 注意：私有仓库的 Rulesets 需要 **GitHub Team/Enterprise 档**；
    Free 档私有仓库没有服务器侧保护（agentic-ops 已踩过这个坑），
    此时 hook 的 `protected_branch_push -> deny` 是唯一防线，建议升级。
- CODEOWNERS 指定关键路径必须人审。

### 4. GitHub MCP Server（远程，免部署）

```sh
claude mcp add --transport http github https://api.githubcopilot.com/mcp/ \
  --header "Authorization: Bearer <fine-grained-PAT>"
```

MCP 的能力上限 = PAT 的权限，因此上表同时约束 MCP 工具。

GitHub MCP 与其它 GitHub 工具不由 AgenticOps 配置或绑定；Agent 根据当前任务、可用工具和用户授权自行选择。AgenticOps 不向用户配置写入 PAT、OAuth client 或 token。

## Jira（Atlassian Cloud）

### 1. 账号与项目权限（第一道防线）

- 每个研发员一个**独立 Jira 账号**（agentic-ops 的做法，保留）；或用 org 的 **service account**（Atlassian 支持为 service account 管理 API token）。
- 项目 Permission Scheme 里只给该账号：Browse Projects / Add Comments / Edit Own Comments / Transition Issues / Work On Issues (worklog) / Assignable User。**不给** Administer Projects、Delete Issues、Edit Issues（如流程不需要）、Manage Sprints。

### 2. Scoped API token（第二道防线）

Atlassian 账号 API token 现已支持 scope。为 agent 签发时只选：

- `read:jira-work`
- `write:jira-work`

不选 `manage:*`、admin 类 scope。注意 scoped token 走 `https://api.atlassian.com/ex/jira/{cloudId}/...` 端点（不是站点域名直连）。

### 3. Atlassian 官方远程 MCP Server

```sh
claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2
```

OAuth 2.1 交互式授权，**权限自动等于登录账号的权限**——所以第 1 步的账号最小化就是 MCP 的权限边界。写操作（transition / comment / edit）再由本仓库的 hook 门禁二次拦截。

Codex 的 `atlassian` MCP 也使用同一远程端点和 OAuth，具体命令见[必需 MCP 配置](../usage/mcp-setup.md)。

## AgenticOps v1 的三层防线小结

| 层 | 机制 | 挡什么 |
|---|---|---|
| 凭证 | 最小权限 PAT / scoped token / 项目权限 | agent 根本做不到的事 |
| 服务器 | GitHub Rulesets、Jira permission scheme | merge / 保护分支 / 删除 |
| Hook | 本仓库 Hook 门禁（Claude 使用 PreToolUse；Codex 使用其生成的 Hook 接线） | 剩余操作的授权伞 + 人工确认 + 审计 |

Tool Adapter 只对完整身份明确匹配的 MCP 和可可靠解析的 Shell 操作生成 Gate 请求。任意解释器、未登记脚本和未映射工具由 Agent 原生权限流程继续判断，不代表 AgenticOps 已授权；不得通过关闭沙箱、全局放行或扩大凭证权限来消除原生确认。Codex `PreToolUse` 的当前判定格式与能力边界以官方 [Codex Hooks](https://developers.openai.com/codex/hooks) 文档为准。

## 参考来源

- [github/github-mcp-server](https://github.com/github/github-mcp-server)（远程端点与 PAT 说明）
- [PAT 权限讨论 issue #552](https://github.com/github/github-mcp-server/issues/552)
- [Atlassian Rovo MCP Server 入门](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/)
- [atlassian/atlassian-mcp-server](https://github.com/atlassian/atlassian-mcp-server)
- [Service account API tokens](https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/)
- [管理 Atlassian API token（scope 说明）](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
