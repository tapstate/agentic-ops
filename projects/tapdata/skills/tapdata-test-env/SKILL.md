---
name: tapdata-test-env
description: 配置、部署、更新、检查、切换或卸载 TapData 工位 Docker 开发环境；复用多份命名配置，每工位一个活动环境，按节点选择 TM、FE、APIServer，使用宿主机构建的完整 Launcher 和指定外部 MongoDB。不管理生产环境、数据库生命周期或源码分支。
metadata:
  product: agenticops
---

# TapData 开发环境

先读取工位绑定和[开发环境指引](../../runbooks/test-environment.md)。当前工位 config 保存多份配置，runtime 只保留一个活动现场；各配置共享当前工位源码，不把配置名称解释为分支或独立源码。使用工位绑定的 Product Root 中的项目脚本，不复制中央脚本到工位，不修改 `.agenticops/` 状态。

## 空闲工位连接已有 TM

用户要求在空闲工位部署 develop FE 并连接已有 TM 时，先按[外部 TM 设计与交接合同](../../runbooks/test-environment.md#空闲工位连接外部-tm-的目标设计)核验工位空闲、源码和有效授权。使用 prepare-source 准备当前 develop 对应的完整工程；该入口不创建业务 run，已有 dirty、ignored 生成物、非目标分支或分叉先按原合同处理，不能覆盖。两个 FE 共用一次完整构建，不为每个节点分别构建或裁剪 TM Jar/WebUI。

prepare-inputs 增加 --external-tm-url <服务根地址> 生成双 FE 草稿，复用现有命名配置与秘密文件；Agent 从当前源码核验 preparation 的 fe_heartbeat 映射。确认对应 Mongo、产品模式、版本、许可证与加入调度的影响，禁止从 TM 地址猜数据库或切到 Cloud token 模式。外部模式保留既有库，不执行下节本地 TM 的清库交互，也不传 --reuse-mongo。普通构建和生命周期继续沿原入口；HTTP 可达及 ready 不能代替许可、WebSocket/认证和业务验收，按 Runbook 分层补证。

后续接管业务任务前先说明停止两个 FE 的影响，复用有效退出授权；完成卸载、必要诊断保全和无任务时的任务清理（先预检并确认 source 源码及运行材料的保全或放弃），核验 runtime 及原接管条件后再接管。无授权则保留环境；失败保留现场，不自动继承容器或伪造任务状态。

## 新配置：集中输入 → 填写后检查 → 自动部署

先回读 `list` 和工位已有配置，区分首次部署、新增命名配置与更新活动环境。已有事实和有效授权直接复用；用户只需要说明运行架构与配置，不要求填写编译命令、制品路径或项目准备文件。输入和秘密文件合同以[开发环境指引](../../runbooks/test-environment.md#首次部署输入与配置命名)为准。

旧配置复用遵循[转换触发时机与处理顺序](../../../../docs/architecture/single-task-station.md#转换触发时机与处理顺序)：先读取新配置与草稿，缺项时 prepare-inputs 从备份提取；多候选仅询问无法判断的来源，使用返回路径加 --backup。preparation-candidate.json 只是旧配方线索，核验当前源码与工具链后另存 drafts/preparation.json，不直接运行旧命令。组件和 TapTest 等原生输入由 Agent 按项目映射转换，不猜测归属或新增解析器依赖。

1. 将配置名（建议 local）、目标架构、产品模式、节点组件及特殊端口/资源要求集中呈现为输入项，填入已知值和默认值，只索取缺项或更正。架构必须来自明确选择，不从宿主机推定；本地模式默认提出单节点 TM+FE，APIServer 仅显式选择时加入；外部模式沿上一节生成 FE-only 配置。不能把互不依赖的字段拆成多轮询问。
2. 配置名已知即运行 `prepare-inputs --env <name>`，在向用户展示路径前创建草稿和秘密文件；名称已有明确值时，可将普通输入项与已创建文件的绝对路径在同轮给出。用户本地填写 Mongo URI 和一份共享 license.txt，所有节点复用该许可证。普通输入可自然语言回复，由 Agent 更新草稿；不让用户在聊天中提交秘密。产品模式无需许可证时，统一清空各节点 license 引用。重复调用保留已填文件；已有正式配置直接复用。
3. 用户填写完成后运行 `check-inputs --config <草稿绝对路径>` 检查规范；失败时只补充或纠正具体问题，保留已有答案和秘密文件，然后重验。静态检查通过不代表连接、认证或许可证有效。只有真实依赖才追加交互，例如名称未定无法创建最终路径、产品模式决定许可要求，或后续预检发现数据库已有集合。
4. 规范检查通过后，Agent 按[项目开发指引](../../runbooks/tapdata-development.md#docker-开发环境的构建准备)完成工具、依赖、源码构建和完整装配，生成内部 preparation，再 `configure` 和 `deploy`。普通准备属于已授权部署范围；只有真实缺少凭证、权限或人工决定才询问缺项，不把内部字段交给用户填写。

本地 TM 模式部署前按 Runbook 只读检查 Mongo 目标库已有集合。发现旧表时展示库名/集合数及保留影响，询问用户保留还是清理；只有用户明确保留才使用 --reuse-mongo。用户选择清理时先提供绑定具体目标库的命令供其审核、复制执行；不能主动运行清理。只有用户明确输入对应命令并要求 Agent 代执行时才按该精确范围执行，回读后再部署。命令不能包含明文 URI/凭据，不删除整个 Mongo 实例或其它数据库。

Mongo URI 和许可证保存在命名环境的受控秘密文件中；生命周期脚本不创建、删除或重置外部 MongoDB。保存配置不等于完成部署，继续至真实验收。新用户首次登录需要其自行输入有效账号；没有登录证据时明确 UI 待验证，不以容器健康代替。

## 生命周期与验收

```sh
python3 <product-root>/projects/tapdata/scripts/test_environment.py list --station <station>
python3 <product-root>/projects/tapdata/scripts/test_environment.py prepare-inputs --station <station> --env <name>
python3 <product-root>/projects/tapdata/scripts/test_environment.py check-inputs --station <station> --config <草稿绝对路径>
python3 <product-root>/projects/tapdata/scripts/test_environment.py configure --station <station> --env <name> --config <运行配置文件> --preparation <Agent准备文件>
python3 <product-root>/projects/tapdata/scripts/test_environment.py inspect-mongo --station <station> --env <name>
python3 <product-root>/projects/tapdata/scripts/test_environment.py deploy --station <station> --env <name>
python3 <product-root>/projects/tapdata/scripts/test_environment.py update --station <station>
python3 <product-root>/projects/tapdata/scripts/test_environment.py status --station <station>
python3 <product-root>/projects/tapdata/scripts/test_environment.py uninstall --station <station>
```

首次部署完成准备后用 deploy；当前环境加载新源码制品或应用配置变化用 update，同名 deploy 只回读已有环境。status 检查当前现场；uninstall 清理整组。所有节点复用 `ao-tapdata-<station_id>` 的一个 Compose 项目和一份 compose.json，更新、切换、卸载保持同一组，不拆成多个 docker run 或独立 Compose 项目。

用户明确要求换环境时使用 `deploy --env <name> --switch`。脚本先构建预检再停止旧环境；不同时运行两份。切换生成新节点身份，update 按同名节点保留身份，减少节点时清理退役节点现场。更新和切换可能影响任务验证，沿用当前授权，范围或风险变化时补齐缺失决定，不逐命令重复询问。

容器就绪、所选组件、Launcher 注册及持续心跳分别核验；状态未就绪或外部结果未知时回读原现场，不反复部署、不手改记录、不缩减验收。部署就绪后给出各 TM 的本机 Web 访问地址、Compose 项目名、配置名称、源码/制品依据、实际节点组件、健康与监控结果，区分模拟验证和真实应用验收，不展示 URI、原始错误、展开凭据或敏感日志。

卸载仅清理当前环境受管容器、网络与运行现场，保留所有配置、源码、外部 MongoDB。私有诊断留在 runtime 的独立诊断目录；任务归档按已有流程保全必要材料。任务释放与工位清理由统一脚本读取项目清单，按本工位 Compose 项目和身份标签回收容器、网络，再处理运行目录。仅残留或异常时由 Agent 用本 Skill/原生工具接手；不要求正常退出前另行调用 Skill。
