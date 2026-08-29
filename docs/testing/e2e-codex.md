# Codex 端到端验证

1. 执行 `<产品根目录>/agenticops init --workspace <项目工作空间> --project tapdata --agent codex`。
2. 按当前 Codex 版本支持的 Hook 配置加载生成的示例，再通过
   `<产品根目录>/agenticops start --agent codex --workspace <项目工作空间>` 启动
   Codex；二态能力和 `ask` 降级由
   `adapters/agents/codex/manifest.json` 声明。
3. 执行与 Claude 相同的任务、多仓库、授权失效和证据场景。
4. 重点验证三态中的 `ask` 在 Codex 二态接口中会变成带授权/人工执行指引的拒绝，
   但操作分级、授权绑定和审计事件与 Claude 使用同一事实源。

Codex 适配层不得复制 `policies/operations.json` 或 TapData 规则；平台协议变化只修改
`adapters/agents/codex/`。

自动化基线（在产品根目录或源码目录执行）：

```sh
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```
