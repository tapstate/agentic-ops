# DE-002 初始化业务项目工作空间

作为公司员工指导员，
我希望 developer 安装先完成一次研发员授权，再用一个简洁入口初始化各业务项目工作空间，
以便身份与凭证集中维护、项目配置相互隔离，并在真实任务开始前完成确定性校验。

### 触发方式

首次安装时可把授权参数交给 Bootstrap；安装脚本只转交 Runtime，不自行处理授权：

```sh
printf '%s\n' "$JIRA_TOKEN" | bash developer/bootstrap/install.sh \
  --agent-id developer-01 \
  --jira-email developer@example.com \
  --git-name 'Developer One' \
  --git-email developer@example.com \
  --github-login developer-one \
  --token-stdin \
  --non-interactive
```

安装时未授权，或后续需要更新身份/凭证时，单独运行：

```sh
ao-work auth
ao-work auth --show
```

自动化配置 token 只能走标准输入：

```sh
printf '%s\n' "$JIRA_TOKEN" | ao-work auth \
  --agent-id developer-01 \
  --jira-email developer@example.com \
  --git-name 'Developer One' \
  --git-email developer@example.com \
  --github-login developer-one \
  --token-stdin \
  --non-interactive
```

授权完成后，在业务项目 AI 工作空间运行：

```sh
ao-work workspace init
```

非交互初始化只提供项目和源码配置：

```sh
ao-work workspace init \
  --non-interactive \
  --project tapdata \
  --source-pool-root <pool-root> \
  --confirm
```

### 前置条件

- AgenticOps developer-only Runtime 已安装。
- 当前安装已通过 `ao-work auth` 配置完整研发员身份和 Jira 凭证。
- 当前目录是独立业务项目 AI 工作空间，不是安装目录或 AgenticOps 源头仓库。
- Project Profile 与对应 Jira Connection 已安装。
- 本机具备 Jira、GitHub 和目标仓库的必要访问能力。

### 主流程

1. Bootstrap 安装或更新 developer-only managed clone；授权参数存在时只调用 `ao-work auth`，不存在时按终端能力引导或输出下一步。
2. `ao-work auth` 原子写入安装目录 `user/identity.yaml` 和 `user/.env`，权限固定为 `0600`；token 不进入参数、日志或输出。
3. `workspace init` 从当前安装读取 `agent_id`、Jira 账户、Git 执行身份和 GitHub login，不接收工作空间级身份或凭证参数。
4. Runtime 从 Project Profile 推导 Jira Connection、站点、Project Key、仓库和状态映射，并展示安装身份、项目、源码池及脱敏账户供确认。
5. 确认后执行无副作用预检：工作空间边界、受管路径、已有配置、安装身份指纹、Profile/Connection、Jira 身份与 Project 访问、源码池和仓库访问。
6. 全部通过后准备源码资产，写入 Profile overlay、developer AI 入口、Skill 副本、工作空间本地 `ao-work` 入口和可重建索引，最后写入 schema v5 `.agentic-ops/agent.json`。
7. `agent.json` 只保存项目事实、`install_identity_ref`、`workspace_entry` 和安装入口摘要；不保存 `agent_id`、Jira accountId、Git 执行身份或 token，不生成工作空间 `.agentic-ops/.env`。
8. Runtime 执行初始化后 preflight；通过后才允许读取或操作真实 Jira 任务。

### 旧工作空间处理

- schema v4 及更早格式缺少当前本地入口合同，工作空间 `.agentic-ops/.env` 不再是授权来源。
- Runtime 必须在读取旧工作空间凭证和发送网络请求前返回 `workspace_jira_identity_upgrade_required`。
- 人工先运行 `ao-work auth` 配置当前安装，再明确确认重新执行 `workspace init`。
- Runtime 不自动复制、迁移或删除旧 `.env`；清理由研发工程师在完成凭证轮换和备份决策后处理。

### 输出

```json
{
  "ok": true,
  "operation": "workspace_init",
  "schema_version": 5,
  "agent_id": "developer-01",
  "install_identity_ref": "install:<sha256>",
  "project_profile": "tapdata",
  "jira_project": "TAP",
  "jira_account": "de*******@example.com",
  "repository": "tapdata/tapdata",
  "workspace_entry": ".agentic-ops/bin/ao-work",
  "preflight_status": "passed",
  "post_preflight_status": "passed"
}
```

### 失败处理

- 安装无授权参数且无终端时安装仍成功，返回 `authorization_status=pending` 和 `ao-work auth` 下一步。
- `ao-work auth` 缺字段、token 为空/不合理或安装用户目录不安全时阻断，不产生部分授权结果。
- `workspace init` 缺安装身份或凭证时返回 `install_identity_missing` 或 `jira_credentials_missing`，明确提示 `ao-work auth`。
- schema v4 及更早格式返回 `workspace_jira_identity_upgrade_required`，不回退扫描 PATH 或读取工作空间 `.env`。
- 安装身份指纹、Profile、Connection、Project 或仓库映射漂移时在外部请求前阻断。
- 源码准备失败时不写初始化完成标记；只有 schema v5 `agent.json` 最后写入后才视为完成。

### 验收标准

- 用户只需记忆 `ao-work auth` 和 `ao-work workspace init` 两个入口。
- `ao-work install identity|auth` 与 `ao-work auth jira` 均不可解析。
- Bootstrap 可选传入授权信息；授权实现仅存在于 Python Runtime，可在安装后单独重复调用。
- 有终端且未传授权信息时 Bootstrap 引导授权；无终端时安装成功并提供明确下一步。
- 安装授权写入 `user/identity.yaml` 与 `user/.env`，均为 `0600`；任何输出都不包含 token。
- `workspace init` 不接收身份、email、Git/GitHub 或 token 参数。
- 新工作空间固定 schema v5，只保存 `install_identity_ref`、本地入口绑定和项目配置，不创建 `.agentic-ops/.env`。
- Agent 从业务工作空间执行 `./.agentic-ops/bin/ao-work version` 时，实际解释器和 `VIRTUAL_ENV` 固定属于初始化所绑定 developer 安装的 `developer/.venv`，不继承业务项目或系统 Python。
- schema v4 及更早格式在凭证读取和网络访问前失败关闭，不进行隐式迁移或安装入口推断。
- Jira 身份、Project 访问和仓库访问在初始化完成前验证。
- 初始化结果包含前置检查和初始化后 preflight 结论。

### 保护行为

- Bootstrap 不能实现或复制 Python Runtime 的授权校验、存储和更新逻辑。
- token 不得进入命令参数、输出、日志、工作空间、标准资产或 Git。
- 工作空间缺安装授权、身份指纹漂移或 schema v4 及更早格式时，必须在旧凭证读取和网络访问前停止。
- Runtime 不自动复制、迁移或删除旧工作空间凭证，也不通过兼容命令恢复旧授权入口。
- Profile、Connection、Jira Project、安装身份和仓库映射不得由 AI 临场猜测或用聊天上下文覆盖。
- `agent.json` 必须最后写入；半初始化状态不能通过 preflight 或任务入口。

### 验收证据

- Bootstrap 带授权参数、交互引导、无终端 pending 三类测试。
- `ao-work auth` 交互、非交互、重复更新、脱敏查看和敏感信息不泄漏测试。
- 旧多级命令和 `workspace init` 旧身份参数拒绝测试。
- schema v5 初始化、工作空间本地入口与旧 schema 失败关闭测试。
- Jira 身份/Project 回读、源码准备、Skill/Rule 边界和固定完整验证结果。
- developer 安装、业务工作空间本地入口、安装 venv Python 三段黑盒验收结果。
