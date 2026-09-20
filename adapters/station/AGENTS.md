<!-- 由 AgenticOps 生成；不要在项目工位直接维护。 -->
# AgenticOps 项目工位入口

本工位是独立项目工作目录，由 AgenticOps 安装目录管理。一工位、一套完整工程、一个当前任务；并发通过多个工位实现。

- 产品根目录：`__AGENTIC_OPS_HOME__/`
- Product Project：`__AGENTIC_OPS_PROJECT__`
- Repository Catalog：`__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/repositories.json`
- 初始化与工位绑定：`.agenticops/init.json`、`.agenticops/station.json`
- 唯一当前任务与操作：`.agenticops/current-task.json`、`.agenticops/operation.json`
- 活动授权与证据：`.agenticops/authorization.json`、`.agenticops/evidence/`
- 持久配置：`config/`；独立源码：`source/<owner>/<repo>/`；唯一运行现场：`runtime/`；正式归档：`archive/<issue>/<run>/`

当前 Project Profile、准入及 Skill 位于：

- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/profile.json`
- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/admission.json`
- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/skills/`

Codex 的 `.agents/skills/`、Claude Code 的 `.claude/skills/` 链接到同一中央项目 Skill，不复制规则。AGENTS、Agent 入口和 MCP 配置只是可再生接线，不是事实源。

## 必需插件的按需配置

必需插件清单位于 `__AGENTIC_OPS_HOME__/adapters/tools/mcp-requirements.json`。首次需要 Jira 事实时检查 `atlassian` 是否可用；缺失、未启用或未登录时，只暂停依赖 Jira 的步骤，说明用途与当前客户端安装/登录入口。不得伪造结果、自行配置全局插件或改用未受控 token/PAT。GitHub MCP、gh 或其它工具由 Agent 按已有授权选择，不作为启动前置条件。

## 当前任务操作

接管、归档、释放、清理是四个生命周期操作。开始前必须先读取当前项目 `.agents/skills/` 或 `.claude/skills/` 中匹配的 Project Skill。memory 只能作为历史线索；当前产品规则和实际 CLI 是依据。Skill 缺失或越界时停止副作用并提示从工位根执行 `./agenticops station repair`。

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
- 构建、测试和应用运行遵循 Project 的资源配方；配置不随任务清除，任务有效配置、Maven local、插件、日志和报告进入唯一 runtime。配套仓源码变更也必须先登记。
- 未完成任务可归档并标记 incomplete；归档后停止开发但仍占用工位。随后经精确确认 clean，复用原档案并追加回执，成功后才解除占用。release 核验交付与项目验收后归档并释放；PR 合并不能替代其它已配置验收。
- 清理必须先核验并停止登记写入者，取得精确清单确认并发布档案；未知文件、路径漂移、未确认的新内容或资源身份不符时保留现场并停止。不得通用 git clean/reset、删除共享缓存或远端对象。保留 Git refs 和正式归档。
- 交互输入、草稿和回读先用 `task.py interaction-path --issue-key <JIRA-KEY> --expected-run-id <run> --name <lowercase-kebab-case.ext> --dir <项目工位>` 获取路径，不散落状态根；允许 json、jsonl、log、md、txt。活动记录仅属于当前 run，正式归档不转成新任务授权。
- 工位级 purge 仅用于任务已释放/清理且操作结束的工位，按生成归属移除接线与受管状态，保留 source/config/archive。原路径重新初始化若保留材料，需明确 `init --reuse-materials`；这不授权覆盖材料、导入有效配置或复用历史证据。runtime 必须为空。
- 不兼容旧工位必须使用原版本保存材料、受控解绑并重建，新版本不解析或迁移旧任务。repair 不跨代际采用；生成/清理先在同版本形成闭环，升级只在干净边界编排，不把清理失败当升级成功。

## 事实、安全与流程检查点

- Jira 是任务事实源，Git 是代码事实源，GitHub PR/CI 是审查事实源；本地状态只服务执行、恢复和证据，不替代外部事实。
- Workflow 在本地状态变更处执行流程门禁。失败后停止依赖步骤并展示原因，不手改状态绕过。原生工具执行仍由平台权限控制，不承诺拦截任意本地写入。
- defect_fix 的 Q1 通过 checklist --json 或 next 读取 repair_strategy；生成 Q2 方案时应用返回的 `planning_guidance`。策略为 advisory，缺失只报告 warning，不增加 Gate/Quality/Authorization 阻断条件。
- 启用 Jira 同步时，task_intake 与 Q4 各按 Skill 尝试一次精确状态流转；失败不阻塞无关本地步骤，外部结果不明先回读。PR Ready 核验当前 PR Head Checks、关联测试和任务检查项；Pull Request Submitted 由责任人处理。
- 用户可见内容使用中文，不保存 token、密钥、客户数据或原始敏感日志。合并、发布、Tag、强推、历史改写、保护分支写入始终需要独立明确授权。
- 未迁移辅助能力只暂停相应副作用步骤并给出人工接力；事实不可信、权限不足、风险须人工决定及外部结果不明必须停止。
