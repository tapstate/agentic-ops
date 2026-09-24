# Claude Agent Adapter

该目录通过 Manifest v3 声明 Claude 启动方式、`CLAUDE.md` 指引、Skill 发现目标与退役产物，不含 `PreToolUse` 转换器，也不生成通用工具 Hook。项目 Skill 不保存在 Adapter 中。旧托管 `.claude/settings.json` 只在同 epoch 下经明确确认与归属核验移除；跨 epoch 由原版本退出并 purge 后重建。合同与保证边界见[工程架构](../../../docs/architecture/agenticops-v1-architecture.md)。
