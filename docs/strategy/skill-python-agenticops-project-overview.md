# AgenticOps Skill 与 Python Runtime 驱动项目全景

> **状态：** 目标设计已确认。实施计划、阶段进度、阻塞和验收统一维护在 Jira `AO-11`。
> **目标架构：** `Skill + Python Runtime + Shell Bootstrap + Rule`。
> **实现说明：** Go 与旧安装资源已退出当前工作树；旧版本只由版本分支、Tag 和 Git 历史保留。现役实现与验收均以两个 Python 工作面为准。

## 1. 定位

AgenticOps 把研发任务标准沉淀为 AI 可执行、可审计、可恢复的协作流程：Skill 组织工作，Python Runtime 承载确定性操作，Shell Bootstrap 负责安装与启动，Rule 约束 AI 的行为边界；Jira、Git、GitHub 和 CI 继续承担各自事实源职责。

目标闭环是：

```text
从受保护 main 安装 AgenticOps
-> 在项目 AI 工作空间执行真实 Jira 任务
-> 本地维护细粒度进展和恢复状态
-> Jira 保存团队计划、进度、决策和验收事实
-> 业务任务优先正确完成
-> 任务结束后总结人工干预和输出质量问题
-> 人工确认优化方案
-> 在独立 AgenticOps worktree 完成改进并创建 develop PR
-> 受控发布进入 main
-> 更新正式安装并用原场景复验
```

日常使用以 Tapdata 为主。AO 的 `Agentic 缺陷` 类型和专用流程继续作为 AgenticOps 试验能力，但不强加给 Tapdata 任务。本次重构的设计、实施和验收统一由 `AO-11` 跟进，不拆分额外 Jira 任务。

## 2. 分层职责

### 2.1 Skill

Skill 根据任务类型和阶段选择标准流程，加载当前运行模式允许的 Rule、标准资产和项目映射，调用 Python Runtime 原子操作，组织需要语义理解的分析与审查，并在能力缺口、授权失效、风险扩大或外部事实冲突时停止。

Skill 不直接维护状态，不复制 Runtime 实现，也不得绕过已存在的受控写操作。

### 2.2 Python Runtime

Python Runtime 负责配置、契约、状态、文件锁、原子写入、schema 迁移、外部事实读取与受控写入、人工门禁、授权、幂等、回读、证据、恢复、反馈，以及稳定 JSON 输出和错误码。

项目差异来自标准资产和项目工作空间 overlay，不能硬编码在通用 Python 模块中。

### 2.3 Shell Bootstrap

Shell Bootstrap 只负责以 sparse checkout clone、更新和回滚 `~/.agentic-ops` 的 developer 工作面，安装或定位 `uv`，准备锁定 Python 环境，生成 `ao-work` 包装入口以及轻量环境检查。

Shell 不解析 Jira、工作流、任务状态、证据或门禁。源头仓库发布脚本可以编排 Git、GitHub 和固定验证，但不进入安装后的业务运行时。

### 2.4 Rule 与标准资产

Rule 保存不能由当前任务临场改变的事实源、权限、语言、授权、分支、停止条件和设计红线。标准资产保存公司标准、操作契约、标准流程、策略、运行手册、模板和项目映射，为四个运行层提供配置，不构成第五个运行时。

## 3. 三类工作目录

| 位置 | 职责 | 不得保存 |
| --- | --- | --- |
| AgenticOps 源头仓库 | Skill、Runtime、Bootstrap、Rule、标准资产、设计和测试 | 用户凭证、具体业务任务状态 |
| `~/.agentic-ops` | 稳定 `main` 的 developer-only sparse managed clone、锁定 Python 环境、版本和回滚点 | maintainer 资产、研发员身份、Tapdata / AO 等具体任务资料 |
| 项目 AI 工作空间 | 项目配置、本地源码、worktree、任务状态、报告、证据和反馈 | AgenticOps 全局源码副本、其它项目任务状态 |

具体任务资料统一位于 `<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/`。

`.superpowers/` 只保存 Superpowers 等可选插件的临时状态、检查点、缓存和中间分析，可删除、可重建，不属于正式任务状态或审计。插件确认结果必须同步到 Jira 和 AgenticOps 正式任务状态；未安装插件不得影响主流程。

## 4. 双工作面与规则加载

工作面由目录、独立命令、Python 包、AI 入口、Git remote、仓库根、项目 Profile 和操作要求共同验证，不能只靠 AI 猜测，也不能用 mode 参数切换。

### 4.1 `maintainer`

用于维护 `tapstate/agentic-ops` 或 AgenticOps 改进 worktree，加载：

```text
AGENTS.md
maintainer/AGENTS.md
maintainer/rules/source-maintenance.md
docs/strategy/project-goals.md
maintainer/standards/
maintainer/skills/
./maintainer/bin/ao-maint
```

### 4.2 `developer`

用于 Tapdata、Tapstate 等业务项目任务，加载：

```text
业务仓库自己的 AGENTS.md
developer/AGENTS.md
developer/rules/ai-execution.md
developer/standards/company/
developer/standards/projects/<project>/
任务类型对应 Skill
项目 AI 工作空间 overlay
ao-work
```

该工作面不得加载 AgenticOps 的设计红线、源头维护规则、项目目标、分支发布规则或 AO 专用试验工作流。每个 Skill 必须在标准 frontmatter 的 `metadata.workplane` 声明唯一工作面；入口、仓库或 Profile 不一致时返回 `workplane_mismatch` 并阻断。

业务任务完成并确认优化方案后，才离开业务工作空间并创建 AgenticOps 独立 worktree，通过根 AI 入口进入 maintainer；业务项目规则不得被带入 AgenticOps 源头维护上下文。

## 5. AI 设计红线

根 `AGENTS.md` 与 `maintainer/AGENTS.md` 是 AgenticOps 源头设计与规划的强制入口，至少禁止：

- 创建新的任务事实源替代 Jira。
- 降低人工门禁、专业审查、分支保护或权限边界。
- 混写个人、公司、项目和 AIAgent 规则。
- 把 Tapdata、AO 或单个 Jira 站点差异硬编码进通用 Runtime。
- 把不成熟的 AI 判断直接固化为默认操作。
- 让 Skill、Shell 或 Superpowers 绕过 Runtime 已有受控操作。
- 在没有事实或映射时猜测 Jira 字段、状态、仓库和权限。
- 提交凭证、敏感日志、客户数据或本机私有路径。
- 因重构删除尚未提取的契约、安全门禁、失败码和验收行为。
- 把尚未实现的目标能力描述为当前能力。

聊天或 Jira 中的临时确认不能直接覆盖设计红线。改变红线必须修改规则文件并经过审查和 PR。

## 6. Python 与依赖

- 初始锁定 Python 3.12，由 `.python-version` 声明。
- 两个工作面分别用自己的 `pyproject.toml` 和 `uv.lock` 管理包与依赖；根目录不提供可同时安装两个 Runtime 的混合 Python 项目。
- Shell Bootstrap 定位并要求可信的 `uv`，再按锁文件准备 Python 3.12 和 `~/.agentic-ops/developer/.venv`；当前不会自动下载 `uv`，用户必须先安装 `uv` 或显式提供 `AGENTIC_OPS_UV`。不要求用户预装 Go。
- `./maintainer/bin/ao-maint` 只调用 `ao_maint`；安装后的 `bin/ao-work` 只调用 `ao_work`。
- 两个包、解析器、授权、配置和状态不得互相导入或隐式读取。
- 不使用系统 Python、全局 `pip`、业务项目虚拟环境或业务项目依赖。
- 更新使用 `uv sync --locked --project developer`；维护环境使用 `--project maintainer`，声明与对应锁文件不一致时阻断。
- 首选标准库，第三方依赖必须解决明确问题并有测试。
- 首期支持 macOS 与 Linux，不构建 AgenticOps 自有平台二进制。

## 7. 本地任务状态

```text
<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/
  task.json
  progress.json
  decisions.ndjson
  sync.json
  journal.ndjson
  reports/
    analysis.md
    plan.md
    blocked.md
    verification.md
    review.md
    completion.md
  feedback/observation.md
  runs/<agentic_run_id>/
    summary.json
    evidence/
```

| 文件 | 作用 |
| --- | --- |
| `task.json` | 任务身份和稳定执行上下文，包括完整 Jira 身份、流程、项目与仓库引用 |
| `progress.json` | 当前本地阶段、下一步动作、已完成检查点和阻塞状态 |
| `decisions.ndjson` | 只增记录人工确认、授权、临时校正、否决事项和失效条件 |
| `sync.json` | Jira、Git、GitHub 外部引用、内容哈希、幂等键和回读状态 |
| `journal.ndjson` | 只增记录操作流水、结果、错误码、回读和重试安全性 |
| `reports/` | 供人工审阅和 Jira 汇报的中文材料 |

JSON 保存当前快照，NDJSON 保存不可丢失的历史，Markdown 保存人读材料。Runtime 使用任务级锁、同目录临时文件、flush/fsync 和原子替换。同一 Jira 工作项同时只允许一个活动运行；历史运行只读保留。

本地状态只负责细粒度执行和恢复，不能替代 Jira 团队状态。与 Jira、Git、GitHub 或 CI 冲突时，先回读外部事实并补记、阻断或请求人工处理。

## 8. Jira 协作模型

### 8.1 信息分工

| Jira 载体 | 保存内容 |
| --- | --- |
| Description | 确认后的稳定任务契约：目标、范围、非目标、验收标准和实施计划 |
| Comment | 分析、计划确认、阶段进展、阻塞、验证、PR 和完成轨迹 |
| Status | 团队关注的粗粒度业务阶段，通过项目 Profile 映射 |
| Custom Field | 项目已认可并明确映射、需要结构化查询的稳定结论 |
| Worklog | 真实处理耗时、中文标题和本次耗时包含的工作内容 |

本地细粒度步骤不逐条写 Jira，只在阶段完成、阻塞、范围变化或需要人工决定时汇报。所有外部写入执行 `plan -> apply -> readback`，将外部 ID、内容哈希和回读结果写入 `sync.json`。结果不明确时先回读，不盲目重试。

### 8.2 Worklog 语义

Worklog 只累计实际处理区间，包括任务理解、代码调查、设计、实施、测试、缺陷修复、证据整理和必要汇报；不包括等待人工、等待外部系统、无人处理暂停和 CI 排队。每条记录必须有中文标题并说明具体包含哪些处理，不能在任务结束时估算一个无法解释的总数。

### 8.3 Custom Field 分级治理

Runtime 面向标准字段，项目 Profile 使用稳定 Jira field ID 映射，并声明 `active`、`read_only`、`pending_validation`、`unsupported` 或 `deprecated`。

- Jira 已有合适字段但映射缺失或失效：作为受控配置修复处理，人工确认后更新 Profile，并执行契约测试和真实只读验证。
- Jira 没有合适字段，或需要修改字段语义、类型、Context、Screen、权限、自动化或跨项目约定：创建专题 Jira 工作项治理，Runtime 不自动修改 Jira 元数据。
- 非关键增强字段可以降级到受管 Description 章节或 Comment，并记录 `capability_gap`。
- 影响接管、授权、目标仓库、验收或结构化流程判断的字段缺失必须阻断。
- 未明确声明写入能力时默认只读；禁止按字段名称模糊匹配。

### 8.4 多 Jira 工作空间

Jira 适配分为 Jira Connection、Project Profile 和 Project AI Workspace。凭证按 `connection_id` 隔离，Profile 不保存 token；字段元数据缓存按 `connection_id + project_key + issue_type` 隔离；任务完整身份至少包含 `connection_id`、`jira_issue_id`、`issue_key` 和 `project_key`。

一个项目 AI 工作空间默认绑定一个 Jira Connection。站点、Connection、Profile 或 Issue 事实不一致时返回 `jira_workspace_mismatch` 并阻断写入。Jira Cloud 差异由 adapter capability 表达，未来支持 Data Center 时增加 adapter，不修改任务核心模型。

## 9. 能力调用与缺口降级

```text
Skill
-> 当前模式的 Rule 与标准流程
-> Python Runtime 原子操作
-> 外部事实回读
-> 更新本地状态和 Jira 汇报
```

- 已有 Runtime 操作必须使用，AI、Superpowers 和 Shell 不得绕过。
- 缺少只读能力时，AI 可以用项目允许的只读工具临时调查，但必须记录证据。
- 缺少本地可逆能力时，AI 可以提出一次性方案，经人工确认后执行并形成待固化建议。
- 缺少外部写入能力时默认阻断；只有当前任务必须恢复且人工明确确认时，才允许一次受控写入并强制回读。
- 保护分支、合并、发布、Jira 元数据、权限、密钥和不可逆操作不得临时降级。
- 同类缺口重复出现或边界稳定时，沉淀为 Skill、Runtime、Rule、项目映射或运行手册。

## 10. 业务任务结束后的快速改进

业务任务处理中出现无法自动完成、人工干预过多或输出质量不足时，先通过人工校正确保业务任务正确完成。任务结束后，AI 生成包含问题表现、人工干预、期望行为、脱敏输入、影响范围、建议载体、回归方法、风险和原业务 Jira 引用的改进包。

经人工确认后，AI 可以从最新 `origin/develop` 创建 AgenticOps 独立 worktree，通过根 AI 入口进入 maintainer，完成修改、原场景回归、提交、推送并创建目标为 `develop` 的 PR，停在人工审查。

普通优化不要求重新创建 AO 卡片，可以关联发现问题的原 Jira 工作项；涉及 Jira 元数据、跨项目语义、安全、权限或发布机制时才开专题。本次重构期间统一关联 `AO-11`。

AgenticOps 变更不得混入业务仓库，也不得直接修改 `~/.agentic-ops` 稳定根工作树。正式发布前，业务任务继续使用稳定 `main`。

## 11. 安装、更新、回滚与发布

`~/.agentic-ops` 根工作树固定跟踪受保护 `main`，必须保持干净，不自动 stash Git 跟踪修改。

```text
fetch origin/main
-> 校验目标提交和来源
-> 记录 previous-ref
-> fast-forward 到目标提交
-> uv sync --locked
-> preflight 与固定自检
-> 成功后写 current-ref
```

回滚到 `previous-ref`，按对应 `uv.lock` 重建环境，自检通过后才切换正式入口。更新和回滚不得修改 `user/`、项目工作空间、业务源码、Jira 或 GitHub 状态。精确安装版本以 `main` commit SHA 为事实，版本分支或 Tag 用于人读版本与历史恢复。

普通改进通过任务分支 PR 合入 `develop`；稳定交付通过受控 `develop -> main` PR；Hotfix 从最新 `main` 建分支并通过 PR 回到 `main`，随后同步到 `develop`。`main` 禁止直接提交和推送，合并、发布和 Tag 继续单独人工确认。

## 12. Go 与旧资料退出

旧 Go 源码、module、平台二进制、checksum、构建测试和只服务旧分发方式的资产已从现役结构删除。旧版本只由版本分支、Tag 和 Git 历史保留，不维护 Go/Python 双轨，不恢复 `agentic-cli` 兼容入口。

已提取的行为、契约、错误码、fixture 和安全门禁由 Python Runtime、标准资产与固定验收继续保护。合入 `develop` 和进入 `main` 前都必须执行现役四项固定验证，确认 Python 主链路、developer-only 安装、更新、回滚、故事门禁和发布流程整体可验证。

## 13. 计划与资料治理

- Jira 是实施计划、阶段进度、验收状态和阻塞事项的唯一团队事实源。
- `AO-11` Description 保存本次重构设计摘要、七项实施计划、范围、非目标和验收标准。
- `AO-11` Comment 保存设计确认、阶段进展、验证结果、风险和变更说明。
- 项目 AI 工作空间保存细粒度步骤、恢复点和临时分析。
- 仓库最终不保留顶层 `plans/`，也不把 Jira 计划复制为 Markdown。
- 现有计划中的长期事实迁入 `docs/`、对应工作面的 `standards/`、`rules/` 或测试；未完成的有效工作转入 Jira；已完成、被替代或仅记录历史过程的计划删除，由 Git 历史追溯。
- 清理计划时发现未确认的产品决策，必须在 `AO-11` 提出并暂停相关部分。

## 14. 验证体系

| 验证层 | 证明内容 |
| --- | --- |
| 单元测试 | 状态、配置、映射、错误码、脱敏、文件锁和原子写入 |
| 契约测试 | Skill、Runtime、操作契约、Profile、Rule 和模板一致性 |
| Fixture E2E | 接管、计划确认、实现、恢复、反馈和重复执行，不访问真实外部系统 |
| 真实验收 | Tapdata 主流程和 AO AgenticOps 改进流程 |
| 安装发布验收 | 无 Go 安装、更新、回滚、固定 HEAD、合并事实和正式版本复验 |

现役固定完整验证不使用历史 Go 命令，只执行：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

固定覆盖信息不足、人工门禁、超时、写入结果不明确、重复执行、中断恢复、状态损坏、锁冲突、授权或范围变化、Custom Field 映射缺失、Worklog 回读和 Superpowers 不可用。CI 未配置或未执行必须标记 `not_configured` 或 `not_run`，不得视为通过。

验证证据必须记录命令、输入范围、结果、未执行项和残留风险。实施矩阵与执行结果统一维护在 `AO-11`。

## 15. 成功标准

以下是目标架构的产品级验收标准，不是 AO-11 当前实现完成声明。当前可执行范围以 `ao-work capability list|show` 为准；正式任务接管、恢复、完整审计与真实 Jira 集成在目录标记为 `capability_gap` 时仍不得执行或伪称通过。

- 新研发工程师无需 Go 环境即可从 `main` 安装并完成 Tapdata 代表任务。
- 确定性操作通过 Skill 与 Python Runtime 复用，AI 聚焦语义理解和专业取舍。
- 本地状态可恢复，但不替代 Jira、Git、GitHub 和 CI。
- 人工干预、无法自动完成和输出质量问题能在任务结束后直接形成可验证改进 PR。
- 多 Jira 站点、不同项目 Profile 和凭证相互隔离。
- Worklog 能准确解释实际耗时及所包含工作。
- Superpowers 可选，不形成运行依赖或事实源。
- 所有进入 `main` 的变更经过受保护发布路径。
- 仓库最终只有一套 Python 目标结构，不保留重复标准资产、顶层 `plans/` 或不可运行的 Go/Python 双轨。

## 16. 关联资料

- [AgenticOps 项目目标](project-goals.md)
- [AgenticOps 项目结构](../architecture/project-structure.md)
- [Python Runtime](../runtime/python-runtime.md)
- [项目规则](../project-rules.md)
- [源码发布工作流设计](../architecture/source-release-workflow-design.md)
- Jira `AO-11`：本次重构实施计划、进度和验收事实源
