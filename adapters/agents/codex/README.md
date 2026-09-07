# Codex Agent Adapter

该目录只转换 Codex Hook 与 AgenticOps 标准协议。当前 Manifest 不生成 `.codex/hooks.json`；旧托管文件由显式检查点迁移移除。这里保留标准协议转换资产供独立测试，不代表原生工具自动被拦截。当前按二态能力声明把 `ask` 转换为带人工处理指引的 `deny`；`allow` 不输出 Hook 决策，交由 Codex 继续执行。
