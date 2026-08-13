# 项目维护者故事

## 1. 范围

本文是 AgenticOps 项目维护者故事索引。项目维护者是维护 `tapstate/agentic-ops` 源头仓库、安装资源和标准规范的人，负责让 AgenticOps 的设计、契约、运行资产、CLI、测试和文档持续一致。

详细故事按单文件维护，便于逐条审核、评论和变更追踪。本文只维护索引，不记录实施计划、勾选项、当前完成度或剩余工作。

## 2. 故事索引

| 编号 | 故事 | 审核重点 | 文件 |
| --- | --- | --- | --- |
| PM-001 | 维护故事线、设计和计划边界 | 故事线、设计、计划和实现状态不混写 | [pm-001-document-boundary.md](project-maintainer/pm-001-document-boundary.md) |
| PM-002 | 维护操作契约、标准流程和工作流配置 | AIAgent 不直接猜测 Jira / GitHub / Git 底层事实 | [pm-002-standard-assets.md](project-maintainer/pm-002-standard-assets.md) |
| PM-003 | 构建 AgenticOps 安装资源 | 安装资源、校验和、安装和审计可验证 | [pm-003-release-assets.md](project-maintainer/pm-003-release-assets.md) |
| PM-004 | 诊断问题并选择修复载体 | 问题能归入 CLI、profile、policy、补卡、release / update 等修复载体 | [pm-004-problem-diagnosis.md](project-maintainer/pm-004-problem-diagnosis.md) |
| PM-005 | 处理反馈并形成改进建议 | 反馈只形成 proposal，不自动修改源头规则 | [pm-005-feedback-proposal.md](project-maintainer/pm-005-feedback-proposal.md) |
| PM-006 | 治理 latest 更新、回滚和兼容性 | latest-only、阻断范围、回滚和审计边界清晰 | [pm-006-release-governance.md](project-maintainer/pm-006-release-governance.md) |
| PM-007 | 守护两类项目故事质量基线 | 代码影响故事后停止自动化，确认和验收绑定同一 Git 内容 | [pm-007-story-quality-gate.md](project-maintainer/pm-007-story-quality-gate.md) |

## 3. 审核方式

审核每个故事文件时，重点检查：

- 保护行为是否足够明确，能防止已确认功能被随意改坏。
- 审核问题是否覆盖角色、事实源、权限、门禁和失败路径风险。
- 验收证据是否能证明故事成立。
- 关联设计是否指向真实存在的设计、规则、契约、配置、模板或运行资产。
