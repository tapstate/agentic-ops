# Codex Agent Adapter

该目录只转换 Codex Hook 与 AgenticOps 标准协议。工作空间生成 `.codex/hooks.json`，由当前 Codex 自动发现；首次启动须在 `/hooks` 审核并信任。当前按二态能力声明把 `ask` 转换为带人工处理指引的 `deny`；`allow` 不输出 Hook 决策，交由 Codex 继续执行。
