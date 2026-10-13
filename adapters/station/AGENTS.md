<!-- 由 AgenticOps 生成；不要在项目工位直接维护。 -->
# AgenticOps 项目工位入口

本工位是独立项目工作目录，由 AgenticOps 安装目录管理。一工位、一套完整工程、一个当前任务；并发通过多个工位实现。

- 产品根目录：`__AGENTIC_OPS_HOME__/`
- Product Project：`__AGENTIC_OPS_PROJECT__`
- Repository Catalog：`__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/repositories.json`
- 初始化与工位绑定：`.agenticops/init.json`、`.agenticops/station.json`
- 唯一当前任务与操作：`.agenticops/current-task.json`、`.agenticops/operation.json`
- 活动授权与证据：`.agenticops/authorization.json`、`.agenticops/evidence/`
- 持久配置：`config/`；独立源码：`source/<owner>/<repo>/`；唯一运行现场：`runtime/`；正式归档：Product Root 的 `.archive/<run-id>/`

当前 Project Profile、准入及 Skill 位于：

- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/profile.json`
- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/admission.json`
- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/skills/`

Codex 的 `.agents/skills/`、Claude Code 的 `.claude/skills/` 链接到同一中央项目 Skill，不复制规则。AGENTS、Agent 入口和 MCP 配置只是可再生接线，不是事实源。

技能调用遵循[统一帮助合同](__AGENTIC_OPS_HOME__/docs/skill-maintenance.md#技能调用与帮助)：仅提供技能名而无法确定操作，或明确请求帮助时，按合同展示帮助；已有明确行动意图时继续原技能流程。

## 必需插件的按需配置

必需插件清单位于 `__AGENTIC_OPS_HOME__/adapters/tools/mcp-requirements.json`。首次需要 Jira 事实时检查 `atlassian` 是否可用；缺失、未启用或未登录时，只暂停依赖 Jira 的步骤，说明用途与当前客户端安装/登录入口。不得伪造结果、自行配置全局插件或改用未受控 token/PAT。GitHub MCP、gh 或其它工具由 Agent 按已有授权选择，不作为启动前置条件。

## 当前任务操作

任务接管与工作清理、任务清理、任务归档、release 的职责分别由工位合同定义。开始前必须先读取当前项目 `.agents/skills/` 或 `.claude/skills/` 中匹配的 Project Skill。memory 只能作为历史线索；当前产品规则和实际 CLI 是依据。Skill 缺失或越界时，提示从工位根执行 `./agenticops station repair`，不使用越界材料。若绑定产品根中的现役 Project 规则、合同、CLI 与原授权可核验，按同一成果标准接管；规则来源、权限或安全路径无法核验时，只停止受影响的副作用步骤，继续无依赖的准备，不手改状态。

```bash
python3 __AGENTIC_OPS_HOME__/workflow/task.py status --issue-key <JIRA-KEY> --dir <项目工位>
python3 __AGENTIC_OPS_HOME__/workflow/task.py repository context --issue-key <JIRA-KEY> --json --dir <项目工位>
```

- 接管前 current 必须为空且没有未完成 operation；完成任务也必须由研发明确释放，不能自动接新任务。接管失败保留同 run、同 operation 恢复，不新建运行编号伪装重试。
- 接管完成后核验完整工程基线及 source；修改仓另行登记工作分支、目标分支、范围和验证方式。基线与任务交付是两份不同信息，PR/HEAD 更新不改冻结基线。冻结后不得隐式扩展工程集合。
- 接管或继续成功只是流程恢复点，不是默认停点。应继续核验 Jira、补齐准入并形成方案，直到真实的确认、事实、权限或风险阻塞点。
- GitHub API 或网页源码只能标为“远程候选参考”；完整 ref/SHA 与本地独立仓库核验完成后才能声称已核实基线并进入设计评审。方案确认后才签发任务授权，范围变化撤销旧授权。
- 所有任务写请求携带当前 `--expected-run-id`；阶段推进另带 `--expected-stage`；生命周期和范围操作携带 operation ID 与 expected revision。参数从当前上下文读出并固定，拒绝后先核对变化。不得用新 run 补齐迟到请求。
- Agent 从工位根 `./agenticops station start <id>` 启动并在同一会话继续；Git 使用 `git -C <source 中已核验仓库路径>`。不启动嵌套 Agent，不复制会话级当前状态，不修改 Product Root 或其它工位源码。
- 构建、测试和应用运行遵循 Project 的资源配方；配置默认保留，显式选择后才清除，任务有效配置、Maven local、插件、日志和报告进入唯一 runtime。配套仓源码变更也必须先登记。
- 工作清理保留当前任务和授权；任务清理撤销授权并解绑；archive 尽力记录已有结果，不冻结开发或阻塞退出。release 组合归档与任务清理，支持未完成和完成任务，业务完成验收独立。
- 清理直接调用现役脚本，按工位和项目两份清单处理。源码、Git 索引/分支和未选择的配置保留；不执行通用 git clean/reset。未知、身份漂移或写入者未停的对象保留并提示，独立步骤继续；AI 只接手脚本残留和异常，扩大删除范围由用户决定。
- 交互输入、草稿和回读先用 `task.py interaction-path --issue-key <JIRA-KEY> --expected-run-id <run> --name <lowercase-kebab-case.ext> --dir <项目工位>` 获取路径，不散落状态根；允许 json、jsonl、log、md、txt。活动记录仅属于当前 run，正式归档不转成新任务授权。
- 工位级 purge 先编排同版本任务退出，再按生成归属移除接线与绑定；保留 source、未选择的 config 和用户修改/未知对象。原路径重建使用 init --reuse-materials，runtime 残留先由用户处置，不继承旧证据或授权。
- 不兼容旧工位必须使用原版本保存材料、受控解绑并重建，新版本不解析或迁移旧任务。repair 不跨代际采用；生成/清理先在同版本形成闭环，升级只在干净边界编排，不把清理失败当升级成功。

## 事实、安全与流程检查点

- Jira 是任务事实源，Git 是代码事实源，GitHub PR/CI 是审查事实源；本地状态只服务执行、恢复和证据，不替代外部事实。
- Workflow 在本地状态变更处执行流程门禁。失败后停止依赖步骤并展示原因，不手改状态绕过。原生工具执行仍由平台权限控制，不承诺拦截任意本地写入。
- 通过 checklist --json 或 next 读取当前任务适用的准入、质量与方案指导；项目 Skill 解释具体协作节点。advisory 指导不可用、未应用或偏离时只报告 warning，不增加 Gate/Quality/Authorization 阻断条件。
- Jira 同步节点、转换、验收要求与人工交接从当前 Project 配置及 Skill 读取，不固定任务类型、阶段或 Jira 状态名。原生写入后回读；失败不阻塞无关本地步骤，外部结果不明先核对原操作。PR Ready 按项目质量合同核验当前候选的审查与检查事实，不代替合入或任务完成。
- 用户可见内容使用中文，不保存 token、密钥、客户数据或原始敏感日志。来源分支 Merge 到任务工作分支需要具体对象与来源 SHA 的明确授权，可复用方案中仍有效的具体决定；普通任务授权不包含 Merge。PR 合入、发布、Tag、强推、历史改写、保护分支写入始终需要新的明确授权。
- 未迁移辅助能力只暂停相应副作用步骤并给出人工接力；事实不可信、权限不足、风险须人工决定及外部结果不明必须停止。
