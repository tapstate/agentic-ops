# AgenticOps `maintainer` 工作面规则

## 故事质量门禁

AgenticOps 只维护两类项目质量故事：

- 项目维护故事：主角是公司员工指导员，保护源头项目的维护、安装、发布和演进质量。
- 研发工程师故事：主角是业务项目工作空间代表的研发工程师，保护安装、授权、任务执行、恢复、证据和审计质量。

维护源头仓库时，任何代码、规则、Skill、标准、测试或文档变更都必须先执行 `ao-maint story impact`。Runtime 命中故事保护路径后，AIAgent 必须停止连续自动化，只允许输出影响报告和运行既有验收。

直接修改故事文档、故事注册表、保护路径、验收检查或证据要求时，现有任务级连续执行授权失效。只有公司员工指导员确认当前 `impact_id`，以与当前影响严格一致的 `user-confirmation:<KEY>:<impact-id>` 留下可审计引用，并完成同一 `impact_id` 的固定验收后，才允许提交。maintainer 没有 Jira 评论回读能力，不接受 `jira-comment`；任意非空字符串、旧 `impact_id`、旧版审批记录或仅有任务级授权都不能作为故事批准依据。

禁止行为：

- 不得使用 `git commit --no-verify`、临时修改 Hook、删除注册表或伪造本地确认记录绕过门禁。
- 不得由 AI 猜测缺失的故事映射并默认放行。
- 不得把任意 Shell 命令写入故事注册表；验收只能引用 Runtime 固定白名单。
- 不得把 Jira 计划、当前进度或临时实现状态写入故事质量合同。

治理范围内路径缺失故事映射时，返回 `maintenance_story_mapping_missing` 并请求公司员工指导员处理。

Git 必须通过 common directory 中的 trusted launcher 从已接受 `HEAD` 加载版本化 Hook，再由 `HEAD` Runtime 检查隔离 index 快照。不得把工作树或 candidate 的 Hook、`ao-maint`、故事 Runtime、注册表或发布脚本作为自己的信任证明。发现未暂存门禁差异时立即停止。

`release` / `hotfix publish` 只允许刷新后的 `origin/main` 基线 Runtime 检查固定 candidate。基线缺失时返回 `release_story_gate_baseline_upgrade_required`；候选修改 Hook、门禁 Runtime、注册表或发布脚本等信任根时返回 `release_story_gate_trust_root_changed`。两者都必须改走受保护 `main` 的独立人工审查 PR，不能由自动 publish 放行。

本地 Hook 是防误操作层，无法阻止本机控制者使用 `--no-verify` 或修改 Git 配置。硬门禁必须由无 bypass 的 `main` Ruleset 强制至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals 和解决全部 review threads；即使 candidate 删除仓库内门禁调用也不能自动合并。`origin/main` 发布基线负责确定性复检；不得把单一本地 Hook 或仓库内脚本描述成不可绕过的信任根。

## 维护任务处理约束

maintainer 面处理任何 Jira 任务（建卡、实现、修复、规则演进等）时，必须遵守以下处理流程：

1. 开始处理任务：先向任务写一条中文评论（说明开始处理的内容与计划），再把任务状态流转为「正在进行」。
2. 合并代码后：向任务写一条中文总结评论（说明实现内容、验证结果与风险），再把任务状态流转为「已完成」。

评论与状态流转必须通过 `ao-maint jira` 的 plan → apply → readback 门禁执行，并留下 `user-confirmation:<KEY>:<plan_id>` 授权引用与决策审计记录。不得绕过 Runtime 直接调用 Jira REST API 建卡、评论或流转状态；发现能力缺口时先补齐 Runtime 能力，再执行任务操作。

评论正文必须按公共评论模板（`shared/standards/jira-comment-template.schema.json`，人读版见 `docs/templates/evidence-templates.md`）组织：`progress`（进度上报/开始处理，重点=进度与任务状态）与 `evidence`（结果反馈/完成总结，重点=总结）评论必须包含 Schema 声明的全部必填键（运行 ID、当前阶段/已完成动作/执行计划/风险 或 完成内容/验证结果/残留风险/已输出表单字段），缺失即被 `ao-maint jira comment plan` 阻断。执行者与工作空间只在任务接管评论声明（代表哪个 Agent 正在处理），字段为执行者 agent_id、Agent 类型、模型、接管环境、运行 ID；身份来自 `ao-maint install identity set` 配置（plan 输出带出 agent_id/agent_type/model/environment）。写评论前先读取任务 Description 与模板提取事实，不得只写一句话或凭记忆临场发挥。
