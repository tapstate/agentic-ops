---
name: repair-developer-problem
description: Diagnose and repair a problem reported from the developer workplane by changing developer-owned source resources only from an AgenticOps maintainer source worktree. Use after taking over an AO issue whose affected component is developer Runtime, Skill, Rule, Profile, Runbook, Template, Bootstrap, or tests.
metadata:
  workplane: maintainer
---

# 修复 developer 问题

只在 AgenticOps 源头仓库的 `maintainer` 工作面使用。`developer/**` 是本 Skill 管理的被维护资源；不得因此加载 `developer/AGENTS.md`、切换到 developer 会话或继承业务工作空间身份。

## 输入门禁

1. 使用 `ao-maint takeover <AO-KEY>` 接管或恢复 AO 维护任务。
2. 从 Jira Description、人工确认的脱敏反馈包或版本化 fixture 读取：来源任务、developer 版本、实际与期望行为、最小复现、影响、人工介入、回归方法和缺失事实。
3. 关键事实不足、材料未脱敏或修复范围不唯一时停止实现，先输出最小补齐清单。
4. 任何源码修改前执行 `ao-maint story impact --change-source worktree`。

## 修复流程

1. 在独立 AgenticOps maintainer worktree 固定源头 HEAD、目标分支和现有脏变更。
2. 将问题归入唯一修复载体：`developer/runtime/`、`developer/skills/`、`developer/rules/`、`developer/standards/`、`developer/bootstrap/` 或 `developer/tests/`。
3. 用人工确认的安全输入在源头 worktree 构造最小 fixture；需要调用 `ao-work` 时只使用维护测试创建的隔离安装和假身份，将其视为黑盒。
4. 先形成可失败的聚焦回归，再完成最小闭环修复；Runtime 负责事实、状态、副作用、幂等和安全门禁，Skill 只负责编排与解释下一动作。
5. 同一变更补齐对应工作面的单元、合同、边界或安装测试。不得用 maintainer Runtime 导入 developer Python 包，也不得把维护资产分发进 developer-only 安装。
6. 运行故事影响要求的固定验收；真实业务 E2E 只能在显式提供 Profile、任务、仓库、分支、授权和脱敏输入后执行。
7. 按版本化分支策略停在 commit 或 PR 代码审查，不自动合并、发布、打 Tag 或修改保护分支。

## 硬边界

- 不进入业务项目工作空间修改源码、配置或任务状态。
- 不读取业务凭证、隐藏状态、完整 Jira Description、原始敏感日志或客户数据。
- 不直接修改稳定安装目录 `~/.agentic-ops` 验证未发布修复。
- 不用真实业务身份调用 developer Runtime；维护验证必须使用测试前明确确认的输入清单和隔离环境。
- 不把 `shared/` 当作跨工作面公共代码区；需要跨面合同必须先完成显式准入和隔离测试。
- developer 问题修复完成后仍由 maintainer 受控发布，再由 developer Bootstrap 更新和原场景复验。
