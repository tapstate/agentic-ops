# DE-002 初始化业务项目工作空间

作为公司员工指导员，
我希望通过一个入口确认研发员身份、Jira 项目空间和授权并初始化工作空间，
以便交付一个身份隔离、事实源明确且通过前置检查的 AgenticOps 研发员。

### 触发方式

常规终端入口不要求参数：

```sh
cd <business-project-workspace>
agentic-cli workspace init
```

脚本或 CI 使用完整非交互输入；token 只能从标准输入传递：

```sh
printf '%s\n' "$JIRA_TOKEN" | agentic-cli workspace init \
  --non-interactive \
  --project tapdata \
  --agent-id developer-01 \
  --jira-email developer@example.com \
  --token-stdin \
  --confirm
```

### 前置条件

- AgenticOps Python Runtime 已安装。
- 当前目录是独立业务项目工作空间，不是 `~/.agentic-ops` 或 AgenticOps 源头仓库。
- `standards/projects/<profile>/profile.yaml` 与对应 Jira Connection 已安装。
- 公司员工指导员可以确认当前研发员身份和 Jira 项目空间。
- 本机具备 Jira、GitHub 和目标仓库的只读访问能力。

### 主流程

1. Runtime 枚举已安装 Project Profile；只有一个或当前目录名匹配时提供默认值，否则要求选择。
2. Runtime 生成 `agent_id` 默认值：读取主机名、转为纯小写、把非法字符段替换为 `-`。用户必须确认或修改；最终值只能匹配 `^[0-9A-Za-z_-]+$`。
3. Runtime 从 Project Profile 推导 Jira Connection、站点、Project Key、默认仓库和源码目录，不要求用户输入 `connection_id`。
4. Runtime 读取当前工作空间授权；缺少完整凭证对时，在同一入口询问 Jira email 和隐藏 token，不跨工作空间继承凭证。
5. Runtime 展示工作空间根目录、`agent_id`、Project Profile、Jira 站点、Project Key、脱敏账户、默认仓库和源码目录，由公司员工指导员统一确认。
6. 确认后执行无副作用候选配置预检：
   - 工作空间边界与可写性；
   - 已有配置和覆盖确认；
   - `agent_id` 格式及本机工作空间冲突；
   - Project Profile、Connection 和仓库映射；
   - Jira 凭证完整性、当前身份和 Project 访问；
   - Git 命令、源码目录、远端仓库和只读访问权限。
7. 所有阻断检查通过后准备源码目录，原子写入 Profile overlay、授权文件、`AGENTS.md` 管理块和非权威工作空间索引，最后写入 `.agentic-ops/agent.json` 作为初始化完成标记。
8. Runtime 使用同一候选配置执行初始化后 preflight；通过后输出下一步动作。

### 工作空间身份索引

`$AGENTIC_OPS_HOME/user/workspace-index.json` 只保存 `workspace_root`、`agent_id` 和 Project Profile，用于发现同一台电脑上的身份冲突。它是可重建索引，不保存凭证、不授予权限、不代表研发员，也不得用于跨工作空间自动加载身份。

### 输出

```json
{
  "ok": true,
  "operation": "workspace_init",
  "workspace_mode": "project_execution",
  "agent_id": "developer-01",
  "project_profile": "tapdata",
  "jira_base_url": "https://tapdata.atlassian.net",
  "jira_project": "TAP",
  "jira_account": "de*******@example.com",
  "jira_identity": "<jira-account-id>",
  "repository": "tapdata/tapdata",
  "source_checkout_status": "cloned",
  "preflight_status": "passed",
  "post_preflight_status": "passed",
  "agentic_next_action": "list_assigned_jira_tasks"
}
```

### 失败处理

- 非终端环境使用默认交互入口时返回 `interactive_terminal_required`。
- `agent_id` 非法时返回 `agent_id_invalid`；已被其它有效业务工作空间使用时返回 `agent_id_conflict` 并显示冲突工作空间。
- 已有不同完整配置且未确认时返回 `existing_config_confirmation_required`；半初始化状态允许相同候选配置修复。
- 凭证不完整、认证失败或目标 Project 无权访问时保持未初始化状态，不写 `agent.json`。
- Profile、Connection、Jira Project 或仓库映射不一致时阻断，不允许 AI 猜测替代值。
- 源码远端不可访问或 clone 失败时返回稳定失败码；只有 `agent.json` 写入后才视为初始化完成。

### 验收标准

- `agentic-cli workspace init` 可以在终端以零必填参数开始引导。
- 初始化摘要明确要求确认 `agent_id`、Jira 项目空间、授权账户和源码仓库。
- 默认 `agent_id` 是规范化后的纯小写主机名，最终值只包含 `[0-9A-Za-z_-]`。
- 同一 `agent_id` 不能绑定本机两个有效业务项目工作空间。
- 一个业务项目工作空间只保存一组 Jira 账户，不从共享安装或其它工作空间继承凭证。
- Jira 身份和 Project 访问、GitHub 仓库访问均在写入初始化完成标记前验证。
- 非交互模式必须明确身份、Profile 和确认；token 不出现在命令参数和输出中。
- `.agentic-ops/agent.json` 最后写入，半状态不能通过任务接管前检查。
- 初始化结果包含前置检查和初始化后 preflight 结论。

### 保护行为

- 业务项目工作空间不能位于 AgenticOps 安装目录或源头仓库中。
- `agent_id` 冲突、Jira 授权失败、Project 访问失败或仓库访问失败时禁止初始化完成。
- 工作空间索引不得保存凭证、代表研发员身份或用于授权。
- token 只允许隐藏输入或安全标准输入，输出只显示脱敏账户。
- Profile、Connection、Jira Project 和仓库映射不得由 AI 临场猜测。

### 验收证据

- 零参数交互初始化输出和确认摘要。
- 非交互初始化、非法 `agent_id`、身份冲突和授权缺失测试。
- Jira 当前身份与 Project 只读回读结果。
- Git 远端只读检查与源码准备结果。
- `.agentic-ops/agent.json`、Profile overlay、工作空间授权文件权限和 `AGENTS.md` 管理块。
- `workspace preflight` 输出及敏感信息不泄漏检查。
