# AgenticOps 仓库指令

## 目标与必读文档

涉及设计、计划、架构、流程、策略或项目适配变更前，必须先读取 `docs/strategy/project-goals.md`。项目方向以该文档为准；工作项、进度、阻塞和验收写入 Jira，不在仓库新增执行计划。

AgenticOps 是公司级 Agentic 研发基础设施。现役架构基于 ao-gate-poc 的思想：Agent 平台负责执行，Hook 在副作用前拦截，Adapter 转换为 AgenticOps 标准协议，Policy 配置决策，少量 Workflow 工具只保存确定性状态，项目差异单独配置。

## 单一产品架构

- `contracts/`：Gate 请求、判定和 Adapter Manifest 的版本化标准契约。
- `gate/`：平台无关门禁内核，只接受标准操作并执行上下文与策略判定。
- `policies/`：公司级操作策略和流程连续性原则，不写项目特例。
- `workflow/`：任务阶段、授权、CI 和证据等小型确定性工具。
- `projects/<project>/`：项目 Profile、准入规则和 Runbook；项目之间不得混写。
- `adapters/agents/`：Agent Hook 协议薄转换；`adapters/tools/`：MCP、CLI 工具映射。两者都不得复制策略、状态机或项目规则。
- `bootstrap/`：安装、更新、回退和工作目录接线，不实现业务流程。
- `tests/`：产品主链路测试。
- `internal/`：仅供本仓库使用的故事门禁和发布工具，不进入研发安装目录，也不是第二套产品 Runtime。
- `docs/`：当前有效的人读目标、架构、安全、测试和故事合同。
- 根 `agenticops`：中央产品根目录薄入口，只负责安装生命周期、工作目录接线、诊断、修复和启动 Agent，不承载 Gate、Policy、Project 或 Workflow 逻辑。

旧版 AgenticOps 只通过 `v0.7` 和 Git 历史查阅。不得恢复 `maintainer/`、`developer/` 工作面、`ao-work`、`ao_work`、`ao-maint`、Go Runtime、`agentic-cli` 或兼容入口。

## 边界与规则归属

- Jira 是任务事实源，Git 是代码事实源，GitHub PR/CI 是审查和检查事实源；项目工作空间的 `.agenticops/` 只保存初始化信息、工作空间配置，以及按任务隔离的本地执行、恢复、授权和门禁事件。
- 公司通用操作边界进入 `policies/`；项目 Jira、分支、准入和验证差异进入 `projects/<project>/`；平台协议差异进入 `adapters/`；确定性状态逻辑才进入 `workflow/`。
- Hook、MCP 和 Skill 接线是 Manifest 与模板生成的产物，不是规则事实源。Gate 不得出现平台协议字段；Adapter 必须无状态，并通过 `tests/test_adapter_boundary.py` 的文件数、代码预算、禁止依赖和禁止状态写入检查。
- 项目规则优先于 AIAgent 规则，AIAgent 规则优先于公司规则，个人偏好最低。
- 当前仓库规则只约束 AgenticOps 本身，不得把 TapData、TapState 等业务仓库的分支、测试和目录约定反向写入本仓库规则。
- AgenticOps 源码仓库、`~/.agentic-ops` 安装目录和各业务项目工作空间必须分开。
- 源码仓库和安装目录使用相同产品根目录结构和入口；各自的非 Git 本地状态统一放入本产品根目录的 `.local/`。项目工作空间配置与运行数据统一放入 `.agenticops/`，可再生平台接线由初始化清单管理，不得复制中央 Policy、Project Skill 或 Runtime。

## 流程连续性

未迁移的辅助能力不能默认阻塞整个研发流程。优先使用 Agent 原生能力或已配置 Adapter；没有安全自动路径时，只暂停当前副作用步骤并输出结构化人工接力，继续不依赖该步骤的准备工作。

以下情况必须停止，不得因“流程要流畅”而放行：事实不可信、权限不足、风险要求人工决策、外部写入结果不明确，以及合并、发布、Tag、保护分支写入、强推和历史改写。不得静默改策略、改 `.agenticops/` 状态或换工具绕过门禁。

## 语言、安全与文件

- 用户可见文档和 Jira 人可见内容使用中文；字段、命令、协议和代码标识可保留英文。
- 目录和文件默认使用英文 ASCII lowercase-kebab-case。
- 不提交 secrets、tokens、private keys、客户数据或原始敏感日志。
- `.superpowers/` 只属于本机临时状态，不维护、不提交，也不是事实源。
- Skill 只能指导 Agent 使用现役架构，不得承担可由 Policy 或 Runtime 强制的规则。

### Markdown 排版

以下规则仅适用于人工维护的 Markdown 文件（`*.md`），不适用于代码、配置、契约、机器生成文件或其他换行可能影响语义的文件：

- 普通段落不按字符数或显示列数硬拆行，阅读时依靠编辑器软换行。
- 标题、列表项和代码块保持 Markdown 的自然结构，不为满足固定行宽而拆分。
- 空行仅用于语义换段或 Markdown 结构所需的边界，不用于调整视觉行距。
- 表格行、链接和行内代码保持完整，不人为拆开。

## 文档治理

- 新建或调整人读文档前，先更新对应主题的规划总纲：现役产品文档以 `docs/README.md` 为总入口；已有主题目录以其 `README.md` 为就近总纲。总纲必须说明主题目标、范围、文档层级、各文档职责和导航关系，再细化正文或子文档。
- 仅在单篇文档过长并影响阅读或维护，或一段稳定内容需要被多个文档复用时，才拆分子文档。拆分后由总纲链接子文档并说明职责；不因一次性写作步骤、短期信息或零散主题过度拆分，也不在多个页面重复维护同一规范。
- 产品方向、架构边界和术语分别以 `docs/strategy/project-goals.md`、`docs/architecture/agenticops-v1-architecture.md` 和 `docs/glossary.md` 为准。操作指引、安全边界、测试合同与用户故事应链接这些权威来源，而不是复制或改写其规则。
- Jira 继续是工作项、进度、阻塞和验收的唯一事实源；文档只记录稳定的产品规则、操作说明、架构和可复用合同，不新增平行执行计划。

## 测试与验证

运行代码变更必须补充可执行验证。现役固定验证为：

```sh
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```

四项分别覆盖 Gate/Workflow、资源边界、产品安装与工作目录接线、仓库发布治理。正常发布的固定验证由 `internal/release/release.sh` 编排，不得跳过。OPA 未安装时可跳过 Rego 一致性测试，但必须明确记录；产品 Python 运行时保持 Python 3.9+ 且无第三方依赖，`internal/` 的 PyYAML 依赖不得进入产品安装。

## 分支与发布

- 默认分支是 `main`，日常开发分支是 `develop`；`main` 禁止直接提交和普通直推。
- 正常发布使用 `internal/release/release.sh prepare --version vX.Y`，再使用 `publish --version vX.Y`。软门禁模式显式增加 `--allow-soft-gate`。
- `main` 只通过 Merge commit 合入；发布脚本回读合并事实、同步 `develop`，最后才创建指向实际 Merge commit 的 annotated Tag。
- Hotfix 只使用 `internal/release/hotfix.sh <JIRA-KEY>`。该调用是快速修复授权，脚本只允许原子更新 `main` 和 `develop`；冲突、分叉或回读不明时失败关闭。
- 候选修改 Agent Hook、共享 Adapter Runtime、Tool Adapter 分类策略、Git Hook、故事门禁、注册表、锁文件或发布脚本时，自动发布失败关闭，必须通过受保护 `main` 的独立人工审查 PR 完成信任根升级。
- 源码版本格式为 `<分支>-<标签>-<提交数>-<提交编号>`，由 `python3 internal/version.py` 输出；发布版本使用 `v1.x`。

## 提交与授权

提交标题推荐：`<type>(<scope>): <Jira key> <中文主题>`。非平凡提交正文使用中文，说明变更、原因、验证和风险；一个提交只包含一个逻辑变更。

命令或脚本传递多段文本时，必须按目标工具实际支持的参数或文件格式构造；不得将转义序列当作多段文本，并依赖下游进行二次解析。

没有可确认的 Jira key 时不得提交。只有用户明确要求提交，或工作项级连续授权覆盖提交时，才可执行 `git commit`；推送同理。工作项级授权必须绑定 Jira 工作项、运行编号、仓库、工作分支、目标分支、修改范围和验证方式。所有权、范围、风险或必要验证变化时授权失效。合并、发布、Tag、直接修改 `main`、强推和历史改写始终需要新的明确授权。

故事候选先执行固定验收，再形成 commit 或 PR 供人工审查。`impact_id` 只用于内部绑定，不是用户确认对象；commit 或 PR Head 变化后旧确认立即失效。
