# 发布检查清单

本文用于审阅 AgenticOps 源头仓库变更是否满足正式研发和发布要求。设计一致性、运行资产、分支保护、完整验证、版本基线、PR 合并事实和审计证据都必须在发布前确认。

## 1. 建议阅读顺序

1. [目标定位](strategy/positioning.md)：确认 AgenticOps 的价值和边界。
2. [项目规则](project-rules.md)：确认项目资料、运行资料、人工门禁和安全约束。
3. [目标全景](strategy/skill-python-agenticops-project-overview.md)：确认现役架构、运行边界和可验证主流程；[历史实现记录](architecture/agenticops-current-design.md)已冻结，不作为现役实现或发布依据。
4. [项目结构](architecture/project-structure.md)：确认仓库目录、全局安装目录和项目 AI 工作空间边界。
5. [故事线总览](user-stories/agenticops-user-stories.md)：确认后续推进遵循“故事线 -> 设计 -> 计划 / 开发 -> 验收”。
6. [项目维护者故事](user-stories/project-maintainer-stories.md)：确认源头仓库维护、标准资产治理、发布、诊断、反馈、回滚和兼容性故事。
7. [研发工程师故事](user-stories/development-engineer-stories.md)：确认安装、初始化、任务接管、恢复接管、任务完成审计和按需反馈分析。
8. [developer AI 入口](../developer/AGENTS.md)：确认业务 AIAgent 如何工作、何时停止、如何回写证据。
9. [操作契约](contracts/operation-contract.md)：确认 AIAgent 能调用哪些受控操作，以及每个操作的输入、输出和副作用。
10. [工作流配置](profiles/workflow-profile.md)：确认如何屏蔽 Jira 事实，并把具体项目流程映射成稳定配置。
11. [Python Runtime](runtime/python-runtime.md)：确认 Python 结构化操作层与 Shell Bootstrap 保持边界；[历史 CLI 运行时](runtime/cli-runtime.md)只用于追溯已删除实现。
12. [反馈闭环](workflows/feedback-loop.md)：确认工作日志如何沉淀为 AgenticOps 改进建议。
13. [源码发布流程](architecture/source-release-workflow-design.md)：确认 `develop`、`main`、Tag 和 Hotfix 规则。

## 2. 必须确认的设计项

- AgenticOps 是 AI 执行控制体系，不替代 Jira、研发工程师、拉取请求审查或 CI。
- 业务任务从已进入迭代、已指定研发工程师的 Jira 卡片开始。
- 研发工程师手动触发任务接管，AIAgent 不能全自动接管任务。
- `tapstate/agentic-ops` 是源码、规则、手册、契约、配置模板和通用文档的源头仓库。
- `~/.agentic-ops` 是 developer-only sparse managed clone，不含 maintainer 运行资产，不代表研发员，也不是具体项目运行目录。
- 具体项目 AI 工作空间才是运行目录，例如 `tapstate`、`tapdata`。
- AI 员工在具体任务中产生的代码、日志、验证结果和任务上下文不能混入全局安装目录。
- AIAgent 必须通过操作契约使用工具，不能直接猜测 Jira 字段、状态或工作流。
- Git 和 GitHub 可以轻封装，但推送、创建拉取请求、合并和发布必须有人确认。
- 工作日志可以生成改进建议，但不能未经人工确认自动改写 AgenticOps 源头规则。
- GitHub 默认分支是 `main`，日常开发使用 `develop`，`main` 只通过 PR 的 Merge commit 合入；硬门禁依赖 Ruleset，软门禁依赖显式人工控制且不得伪装成服务器端保护。
- 正常发布只使用 `maintainer/scripts/release.sh`，Hotfix 只使用 `maintainer/scripts/hotfix.sh`；两个 `publish` 都必须固定执行完整验证并取得最终确认。
- 根 AI 入口只进入 maintainer，业务项目 AI 入口只进入 developer；`ao-maint` / `ao-work`、Python 包、Skill、授权、配置和状态无交叉。
- developer 安装不提供 `agentic-cli` 别名或 `--mode` 工作面切换。
- 正常发布只推送二段式 annotated `vX.Y` tag；Hotfix 复用最近版本基线且不产生新 tag。

## 3. 审阅时重点找的问题

- 是否有文档暗示尚未实现的命令、脚本、配置或适配器已经存在。
- 是否有资料边界混淆，把全局安装目录、源头仓库和项目 AI 工作空间混在一起。
- 是否有 AIAgent 可以绕过研发工程师的人工确认点。
- 是否有 Jira、GitHub、Git 或本地路径的事实被写死在通用规则中。
- 是否有标题、术语或文件说明不利于试点研发理解。
- 是否有故事线缺少失败路径、输出证据或验收标准。
- 是否有故事线缺少保护行为、审核问题、验收证据或关联设计。
- 是否有保护行为缺少对应验收证据，导致好功能以后可能被随意改坏。
- 是否有反馈闭环会导致规则自动自我修改。

故事线审核时，应逐条确认：

- 保护行为是否足够明确，能防止已确认功能被随意改坏。
- 审核问题是否能暴露角色、事实源、权限、门禁和失败路径风险。
- 验收证据是否能证明故事成立，而不是只说明“应该可以”。
- 关联设计是否指向真实存在的设计、规则、契约、配置、模板或运行资产。

## 4. 发布前检查

执行发布前必须逐项确认：

- 当前分支、目标版本和 Jira 绑定正确，工作区干净。
- `core.hooksPath` 指向 Git common directory 中带 `AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1` 标记的入口，远端 `develop` 存在且 GitHub 默认分支为 `main`。
- 默认硬门禁要求 `main` Ruleset 无 bypass、至少 1 个独立批准、最后推送者不能自批、dismiss stale approvals、解决全部 review threads，并要求 Auto-merge 和 Merge commit 配置符合预期；GitHub Free 私有仓库只有显式传入 `--allow-soft-gate` 才允许放宽 Ruleset 与 Auto-merge，并明确接受剩余风险。
- `bash maintainer/scripts/test-python-runtime.sh` 通过。
- `bash maintainer/scripts/test-resources.sh` 通过，且确认旧 Go Runtime、`agentic-cli` 和 `install-resources/` 没有残留。
- `bash developer/tests/bootstrap/test_install_boundary.sh` 通过。
- `bash maintainer/scripts/test-release-workflow.sh` 通过。
- `prepare` 对固定 HEAD 完成四项完整验证，验证失败不创建新 tag，也不生成项目自有平台二进制或 checksum。
- publish 由刷新后的 `origin/main` 基线 Runtime 检查固定 candidate；信任根变更已停止自动发布并转人工审查 PR。
- 最终确认展示的 HEAD、提交列表、版本基线和合并方向正确。
- 软门禁普通发布使用固定 `release/vX.Y`；首次 `publish` 返回状态码 `2` 后未自动合并、未推送 Tag，人工合并后使用原命令恢复并完成第二次完整验证。
- PR 实际以 Merge commit 合入，`origin/main` 包含待发布 HEAD。
- 正常发布的远端 tag 在合并验证后创建且不可变；Hotfix 没有 tag 写操作。
- `.local/release-runs/` 逐级拒绝符号链接、特殊文件和物理越界，原子写入普通 JSON；审计记录正确的 `protection_mode` 和等待/完成状态，且不包含凭证或原始敏感日志。

任一检查不满足时停止发布，按脚本稳定错误码处理，不手工绕过门禁。

## 5. 发布后的下一步

后续推进必须先对齐故事线，再保持 Jira 计划、文档、契约、测试和代码同步。阶段性状态、剩余工作和验收命令只维护在对应 Jira 工作项中。

- 正常发布确认远端 tag 与发布记录一致。
- Hotfix 明确提示并由研发工程师人工把修复同步回 `develop`。
- 若能可靠确认 Jira 编号，推送成功后回写中文变更总结；回写失败只重试评论，不重复推送或发布。
- 保留 PR、Merge commit 和本地 JSON 审计作为发布证据。
