# AgenticOps 配置规范

本文定义 AgenticOps 配置项的分类、落点、读取方式和变更审查要求。后续任何配置相关调整都必须先按本文判断，不符合本文的方案必须先形成审查分析，再由研发负责人决策。

## 1. 配置分类

AgenticOps 配置必须先区分四类：

- 环境变量：只用于进程级临时覆盖、CI 或 secrets 注入。
- 全局配置：维护在 `$AGENTIC_OPS_HOME/user/config.local.yaml`，保存用户本机可复用的非密钥应用配置。
- 项目配置：维护在项目 AI 工作空间 `.agentic-ops/config.local.yaml` 或 `.agentic-ops/profile.local.yaml`，保存当前工作空间 overlay 和项目本地差异。
- 标准资产配置：维护在 `install-resources/basic/`，保存公司、项目和 AIAgent 可共享的非个人规则、profile、契约、模板、运行手册和默认值。

不得把密钥、token、private key、原始敏感日志或未脱敏业务上下文写入 YAML、标准资产、日志、事件或提交内容。

## 2. 来源优先级

配置解析优先级固定为：

```text
进程环境变量
> 项目工作空间本地配置
> 个人全局配置
> install-resources/basic 项目资产
> install-resources/basic 公司资产
> agentic-cli 内置兜底
```

该顺序只解决“值从哪里来”，不改变规则冲突优先级。规则冲突仍按：

```text
项目规则 > AIAgent 规则 > 公司规则 > 个人规则
```

## 3. 密钥落点

Jira API token 的持久化落点只有一个：

```dotenv
$AGENTIC_OPS_HOME/user/.env
AGENTIC_OPS_JIRA_API_TOKEN=<api-token>
```

`config.local.yaml` 只保存非密钥应用配置。Jira 配置示例：

```yaml
projects:
  tapdata:
    jira:
      adapter: real
      base_url: https://tapdata.atlassian.net
      email: your-email@example.com
```

Jira Cloud `base_url` 必须使用站点根地址，例如 `https://tapdata.atlassian.net`，不得写成带 `/jira` 的地址。

交互式初始化收到研发负责人确认的本机配置后，应先持久化非密钥配置和 secret，再执行可能耗时或失败的源码下载。workspace overlay、`agent.json` 和 `AGENTS.md` 管理块只在源码准备完成后写入，避免外部操作失败造成“配置丢失但工作空间看似已初始化”的半状态。

## 4. 统一读取入口

外部脚本和 AIAgent 必须通过 `agentic-cli conf <key>` 读取 effective 配置，不直接解析 YAML 或 `.env`。

常用 key：

```sh
agentic-cli conf paths.user_config
agentic-cli conf paths.user_env
agentic-cli conf paths.workspace_config --workspace tapdata
agentic-cli conf jira.base_url --workspace tapdata
agentic-cli conf jira.email --workspace tapdata
agentic-cli conf jira.api_token_configured --workspace tapdata
```

secret 原值默认不得输出。需要判断 secret 是否可用时，使用 `*_configured` 布尔 key。

## 5. 配置变更审查

新增或调整配置前必须完成以下检查：

- 明确配置属于环境变量、全局配置、项目配置、个人配置、标准资产配置中的哪一类。
- 明确配置是否包含 secret；包含 secret 时只能进入 `.env`、进程环境或后续受控凭据存储。
- 明确默认值来自哪里，是否应该沉淀到公司资产、项目资产、个人配置或 CLI 兜底。
- 明确读取入口是否已注册到配置模块和 `agentic-cli conf`。
- 明确初始化表单、帮助信息、操作契约、上手文档和测试是否同步更新。
- 明确配置变更是否影响已有工作空间的预检、任务列表、接管、证据回写或安装更新。

如果实现方案绕过统一配置模块、让单个功能直接解析 YAML / `.env`、新增第二个 token 名称、把 secret 写入 YAML，或让文档与 CLI 行为不一致，必须先停止实现，输出审查分析和推荐方案，等待研发负责人决策。

## 6. 测试要求

配置变更必须补充或更新测试，至少覆盖：

- 初始化后配置文件实际写入位置。
- `agentic-cli conf <key>` 的读取结果。
- 依赖该配置的命令是否能按同一契约读取。
- secret 不出现在 YAML、stdout、stderr、事件、日志或提交内容中。
- 初始化在源码下载失败或中断时保留已确认的本机配置，并允许不带覆盖确认地修复不完整工作空间。
- 文档示例中的 key、路径和默认值与 CLI 行为一致。
