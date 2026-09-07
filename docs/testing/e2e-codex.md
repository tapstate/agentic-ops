# Codex 端到端验证

1. 执行 `<产品根目录>/agenticops init --workspace <项目工作空间> --project tapdata --agent codex`。
2. 核对新工作空间不存在 `.codex/hooks.json`，启动后原生 Git/Jira/PR 不产生 AgenticOps 工具门禁。
3. 在同一会话接管和准备多仓 worktree，所有写命令绑定 expected-run-id；advance 另绑定 expected-stage。重复请求和旧 run 必须失败，缺准入、方案确认或当前提交证据不能推进。
4. 在独立旧工作空间夹具中验证 start/repair 保留旧 Hook；显式 `repair --accept-checkpoint-migration` 才移除哈希匹配的托管文件，任一修改过的文件必须保留。
5. 验证 Jira 同步准备幂等、未知结果可以只读回读恢复，本地阶段不随外部调用自动推进。

Codex 适配层不得复制 `policies/operations.json` 或 TapData 规则；平台协议变化只修改 `adapters/agents/codex/`。

自动化基线（在源码产品根目录执行；安装目录不含 internal）：

```sh
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```
