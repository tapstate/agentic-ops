# Codex 端到端验证

1. 执行 `<产品根目录>/agenticops init --workspace <项目工作空间> --project tapdata --agent codex`。
2. 初始化会生成 `<项目工作空间>/.codex/hooks.json`；进入项目工作空间，通过 `./agenticops start codex` 启动 Codex 后，在 `/hooks` 审核并信任该项目级接线。工作空间由根入口自动绑定；二态能力和 `ask` 降级由 `adapters/agents/codex/manifest.json` 声明。
3. 在同一 Codex 会话接管任务、登记多个仓库并执行 `repository prepare`；随后读取 `repository context --issue-key <key> --json`，确认不重启 Agent、不切换工作空间即可在列出的 worktree 中继续分析。
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
