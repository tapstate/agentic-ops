# 初始化 AgenticOps 研发员

本文是业务项目工作空间的人用初始化入口。它只面向 `developer` 工作面；维护 AgenticOps 源头项目请从根 `AGENTS.md` 和 `maintainer/AGENTS.md` 开始。

## 1. 安装共享 developer 运行环境

默认安装目录为 `~/.agentic-ops`。安装物是稳定 `main` 的 developer-only sparse managed clone，不代表研发员，也不包含 `maintainer/`。

从可信的 AgenticOps 源头检出执行：

```sh
bash developer/bootstrap/install.sh
```

安装完成后把命令目录加入当前终端：

```sh
export PATH="$HOME/.agentic-ops/bin:$PATH"
ao-work --help
```

没有 `agentic-cli` 兼容命令，也不需要 Go 或项目自有平台二进制。

## 2. 初始化业务项目工作空间

一个业务项目工作空间代表一名研发员，并只绑定该研发员的一个 Jira 账户。在独立工作空间根目录运行：

```sh
ao-work workspace init
```

交互入口会让公司员工指导员确认：

- `agent_id`；默认取规范化后的纯小写主机名，只允许 `[0-9A-Za-z_-]`。
- Project Profile、Jira 站点和 Project Key。
- 当前研发员的脱敏 Jira email；token 使用隐藏输入。
- 默认代码仓库和源码目录。

Runtime 会先执行工作空间边界、已有配置、身份冲突、授权、Jira Project 访问和 Git 访问等前置检查。任何检查失败都必须停止，不得手工伪造 `.agentic-ops/agent.json`。

## 3. 开始任务前检查

```sh
ao-work workspace preflight
ao-work auth jira show
ao-work auth jira verify
ao-work capability list
```

只有 `preflight` 与授权验证都成功后，才能读取或执行真实 Jira 任务。调用具体操作前继续执行 `ao-work capability show <operation>`；只有 `status=implemented` 且返回明确命令路径时才能调用，`capability_gap` 必须停止或转人工。Jira token 只保存在当前业务项目工作空间 `.agentic-ops/.env`，共享安装、其它业务工作空间和 maintainer 工作面都不得继承。

初始化生成的当前工作空间 `AGENTS.md` 直接承载 developer Rule，并把 developer Skill 复制到 Codex 标准发现目录 `.agents/skills/`。标准资产只通过 `ao-work` 从受信安装根解析；AI 不得把 `developer/...` 当作业务仓库相对路径，也不得读取根项目维护规则或切换工作面。`workspace preflight` 会阻断 Skill 缺失、漂移、额外资产或 maintainer 污染。
