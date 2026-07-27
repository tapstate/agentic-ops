# AgenticOps 故事线总览

## 1. 目的

本文用于确定 AgenticOps 的故事线分类和推进顺序。故事线只描述角色、目标、触发方式、关键输出、失败路径和验收口径，不记录实现计划、勾选项、当前完成度或剩余工作。

AgenticOps 后续推进必须按以下顺序展开：

```text
确定故事线
-> 确定设计
-> 制定计划并开发
-> 按故事线验收
```

设计文档承载稳定设计事实、角色责任、事实源、能力边界、门禁和安全约束；计划文档承载任务拆解、实施顺序、勾选项、验证命令和当前状态。

## 2. 故事线分类

AgenticOps 的故事线分为两类。

| 故事线 | 主角 | 关注点 | 详细文档 |
| --- | --- | --- | --- |
| 项目维护者故事 | 维护 `tapstate/agentic-ops` 源头仓库的人 | 定义标准、维护契约、发布版本、处理反馈、治理兼容和回滚 | [项目维护者故事](project-maintainer-stories.md) |
| 研发工程师故事 | 在具体业务项目中使用 AgenticOps 管理 AIAgent 执行 Jira 任务的人 | 安装、初始化、配置工作空间、接管任务、确认人工门禁、验收任务证据 | [研发工程师故事](development-engineer-stories.md) |

AIAgent 和 `agentic-cli` 不作为第三类故事主角。AIAgent 是流程执行者，`agentic-cli` 是受控运行时；它们出现在两类故事线的流程和验收中，用来表达执行动作、门禁、副作用和结构化输出。

## 3. 推进门禁

### 故事线门禁

新增或修改故事线时，必须先明确：

- 主角是谁。
- 主角要完成什么目标。
- 触发方式是自然语言、CLI、发布流程还是审阅流程。
- 成功时输出什么结构化结果、证据或资产。
- 失败时如何阻断、提示人工动作或回滚。
- 验收时使用真实外部系统、接近真实演示还是本地模拟回归。

每条可审核故事必须补充以下字段：

- 保护行为：该故事已经确认后不能被随意改坏的用户可见行为、门禁、副作用或输出。
- 审核问题：研发工程师或项目维护者审阅故事时必须回答的问题。
- 验收证据：证明该故事成立的命令、演示、结构化输出、日志、审计记录或人工确认材料。
- 关联设计：该故事依赖的设计、规则、契约、配置、模板或运行资产。

### 设计门禁

故事线确认后，才能调整设计。设计调整只描述稳定边界，包括：

- 角色和责任边界。
- 事实源。
- 数据流和状态流。
- 操作契约。
- 工作流配置。
- 策略门禁。
- 审计和反馈模型。

涉及产品形态、流程权限、自动化程度、发布权限、事实源归属或冲突裁决的缺口，必须明确提示用户决策，不能写成默认设计或默认计划。

### 开发门禁

设计确认后，才能进入计划和开发。计划文档必须写在 `plans/`，并包含任务拆解、勾选项、验证命令和需要同步更新的文档、契约、运行资产或测试。

### 验收门禁

验收必须回到故事线，而不是只看代码模块或文档文件是否存在。

- 项目维护者故事验收：维护者能否按规则更新源头资产、发布版本、处理反馈、完成回滚和审计。
- 研发工程师故事验收：研发工程师能否完成安装、初始化、任务接管、恢复、证据回写、人工确认和任务审计。

本地 fake flow 只作为自动化回归验证；对外演示和正式试点必须使用真实 Jira 卡片。

## 4. 当前故事线索引

### 项目维护者故事

- [PM-001：维护故事线、设计和计划边界](project-maintainer/pm-001-document-boundary.md)。
- [PM-002：维护操作契约、标准流程和工作流配置](project-maintainer/pm-002-standard-assets.md)。
- [PM-003：构建 AgenticOps 安装资源](project-maintainer/pm-003-release-assets.md)。
- [PM-004：诊断问题并选择修复载体](project-maintainer/pm-004-problem-diagnosis.md)。
- [PM-005：处理反馈并形成改进建议](project-maintainer/pm-005-feedback-proposal.md)。
- [PM-006：治理 latest 更新、回滚和兼容性](project-maintainer/pm-006-release-governance.md)。

### 研发工程师故事

- [DE-001：安装 AgenticOps](development-engineer/de-001-install.md)。
- [DE-002：初始化项目 AI 工作空间](development-engineer/de-002-workspace-init.md)。
- [DE-003：初始化 AIAgent 能力](development-engineer/de-003-agent-init.md)。
- [DE-004：新任务接管](development-engineer/de-004-takeover-task.md)。
- [DE-005：恢复接管任务](development-engineer/de-005-resume-takeover.md)。
- [DE-006：任务完成审计与反馈分析](development-engineer/de-006-task-audit-feedback.md)。
