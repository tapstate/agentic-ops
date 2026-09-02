# 必需 MCP 配置

TapData 项目工作空间只要求 `atlassian` MCP 插件，用于 Jira 任务、准入和状态事实。这个清单对应当前 `adapters/tools/mcp-requirements.json`。GitHub MCP、Git、Git SSH、`gh` 都不是必需 MCP：Agent 按当前任务、可用工具和用户授权自行选择。其它 MCP（例如聊天、设计或知识库）同样保持可选。

它是**按需依赖**，不是 `agenticops start` 的前置条件。Agent 第一次需要 Jira 外部事实时检查插件是否可调用；缺失、禁用或未认证时，只停止当前依赖步骤并给出当前客户端的安装和登录入口。Agent 不得自行安装插件、写入全局配置、请求/保存 token，或使用别的工具绕过该步骤。

## Claude Code

`agenticops init` 会在工作空间生成 `.mcp.json`，其中已声明两个远程 MCP。进入工作空间后首次启动 Claude Code：

```sh
./agenticops start claude
```

当 Agent 首次需要 Jira 事实时，Claude Code 会显示项目 MCP 的待审批或认证状态。按 Claude Code 的界面完成审批和登录；不要修改生成的 `.mcp.json`，它会由 `agenticops repair` 重建。

## Codex

Codex 的插件和连接器由当前客户端管理，项目工作空间不会静默修改 `~/.codex/config.toml` 或写入凭据。Agent 在实际需要 Jira 事实但发现工具不可用时，会提示你在 Codex 的插件/MCP 设置中安装或启用 `Atlassian Rovo`，并完成该客户端显示的登录流程。

同一台机器、同一用户下可由客户端复用受管登录态；容器、远程主机或不同 OS 用户是独立信任边界，必须在该环境自行安装和登录，不得复制 token 或用户配置。GitHub 工具没有 AgenticOps 绑定，Agent 可使用当前客户端已提供且获用户授权的方式。

两个客户端都不会因 MCP 已配置而获得额外授权：凭证范围、GitHub 服务器保护、Jira 项目权限以及 AgenticOps Gate 仍然分别生效。
