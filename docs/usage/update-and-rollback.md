# 更新与回退

本页负责用户切换产品的顺序与失败边界。工位数据合同见[工位合同](../architecture/single-task-station.md)。不要把“更新成功”“工位生成成功”和“应用验收通过”混为一谈。

## 共享 Skill 资源与接线

安装只包含 `skills/shared/`，维护根和工位通过各自原有受管清单接入共享技能。新增共享技能不修改任务状态或 epoch；同 epoch 旧清单在 `station repair` 时补齐，现有任务与授权保持原语义。`station doctor` 可报告缺资源、缺链接与漂移；修复不覆盖未受管同名文件。

旧版 updater 首次切换到支持共享 Skill 的版本时，可能保留旧 sparse-checkout 范围。此时先在绑定安装产品根再次运行 `./agenticops update`，由新版更新器补齐共享子树，再运行 `station doctor --station <工位路径>` 和 `station repair --station <工位路径>`。产品 HEAD 未变化时只补齐资源，不覆盖原回退指针；不要复制技能到用户目录来补偿缺失。重新执行 update 仍遵守原来的脏树、远端及兼容检查。

新版更新与回退按目标 Git 树选择共享子树，维护 Skill 始终不进入安装。回退后由目标版本的原生 repair 清理其不再需要、且清单归属和目标仍匹配的生成链接；漂移则停止并保留材料。生成与清理沿用原有清单，目标版本不读取或迁移任务数据。该支持不追溯改变旧 updater；缺资源或失败时不宣称共享技能已经可用。

## 第一阶段：同版本生成与清理

执行 `agenticops version` 查询当前产品版本；在源码或工位目录使用 `./agenticops version`，也可用安装入口 `~/.agentic-ops/agenticops version`。输出为 `<分支>-<标签>-<提交数>-<提交编号>`；工位入口显示其绑定 Product Root 的版本，不读取业务仓库版本。产品存在未提交修改时附加 `-dirty`，供诊断使用；接管水印仍拒绝使用脏产品版本。查询不更新产品或工位状态。

先在同一个 Product Root 版本完成闭环：

1. agenticops station init --station <绝对路径> --project tapdata，生成绑定、空 current 和 config/source/runtime；正式档案在绑定 Product Root 的 `.archive/` 中按 run 保存。
2. 在工位中接管任务。处理中不能接新任务；结束后 archive/release 或 archive/clean，精确授权范围以项目 Skill 和 CLI 为准。
3. 当前任务为空、操作完成且 runtime 已清空后，明确执行 agenticops station purge --station <绝对路径> --yes。清理验证生成归属，不删除 source/config、Product Root `.archive/` 和未知用户材料；未知状态导致失败，不能宣称工位已干净。
4. 确认 .agenticops 与受管接线已移除，再初始化。保留目录非空时明确追加 --reuse-materials；这只允许保留既有真实目录，不授权覆盖、删除或导入旧任务。runtime 必须为空，重新生成新的 station_id。

station clean --generated-only 是接线刷新，不是上述解除绑定操作；不能用它证明状态已清空。

## 第二阶段：跨版本编排

升级器只编排产品切换，不实现任务清理逻辑。兼容清单只维护 `station_state_epoch` 一个配置项。升级器在 Git 引用切换前比较当前与目标值：相同可直接更新；不同则必须存在可读取的空工位登记。任一仍登记的工位都会停止切换并列出路径，提示继续使用原版本将已完成任务 release、未完成任务 clean，确认正式档案已经发布，再执行 `station purge`。单独执行 archive 仍占用工位，不满足升级条件。登记缺失、损坏或无法读取同样停止。升级器不读取 current task、operation、runtime 或旧状态；原版任务与旧状态必须由原版处理，不由目标版本猜测迁移。

同 epoch 更新执行 `agenticops update`，随后 doctor 检查、repair 刷新接线。epoch 变化后，研发先在原版本完成任务并解绑全部工位，更新成功后再用目标版本重新 init；不自动 repair、init 或导入旧任务。rollback 只适用于安装目录，并使用相同的“epoch 变化则登记必须为空”规则；产品源码使用 Git 治理流程，不自动移动源码分支。

## 本次首次切换

通用工具 Hook 执行链在 Manifest v3 中退役，原 Hook 的可执行依赖不再随产品安装。该变更提升工位 epoch；即使某个工位已经不生成 Hook，也必须先由原版本退出并 purge，再切换重建。`repair --accept-checkpoint-migration` 只处理同 epoch 的已托管产物，不是跨 epoch 升级入口；人工或全局 Hook 不自动扫描、删除，不能证明归属的文件应保留并由用户处理。

当前 epoch 以机器契约为准，清单不再维护最低 updater protocol、旧 epoch 映射或支持列表。本版不提供旧状态兼容 Runtime：用户先用仍可运行的原版本保存材料、清理旧任务及其受控源码现场、注销旧绑定，然后再更新或重新安装并明确初始化。旧源码池、Git refs、导出材料和 Product Root `.archive/` 不由新版本删除。替换或删除整个 Product Root 前必须先保留或导出 `.archive/`。新升级规则只保护采用此协议后的切换，不追溯保护旧升级器。

若手动替换产品文件或源码分支绕过升级器，新入口拒绝旧状态。应恢复与旧状态匹配的原产品版本完成清理，或先保全旧现场、使用独立空目录安装和初始化；不手改 epoch、注册表或当前状态。

任何清理失败都留在原版本处理，不能自动进入新生成步骤。已有确认只覆盖其明确路径与材料；新增文件、范围变化和未知结果先回读并取得缺失决定。合并、发布、Tag 和保护分支写入仍需独立授权。
