# AgenticOps 扩展使用

[PR 正文发布与回读](quality-checkpoints.md#pr-正文发布与回读)负责多行正文的安全传递、只读预检、原生 PR 正文回读比对及局部失败恢复。工具不发送 PR、不新增阶段门禁；项目 Skill 仅负责调用顺序，正文内容及验收事实仍需人工核对。

任务退出范围与 Jira 同步分别由[任务授权指引](task-authorization.md#清理范围决策视图)和[质量检查与证据](quality-checkpoints.md#同步待办与有限回执恢复)说明：前者负责清理、保全、保留和未知对象的用户决策；后者负责接管与检查点的待办、原生执行、回读及终态恢复。状态冻结与兼容边界统一由[工位合同](../architecture/single-task-station.md#外部同步回执恢复)定义，不另建生命周期或同步队列。

[源码定位与实时核验](quality-checkpoints.md#源码定位与实时核验)负责质量事件不保存源码绝对路径、同类实现的历史与实时核验边界、材料缺口与工具故障的处理，以及发送正文检查；兼容代际变更统一遵循[更新与回退](update-and-rollback.md)，不导入旧任务确认。

[更新与回退](update-and-rollback.md)同时负责当前产品版本查询，说明源码、安装和工位入口的版本归属及未提交修改的显示方式；通用工具 Hook 退役导致的跨 epoch 切换也复用该页的原版本退出和重建顺序，不另建迁移入口。

[功能方案完整性与一次决策包](quality-checkpoints.md#功能方案完整性与一次决策包)覆盖同类源码、AC 映射、公共层必要性、交付依赖、环境及验证计划；项目环境的实际取值与核对顺序由现有 TapData Runbook 维护。

质量输入诊断由[质量检查与证据](quality-checkpoints.md#质量输入预检)说明只读格式校验、日志 revision 与未执行原因；启动参数在工位刷新前核验，`station start --help` 不访问工位。默认启动需要终端，自动化调用须显式使用 `--non-interactive` 并传入 Agent 自身支持的非交互参数；包装入口不猜测或改写这些参数。

完成预检归[任务授权指引](task-authorization.md#完成预检与等待合并)说明：`next`、实际推进和释放共用判定，预检只读，实际变更重新核验；PR Ready 与合并完成分开。

[配置化清理入口](task-authorization.md#配置化清理入口)负责 `station-clean.py` 的预检、成果保全、一次范围确认、默认执行和 Agent 接力后的结果验收，并说明中央/项目名单及恢复请求；仅支持计划版本 6，旧现场退出复用[更新与回退](update-and-rollback.md)。验收标准和安全依赖以[工位合同](../architecture/single-task-station.md#成果导向清理计划版本-6)为准。

任务退出的使用目标是保留关键材料并重置工位：[任务授权指引](task-authorization.md)说明目录生产前登记、一次确认、空未初始化子模块及工位 IDE 配置的保留边界和恢复入口，[工位合同](../architecture/single-task-station.md)维护唯一状态语义，工位代际及支持范围以[机器兼容清单](../../contracts/station-state-compatibility.json)为准，清理计划 schema 是独立的格式版本，不能当作工位 epoch；[更新与回退](update-and-rollback.md)说明跨代际必须原版退出、purge 后再重建的边界。

[工位源码与材料](station-materials.md)负责源码池下载加速、独立源码生成、中央共享 Wiki 的显式生命周期及缓存丢失后的使用边界；初始化接线、接管准备源码和实际应用运行保持分离。源码准备成功不等于工具链、数据库、构建产物或应用健康已验证；运行操作依据由 TapData 构建运行指引维护。

质量检查与证据文档统一说明本地有效事实、独立实施分支、简洁 Jira 评论和同步失败恢复；所有阶段的同步警告在 PR 后总结统一展示。

本目录保存首次使用之外的稳定操作说明。[首次使用指引](../usage-guide.md)只覆盖默认路径；遇到不同环境或需要调整默认值时，再按下面的场景进入对应文档。

首次工位初始化必须显式指定 `--project`，已有工位沿用绑定；项目不一致须先退出并 purge，不能通过 init 或 repair 换项目。项目选择示例由[首次使用指引](../usage-guide.md#2-创建项目工位)维护，材料复用与跨版本顺序分别由本目录对应指引说明。

[更新与回退](update-and-rollback.md)负责产品切换前的兼容性预检、旧版本受控解绑、失败恢复和新版本重建顺序；不兼容的空绑定也不能通过 repair 在线采用新代际。单任务工位的状态合同仍由架构文档维护。

[质量检查与证据](quality-checkpoints.md)也负责共同验证材料的字段与检查点绑定、失败归因、跨本地/CI/审查的累计修复轮次、PR 审查意见的逐项处理，以及研发追加轮数或接受具体缺口后的恢复；失败工具只记录和检查条件，修复与重验仍使用原生工具。工位 epoch 以机器契约为准，升级不在线迁移旧任务。

[质量检查与证据](quality-checkpoints.md)同时负责按任务类型选择项目质量配置、方案字段检查及旧授权兼容边界；它说明可复用控制的使用合同，不代表任一业务任务类型已通过真实接入验收。

功能开发的项目合同由 `projects/tapdata/admission.json` 与 `quality-feature.json` 定义，[TapData 任务引导](../../projects/tapdata/skills/tapdata-task/SKILL.md)负责功能与缺陷的协作差异；[质量检查与证据](quality-checkpoints.md)说明共用工具和功能任务的人工 Jira 回读边界，不扩展需求任务规划。

[任务授权指引](task-authorization.md)同时负责已有分支/PR 续办、远端分支的精确 Head 获取、历史基线与版本规划的独立核验及新分支重做：说明何时恢复原 run、何时归档清理后重新接管、如何登记冻结基线内的修改仓库；Git 成果与旧验证证据的边界以架构和质量文档为准。

PR 前检出来源同步也由[任务授权指引](task-authorization.md)说明：原生远端回读、具体 Merge 授权、只读包含关系核对及同步后差异分析与重新验证；不将同步执行写入中央入口或源码池。

| 场景 | 文档 | 何时使用 |
|---|---|---|
| 默认安装 | [Git SSH 安装](git-ssh-install.md) | 已配置 SSH，按受信 `main` 安装到安装目录 |
| 配置必需 MCP | [必需 MCP 配置](mcp-setup.md) | 首次使用 Jira 事实时，连接 Jira/Atlassian |
| 让 AI Agent 安装 | [Agent引导安装指引](agent-guided-install.md) | 从空目录启动 Agent，由它依据现役安装文档安装并初始化项目工位 |
| 无法使用 Git SSH | [gh 一键安装](gh-one-click-install.md) | 通过 GitHub CLI 登录并安装 |
| 复用持久材料 | [工位源码与材料](station-materials.md) | 同版 purge 后显式复用 source/config 和旧工位 archive（若有）；Product Root `.archive/` 独立保留，不导入旧任务状态 |
| 脚本接管与授权 | [任务授权指引](task-authorization.md) | 从空闲工位接管 Jira 任务，完成准入、受控基线、方案确认与实施授权 |
| 缺陷与功能质量协作 | [质量检查与证据](quality-checkpoints.md) | 当前 run 的交互文件分配、影响版本与优先修复线、稳定方案确认、精确代码证据、从 Jira「已链接工作项」识别 Test 用例、按任务类型读取事实、TapTest 状态接纳与本地执行分离、阶段回填、全流程 Jira 字段统一采集与转换重入、PR Ready 三类核对和逐检查点 Jira 回读；复用任务授权及阶段，项目标准从 Project 配置读取 |
| 日常维护安装 | [更新与回退](update-and-rollback.md) | 安装已存在、更新失败、接线漂移或需要回退 |
| 排障与恢复 | [常见问题](faq.md) | 安装、启动、Hook 或已接管任务出现问题 |

[常见问题](faq.md)负责旧 Hook 显式迁移与失败恢复；[任务授权指引](task-authorization.md)负责当前 run/阶段绑定和检查点确认；[质量检查与证据](quality-checkpoints.md)负责当前 run 的交互文件和原生 Jira 调用后的证据回读；[更新与回退](update-and-rollback.md)负责跨工位状态代际的升级前检查与任务清理引导。外部操作不再由通用 Hook 拦截。

授权到期但方案未变时，按[任务授权指引](task-authorization.md)显式续签，保留当前 run 和证据；完成操作中断时，按[常见问题](faq.md)重试原请求，收敛已提交的完成状态，不重新验收或重置任务。

这些文档只说明安装目录和工位。维护 AgenticOps 源码、测试或发布请使用[维护指引](../maintenance-guide.md)。

同一实施范围内更正验收方案后的恢复也归[任务授权指引](task-authorization.md)管理：明确 Q2 再确认及配置更正后的显式 Q1 再确认、原授权和目标摘要绑定、历史保留及拒绝范围，与仅延长有效期的续签分开。

设计前已登记仓库的范围修订由[任务授权指引](task-authorization.md#设计前修订已登记范围)说明：仅调整范围和验证方式，保留分支与历史，撤销旧授权并重新核对质量证据；不支持后期交付回退。

任务进入实现后修订方案或增加允许的仓库，使用[同周期返工](task-authorization.md#同周期方案返工)，保留原成果并撤销旧授权；不通过退出工位重建任务。
