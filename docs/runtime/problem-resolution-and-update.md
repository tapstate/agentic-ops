# AgenticOps 问题修复与同步路径

## 1. 目的

业务任务处理中出现无法自动完成、人工干预过多或输出质量不足时，先把当前任务正确完成，再把问题转化为可验证的 AgenticOps 改进。问题修复不得混用 developer 与 maintainer 工作面。

## 2. 工作面分工

| 阶段 | 工作面 | 入口 | 允许读取 |
| --- | --- | --- | --- |
| 发现与校正 | developer | `ao-work` | 当前业务项目工作空间、该研发员授权、当前 Jira 任务和业务仓库 |
| 形成反馈材料 | developer | `ao-work capability show feedback_bundle`；当前 gap 时人工整理 | 当前任务的最小脱敏证据与明确引用 |
| 设计和实现改进 | maintainer | `ao-maint` | 人工确认的输入清单、脱敏反馈包、AgenticOps 源头仓库 |
| 发布与同步 | maintainer / developer | `maintainer/scripts/*` / developer Bootstrap | 固定发布提交 / developer-only 安装 |

`ao-maint` 不扫描业务工作空间、不读取业务凭证；`ao-work` 不修改 AgenticOps 源头、不执行故事放行或发布。

`developer/**` 虽归属 developer 运行工作面，但它在 AgenticOps 源头仓库中是 maintainer 管理的被维护资源。developer 上报的问题必须在独立 maintainer worktree 修复；业务项目 developer 会话不得修改源头或稳定安装来完成自修。

## 3. 标准闭环

```text
业务任务遇到问题
-> 研发工程师人工校正，完成当前任务
-> 查询 feedback_bundle 能力；当前 gap 时人工生成最小脱敏反馈材料
-> AI 总结问题、人工干预、期望行为和回归方法
-> 人工确认改进方案
-> 创建独立 AgenticOps worktree
-> 根 AI 入口固定进入 maintainer
-> ao-maint 执行故事影响分析和维护验证
-> 实现、回归并创建 develop PR
-> 人工审查和受控发布到 main
-> developer Bootstrap 更新 ~/.agentic-ops
-> 在原业务场景复验
```

普通优化可以引用发现问题的原 Jira 工作项；涉及 Jira 元数据、跨项目语义、安全、权限或发布机制时必须开专题。当前重构统一由 `AO-11` 跟进。

## 4. 输入清单

进入 maintainer 前必须显式提供或人工确认：

- 原业务 Jira key 和脱敏问题摘要。
- 发生问题的 developer 版本 / Git ref。
- 操作、输入类型、实际输出和期望输出。
- 人工介入做了什么，以及哪些内容不能自动化。
- 建议沉淀为 Skill、Runtime、Rule、标准资产、运行手册还是模板。
- 可在本地执行的最小回归方法。
- 不得带入的凭证、客户数据、原始敏感日志和业务私有规则。

不得从本机其它目录、环境变量、旧聊天或其它研发员工作空间自动补齐清单。

## 5. 问题分类

| 类型 | 修复载体 | 验收重点 |
| --- | --- | --- |
| developer Runtime 确定性错误 | `developer/runtime/` | JSON、失败码、副作用回读、任务状态恢复 |
| maintainer 门禁或维护操作错误 | `maintainer/runtime/` | 故事影响、固定验收、源头仓库边界 |
| 流程组织或提示不足 | 对应工作面的 Skill | 唯一 `metadata.workplane`、调用已有原子操作、停止条件 |
| 不可临场改变的约束缺失 | 对应工作面的 Rule | 事实源、授权、语言、分支或隔离边界 |
| Jira 字段或流程映射缺失 | `developer/standards/projects/<project>/` | 稳定 field ID、只读验证、`plan -> apply -> readback` |
| 已知异常恢复路径不足 | `developer/standards/runbooks/` | 可执行检查、重试安全性、转人工动作 |
| 安装、更新或回滚问题 | `developer/bootstrap/` | developer-only sparse 安装、状态保留、回滚 |
| 源头发布问题 | `maintainer/scripts/` | 分支保护、完整验证、人工确认、合并事实 |

跨项目 Jira Custom Field 的元数据、语义、Context、Screen、权限或自动化变化不作为普通映射修复，必须进入专题治理。

## 6. 本地调试与 PR

在独立 AgenticOps worktree 中：

1. 根 `AGENTS.md` 固定进入 maintainer，并加载 `maintainer/AGENTS.md`。
2. 只使用 `ao-maint` 执行维护操作；需要复现 developer 行为时，通过本地 fixture 或显式测试清单调用 developer 黑盒入口，不继承真实业务凭证。
3. 变更命中项目故事时，运行 `ao-maint story impact` 输出候选预警并先完成固定验收；功能、修复和任务分支形成 PR 后审查当前 Head，其它允许分支形成未推送 commit 后审查提交编号。人工确认逐项绑定确认事项、变更点和风险，不面向用户暴露内部 `impact_id`。
4. 验证通过后创建目标为 `develop` 的 PR，停在人工审查。

不要直接修改 `~/.agentic-ops` 验证修复。该目录代表稳定 developer 安装；未发布改进在 source worktree 中测试，发布后再通过 Bootstrap 更新。

## 7. 发布与同步

源头发布入口：

```sh
maintainer/scripts/release.sh prepare --version vX.Y
maintainer/scripts/release.sh publish --version vX.Y
```

发布必须验证：

- maintainer/developer 包、命令和 AI 入口无交叉。
- developer-only sparse 安装不包含 maintainer 资产。
- `ao-work` 可用且没有 `agentic-cli` 兼容别名。
- 更新与回滚不破坏业务工作空间配置和任务状态。
- PR、合并事实、Tag 与审计满足项目规则。

发布进入稳定 `main` 后，研发工程师通过 `~/.agentic-ops/developer/bootstrap/update.sh` 更新 `~/.agentic-ops`。更新只改变 developer 安装，不修改各业务项目工作空间的 Jira 账户、任务状态或源码；首次安装才使用 `developer/bootstrap/install.sh`。

## 8. 失败和停止条件

- 无法脱敏：停止，不创建反馈包或 PR。
- 问题无法归属一个工作面：先澄清边界，不创建共享捷径。
- 需要读取另一工作面的凭证、授权或状态：停止并改用显式脱敏输入。
- 影响故事但没有当前内容指纹的人工确认：停止提交。
- 验证无法覆盖原场景：在 Jira 记录缺口，不发布。
- 外部写入结果不明确：先回读，不重复副作用。
- 范围、风险、所有权或授权变化：原连续执行授权失效，重新确认。

## 9. 验收证据

- 原问题的脱敏反馈包和人工确认引用。
- 变更归属工作面的说明。
- 单元、合同、边界、安装或 E2E 验证结果。
- 故事 `impact_id`、人工确认和同指纹固定验收。
- `develop` PR 与人工审查结果。
- 发布后 developer-only 安装和原场景复验结果。
