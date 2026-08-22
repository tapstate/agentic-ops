# AgenticOps 配置规范

本文定义配置的分类、落点、读取方式和工作面边界。任何配置调整都必须先确认归属 `maintainer` 或 `developer`；不得建立跨面兜底。

## 1. 工作面归属

| 工作面 | 版本化配置 | 本地配置与状态 | 禁止读取 |
| --- | --- | --- | --- |
| maintainer | `maintainer/standards/`、`maintainer/rules/` | `maintainer/.local/` | 业务项目凭证、developer 任务状态 |
| developer | `developer/standards/`、`developer/rules/` | 安装目录 `user/` 与当前业务工作空间 `.agentic-ops/` | maintainer 规则、故事确认和发布配置 |

`shared/` 不保存授权、secret、项目 profile、工作流决策或可变配置。

## 2. developer 配置分类

- 安装授权配置：`user/identity.yaml` 与 `user/.env` 保存当前 developer 安装唯一研发员的身份和 Jira 凭证；`user/ssh/` 与 `user/gh/` 保存可选的安装级 Git SSH 和 GitHub CLI 隔离授权。
- 项目工作空间配置：`.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 和受管配置文件，只保存项目绑定与安装身份引用。
- 项目标准资产：`developer/standards/projects/<project>/`，保存项目字段、流程、仓库和审查映射。
- 公司标准资产：`developer/standards/company/`，保存跨项目硬规定。
- Connection：`developer/standards/connections/` 保存非密钥站点和 API 能力定义。
- secret：只保存到当前 developer 安装的受保护 `user/.env` 或后续受控凭据存储。

一个 developer 安装代表一名研发员并只维护一个 Jira 账户；同一安装下的业务项目工作空间继承该身份和凭证，但项目配置相互隔离。不同研发员必须使用隔离安装。Git 提交身份、Git SSH 远端认证和 GitHub CLI 登录分别建模，不得用 `gh` 账户代替 SSH push actor 证据。

## 3. 来源优先级

developer effective 配置顺序：

```text
当前业务项目工作空间 overlay
> developer/standards/projects/<project>/
> developer/standards/company/
> ao_work 固定默认值
```

进程环境变量默认不作为配置或凭证来源。测试或非交互调用只有通过受控接口显式允许，并完整提供同一来源的账户凭证对时才可使用；不得从本机环境、其它工作空间或旧聊天中猜测补齐。

该顺序只解决值来源，不改变规则优先级：

```text
项目规则 > AIAgent 规则 > 公司规则 > 个人规则
```

## 4. 授权落点

Jira 邮箱和 token 必须来自同一显式来源。token 不得进入 YAML、命令行参数、标准资产、日志、事件或提交内容。

授权通过安装级单入口管理：

```sh
ao-work auth
ao-work auth --show
```

`ao-work auth` 不选择 Connection 或 Project；Connection 由业务工作空间的 Project Profile 推导，workspace/task Runtime 入口负责真实 Jira 身份和权限回读。

Git/SSH/`gh` 执行授权必须显式选择：

- `global`：只回读并复用机器已有配置，Runtime 不写全局 Git、SSH 或 `gh`。
- `installation`：在安装 `user/` 内使用独立 SSH key/config/known_hosts 与 `GH_CONFIG_DIR`；SSH 通过 `ssh.github.com:443` 且不得回退全局 Agent。

模式、执行身份和安装公钥指纹纳入 `install_identity_ref`。已有受管授权不同必须展示脱敏差异并使用绑定当前 `change_digest` 的精确确认；非受管路径、不同 `gh` 账户、既有私钥、自定义 `core.sshCommand` 和宽松权限不得静默覆盖。

## 5. 统一读取入口

developer 功能必须通过 `ao_work` 配置模块读取 effective 配置，不由 Skill、Shell 或单个功能直接解析 YAML / `.env`。人和 AIAgent 通过 `ao-work` 的授权、工作空间和后续配置查询子命令查看脱敏状态。

maintainer 功能只通过 `ao_maint` 读取源头仓库与 `maintainer/.local/`，不得调用 developer 配置模块。

## 6. 变更审查

新增或调整配置前必须确认：

- 唯一工作面和版本化落点。
- 是否包含 secret；secret 只能进入受保护本地凭证存储。
- 默认值、Project Profile、Connection 和工作空间的职责是否清晰。
- 初始化、帮助、操作契约、文档和测试是否同步。
- 是否把提交身份、SSH 远端认证和 GitHub CLI 账户分别校验，且项目验证子进程未继承安装凭证。
- 是否影响现有工作空间 schema、任务接管、证据回写或安装更新。
- 是否会产生跨面读取、跨工作空间继承或环境隐式兜底。

涉及 Jira Custom Field 元数据、字段语义、Context、Screen、权限、自动化或跨项目影响时，必须开专题治理；普通稳定 field ID 映射缺失可作为项目配置修复。

## 7. 测试要求

- maintainer/developer 配置模块无交叉导入。
- 安装授权与业务工作空间各自写入位置、权限和 schema 正确。
- secret 不出现在 YAML、JSON 输出、stderr、日志、事件或提交中。
- 不显式提供输入时不会读取本机环境或其它工作空间。
- Connection、Profile、Project 与 Issue 不一致时返回 `jira_workspace_mismatch`。
- 初始化中断不会留下可被误认为完成的半状态，并允许安全恢复。
