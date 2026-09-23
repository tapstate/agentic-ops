# Codex Agent Adapter

该目录通过 Manifest v3 声明 Codex 启动方式、Skill 发现目标与退役产物，不含 Python Hook 或判定转换器，也不生成 `.codex/hooks.json`。原生工具由 Codex 执行，Workflow 检查状态推进条件。旧托管文件只在同 epoch 下经明确确认与归属核验移除；跨 epoch 由原版本退出并 purge 后重建，不能用 repair 在线迁移。合同与保证边界见[工程架构](../../../docs/architecture/agenticops-v1-architecture.md)。
