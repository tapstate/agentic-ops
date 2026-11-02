---
name: tapdata-task
description: 在单任务工位执行 TapData Jira 研发任务，覆盖工程接管、设计、多仓实现、开发运行环境、IT、按用户选择处理 TapTest、PR/CI 与归档清理。
metadata:
  product: agenticops
---

# TapData 单任务工位

处理绑定 TapData 工位的研发任务。先读取工位 AGENTS、当前 Project Profile 和准入规则，以 station.json 的 Product Root 与实际安装能力为准；memory 仅作历史线索。本技能负责接管、授权、质量记账、阶段及交付；测试、环境和 Wiki 能力按下表调用，不代替主流程。

## 当前动作与按需资料

先核验当前任务、阶段和在途操作，只读取当前动作对应的资料；恢复跨越多个动作时按实际依赖顺序读取，不把下表当作全部必读清单。

| 当前动作 | 执行前读取 | 应完成的结果 |
|---|---|---|
| 新接管、同 run 恢复或接管待收尾 | [接管与恢复](references/takeover.md)；跨会话/升级接续使用 [ao-task-resume](../../../../skills/shared/ao-task-resume/SKILL.md) | 分类、归属、完整工程基线、原操作和同步待办均已核验；接管不是任务完成 |
| 准入、源码分析、方案或范围返工 | [准入与设计](references/design.md) | 确认实施方案、验收方案和动作授权；按任务类型绑定现役检查点 |
| Jira 字段/状态同步、实现验证、PR/CI、审查返工与完成 | [实施与交付](references/delivery.md) | 逐仓当前版本的真实证据、回读和剩余事项；预检不等于提审完成 |
| 清理、退出或归档失败恢复 | 本页下方清理约束及[清理与退出](../../../../docs/usage/task-authorization.md#清理与退出) | 成果保全、资源与任务占用分别核验，沿原操作收尾 |

新任务的 `task.py takeover --issue-key` 参数、准入和顺序按接管资料与绑定版本 CLI help；恢复不得借新 run 绕过旧在途操作。新工作段先回读 Jira 任务归属与本地占用；当前动作不明确时先只读核验，不能默认重新接管或清理。

## 始终保留的边界

- 写入绑定 issue/run，阶段推进另带 expected-stage，生命周期与范围操作使用 expected-revision 和稳定 operation-id；拒绝后先回读，不换身份重放旧决定。证据使用 interaction-path；任务释放后不写旧运行证据。
- 编码前以一轮方案确认覆盖实施、验收及后续动作授权，已有有效决定复用；范围、风险或验收预期变化时只补缺少的决定并沿现役接口重新绑定，不能自行签发授权或手改状态。
- Q3 是满足 Q2 已选修复后项的首轮事实，使用 auto_checkpoint，不是人工验收；Q4 Manual/Unit 使用当前完整 SHA 的证据及真实验收，TapTest 按项目状态合同。充分 IT、Manual Test 与已有 Test 分别按现役规则核验；风险接受、未执行、零用例和旧 PASS 不能冒充通过。
- 源码、配置和运行产物使用工位 config/source/runtime，正式档案在 Product Root 的 .archive/<run-id>；不复制旧授权、PASS 或未知状态。原 run 保留历史基线；新 run 续办核实历史基线与预期 Head。
- 在有效授权内连续推进编码、验证、提交/推送、PR 和 Jira 回写；task next 只提供建议，不授权或证明完成。合入、发布、Tag、rebase、强推、保护分支写入及来源 Merge 均遵守其独立授权边界。
- 失败只暂停依赖动作；事实不可信、权限不足或外部写入未知时停止相关副作用，沿原操作回读，不重发、不重建 PR。sync_actions 主动按授权处理，completed 后只恢复已有回执，不新建发送。测试、CI、人工验收、Jira 同步和本地阶段分别报告。
- 暂停时将 quality status/task next 的 handoff 转为可执行说明：原因、场景/步骤/预期、逐仓完整 SHA、执行人、环境与证据要求、选项及可继续工作；集中反馈，恢复复用有效决定。日志缺 SHA 先查来源，不能猜测。

## 专项能力

| 需要的能力 | 入口与调用边界 |
|---|---|
| IT 覆盖设计、用例、执行、PR CI 报告分析 | [tapdata-ci-test](../tapdata-ci-test/SKILL.md)，方案、实现验证和 CI 返回时分别按需调用；主流程保存 integration_tests、verification/execute/failures |
| TapTest 用例开发或执行 | 仅用户选择当前会话处理时读 [TapTest 开发指引](../../runbooks/taptest-development.md)；不自动因关联或缺口扩展范围，不进入 Maven 流程 |
| 一般架构和实现知识 | [tapdata-wiki](../tapdata-wiki/SKILL.md)；IT 由测试技能按需查询，复用本轮有效结果，不重复刷新 |
| Docker 开发环境、应用启动联调 | [tapdata-test-env](../tapdata-test-env/SKILL.md)及[开发指引](../../runbooks/tapdata-development.md)；源码就绪不等于环境或应用就绪 |
| 指定连接器编译、注册 | [tapdata-connectors](../tapdata-connectors/SKILL.md)，不为更新单个连接器重建整套环境 |
| 公共库修改 | 先读[公共库升级](../../runbooks/common-lib-upgrade.md)，检查兼容性、release 前置与 Nexus 发布/消费 |

## 清理与归档

使用[清理与退出](../../../../docs/usage/task-authorization.md#清理与退出)的统一入口。先由脚本预检，Agent 集中解释 task/station 范围、源码保全缺口和唯一数据，复用用户有效决定后执行。按工位合同先保全或明确放弃，再归位旧基线并保留仓库供下一任务复用；不使用 rebase，不把锁冲突、权限失败或未保全当删除兜底。任务清理保留配置，工位清理删除用户配置并保留初始接线及可复用源码；归档独立，不是清理前置。现有安装须先核验支持的入口和 epoch，不用新命令处理旧状态。

失败沿原 operation 恢复，先核实外部未知结果，再处理原范围的剩余事项。源码归位或目录删除开始后不换新操作扩大范围。分别说明任务是否解绑、清理是否完成、执行占用是否释放；不手改状态或补造回执。程序自产合法状态无法继续时反馈产品缺陷，外部数据与环境缺项由 Agent 和研发补齐。
