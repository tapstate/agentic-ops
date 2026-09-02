# Codex 端到端验证

1. 执行 `<产品根目录>/agenticops init --workspace <项目工作空间> --project tapdata --agent codex`。
2. 初始化会生成 `<项目工作空间>/.codex/hooks.json`；进入项目工作空间，通过 `./agenticops start codex` 启动 Codex 后，在 `/hooks` 审核并信任该项目级接线。工作空间由根入口自动绑定；二态能力和 `ask` 降级由 `adapters/agents/codex/manifest.json` 声明。
3. 在同一 Codex 会话接管任务、登记多个仓库并执行 `repository prepare`；随后读取 `repository context --issue-key <key> --json`，确认不重启 Agent、不切换工作空间即可在列出的 worktree 中继续分析。对直接调用的 `gh pr create`、`gh pr edit` 等分支相关 GitHub 写操作，如需 AgenticOps 验证任务分支，在 Bash 调用中将 `workdir` 设为该命令所属任务的 worktree；同仓库多个 active 任务而该单次执行目录缺失时，应收到 `branch_context_required`，不得重新接管或从 PR 正文推断任务。验证 workdir 与 task/run 的 prepared worktree 精确匹配，不能借用任意同仓库、同分支目录。另验证 `cd ... && gh ...` 等命令内上下文切换不会被错误映射为 Gate 操作，而是交还 Codex 原生权限。
4. 执行与 Claude 相同的多仓库、授权失效和证据场景，确认当前会话没有访问工作空间外 Source Pool 的需求。
5. 重点验证三态中的 `ask` 在 Codex 二态接口中会变成带授权/人工执行指引的拒绝，但操作分级、授权绑定和审计事件与 Claude 使用同一事实源。

Codex 适配层不得复制 `policies/operations.json` 或 TapData 规则；平台协议变化只修改 `adapters/agents/codex/`。

自动化基线（在源码产品根目录或安装产品根目录执行）：

```sh
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```
