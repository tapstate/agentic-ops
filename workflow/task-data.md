# 任务状态依赖描述

所属模块为 `workflow.task_store`，当前格式版本和完整字段要求以 [task-state.schema.json](../contracts/task-state.schema.json) 为唯一机器合同。需求版本随该 schema 的格式和语义要求维护，不跟随应用版本或 epoch 变化。本文件解释字段来源、修补和生命周期，不复制 schema 字段清单。

任务身份、阶段、成果、历史和仓库引用由任务功能写入；执行控制与 Jira 协调通过各自模块接口使用信封内的数据。归档、清理和其它调用方使用 task_store 接口，不分别转换文件。方案、质量证据、授权和操作回执仍由各自模块管理；本模块不创造或修改它们的业务结论。

`task.py data inspect` 返回本地原数据、来源版本、字节摘要、当前合同与具体差异。已满足合同直接调用正常功能，不运行修补。输出可能包含任务材料，仅供本地分析，反馈时须脱敏。

Agent 先从原合同、Jira 决定、Git 和项目材料核实差异。当前格式修补入口支持：任务顶层的旧字段保值改名到当前缺失字段，以及在含义已经核实不变时采用当前格式版本。它不接受任意候选 JSON，不删除未知字段、不覆盖已有字段、不更改字段值；缺少业务事实须通过所属正常入口补齐。缺失身份、损坏 JSON、未知字段语义、旧多任务目录及无法解释的在途操作需要保全并由维护面处理，不能将此入口宣称为任意历史格式转换器。

修补输入含 `source_digest`、`renames`（旧字段名到当前字段名的对象，可为空）、`analysis_ref`（字段含义和项目来源的核实依据）。先执行 `task.py data preview --input <输入>`，向研发展示差异、原事实和候选，确认后执行 `data apply`，提供 preview 的 `--proposal-digest`、`--decision-ref` 和 `--writers-stopped`。这些声明不认证身份，也不代替实际停写。原字段值、run/revision、控制记录及其它文件保持不变；业务动作仍经过正常接管和质量检查。

应用在既有工位锁内重新校验原字节摘要、当前合同及候选，先将原件和确认保存到 `.agenticops/task-state-backups/<原摘要>.json`，再原子替换。失败保留原件；写入后中断可按同输入回读，数据已变化则重新核实，不能覆盖新现场。修补备份只属于本地恢复材料，不是跨工位归档，任务清理通过任务模块接口回收它，归档恢复仍依赖 Jira/Git。

验收须证明共享调用方能读取同一修补结果、任务事实不变、未确认不写入、并发漂移拒绝、失败保全与重试、清理收尾；格式通过不等于项目任务完成。

## 接管操作依赖

接管恢复需求版本为 1，由 `workflow.station_takeover` 与 `workflow.station_operation` 拥有；公共 operation 的 schema_version 仍为 1，读取和摘要校验以 station_operation.read 为准。它依赖 task_store 核验的同任务 current、Project 的工程 Profile/分支规则、原 clone/fetch 意图及 Git 实际对象；不通过 task data apply 改写操作。

新操作的可选 `takeover_baseline` 复用 [engineering-baseline.schema.json](../contracts/engineering-baseline.schema.json) 保存预检值，不表示 current 已冻结或源码准备成功；它绑定预检时的 Profile、规则及逐仓完整 SHA。旧操作缺失该值先沿原请求完成本次预检并保存，再恢复外部登记与源码准备，不补造历史预检。更正后 `request/request_digest` 是有效请求，`takeover_revisions` 依次保存 before_request/before_digest/after_digest、预期任务 revision 和 decision_ref；相邻摘要必须连续，最后一项绑定当前有效请求，字段缺损或未知副作用拒绝采用。

来源为用户明确的分支决定、Project 配置和 Git 回读。仅 `task.py takeover-correct` 在同一工位锁内核验并原子应用，保留 run/op 及准备回执；同输入重复调用只返回已经保存的修订，再由 takeover 完成后续动作。更正历史属于本地执行恢复材料，随原 operation 经现有清理交接保留与回收，不建立独立备份目录。回退到不理解本合同的版本前必须核验在途操作适用性，不能借旧程序重算已固定的预检结果。具体安全边界由[工位合同](../docs/architecture/single-task-station.md#接管归档与恢复)维护。

## 源码归位依赖

所属模块为 `workflow.station_source_reset`，需求版本为 1；输入由清理预检生成，机器结构见 [station-reset.schema.json](../contracts/station-reset.schema.json) 的 v9 source_reuse。数据来源为任务冻结基线或已记录的保留 HEAD、Git 实际对象、目录身份及成果处置决定；没有冻结基线时仅清理当前已核验 HEAD，并在计划中明确其含义。只理解 v8 的旧程序不能执行 v9，新程序仅沿原语义恢复已确认的 v8，不改写其删除目标。

原操作的 restore 步骤保存精确目录快照与目标 SHA；源码暂存于现有 `.agenticops/cleanup/<operation-id>/restore-<path-digest>`，仅供本操作恢复，不能作为新的开发路径或用户材料目录。归位完成先保存 ready_snapshot，再放回原路径并写 receipt；重试核验私有目录身份或已发布快照，不覆盖后来出现的内容。归位失败不得改为删除，恢复材料在本操作完成前不能清除。源码归位是所属功能的数据操作，公共清理只编排它与运行区删除；不改动授权、任务阶段或业务验收结果。

## 接管归属依赖

所属模块为 `workflow.jira_ownership`，复用四项标记记录的可选 `ownership` 对象保存 Jira site、issue_id 和本地 session_id；仅同一会话的最新成功回读允许继续依赖归属的写入，历史 verified_ref 不能代替当前归属。标记缺失、残缺、异主或身份变化时保留原写入意图并撤销当前证明，原生回读及人工交接通过标记模块接续，不经 task data apply 修改。

旧信封的 jira_coordination 保留原 entries/proof 格式；retire 只在核实旧执行者停写及未知写入结果后追加 retired 依据并清除旧 proof。旧 entries 不再赋予执行权，不生成新协调评论；未知意图未核实前不能清理。归属、评论、状态同步分别使用原模块回执，不增加统一持久状态机。
