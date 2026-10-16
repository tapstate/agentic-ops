# TapData 工位 Docker 开发环境

本环境用于研发本地测试和联调。现有 `tapdata-test-env` Skill、脚本、配置及运行目录标识继续兼容，用户界面与指引统一称为“开发环境”。

本指引负责命名环境配置、完整 Launcher 装配和本地 Docker 生命周期，由 [tapdata-test-env](../skills/tapdata-test-env/SKILL.md) 使用。源码构建方法仍以[构建测试指引](build-test-and-local-run.md)和当前工位分支为准；工位身份与任务退出以[工位合同](../../../docs/architecture/single-task-station.md)为准。本能力不修改 `.agenticops/` 或引入环境池；配置和运行目录都是可选增量，兼容 epoch 26 的原有工位。

## 首次部署输入与配置命名

首次部署和新增命名配置按“复用已知信息 → 集中收集独立输入并填写秘密文件 → 配置规范检查 → 构建与部署预检 → 自动部署”进入。配置名、目标架构、产品模式、节点与特殊端口/资源要求没有先后依赖，应同轮展示默认值与缺项，只让用户补充或更正；仅当路径依赖尚未确定的配置名、产品模式决定是否需要许可证，或数据库预检发现已有集合时，追加相应交互。用户只需要运行意图，构建准备由 [项目开发指引](tapdata-development.md#docker-开发环境的构建准备)负责；已有配置或事实直接复用。

| 输入 | Agent 的引导与默认值 |
|---|---|
| 配置名 | 未命名时建议 `local`；用稳定用途命名，允许用户明确命名，不随架构或工位变化改名 |
| 架构 | 明确选择 `linux/amd64` 或 `linux/arm64`，不从宿主机推导 |
| 产品模式 | 核实部署意图，例如本地企业版 DAAS；内部 Profile 由项目指引推导 |
| 节点组件 | 默认提出 node1 的 TM+FE，1～3 个节点，至少一个 TM；APIServer 仅显式选中时部署 |
| MongoDB | 先创建 Mongo URI 秘密文件并给出绝对路径，由用户本地填写；已有受控文件直接复用，核对容器可达、认证、副本集和数据库用途 |
| 许可证 | 每个环境仅一份 license.txt，所有节点共享；先创建权限 0600 的文件并给出绝对路径，由用户填写；存在或非空不代表授权有效，启动后验证 |
| 端口与资源 | 默认 node1 TM 13030/API 13080，node2 23030/23080，node3 33030/33080；未选组件为 null，冲突时选择空闲端口并告知；默认 Java 堆见模板 |

通过 `prepare-inputs --env <name>` 从 [运行配置模板](../templates/station/test-env.json) 生成用户可编辑草稿及秘密文件，不依赖 Docker、构建或 preparation。返回输入项、已有默认值、缺项与绝对文件路径；产品模式从会话复用，脚本不新增模式台账。Agent 填入名称、架构和已知值，只让用户补缺项，可用自然语言反馈而不要求手工编辑 JSON。产品模式与登录验收目标在会话/Jira 记录，不另设执行清单。每工位可以保存多份配置，但只有一个活动环境；新增配置不会启动第二套环境，也不代表切换源码分支。

新配置采用以下统一布局；`<name>` 是用户配置名，不是 station_id：

| 对象 | 统一位置或名称 |
|---|---|
| 用户运行配置 | `config/tapdata-test-env/<name>.json`，schema_version 2，字段见模板 |
| 输入草稿 | `config/tapdata-test-env/drafts/<name>.json`；沿用 schema 2，未完成时不列为已保存环境，不作为部署输入自动加载 |
| Agent 项目准备 | `config/tapdata-test-env/preparation/<name>.json`，schema_version 1，用户不填写 |
| Mongo 秘密文件 | `config/secrets/tapdata-test-env/<name>/mongodb-uri`；mongo_uri_file 填 `tapdata-test-env/<name>/mongodb-uri` |
| 许可证秘密文件 | `config/secrets/tapdata-test-env/<name>/license.txt`；ports 中 license 填 `secrets/tapdata-test-env/<name>/license.txt` |
| 节点 | 新配置统一 `node1`、`node2`、`node3`；nodes 与 ports 的键一致 |
| 单一运行现场 | `runtime/tapdata-test-env/`，全部节点使用 `ao-tapdata-<station_id>` Compose 项目 |

新配置所有节点的 license 引用均指向同一份 `license.txt`；确认产品模式无需许可证时统一为空字符串，不要求用户逐节点提供。旧配置继续原样读取，不自动重写既有许可证引用。运行配置不含 build/assets、制品路径或监控实现字段；schema 1 的旧完整配置继续原样读取，旧名称/秘密路径/节点身份保留，不自动迁移。新配置和项目准备通过 configure 保存，不改 `.agenticops/`，不提升工位 epoch。只修改已有运行配置时可复用对应 preparation；切换源码或架构需 Agent 重新核验它，不要求用户同步技术字段。

TM、FE、APIServer 分别映射 Launcher 的 `start frontend`、`start backend`、`start apiserver`，每次附带独立 `--workDir`。无 TM 节点等待 TM 容器健康；节点用 Compose DNS 地址通信，内部 TM 3030/API 3080，宿主映射只绑定 127.0.0.1。每个节点角色、端口及资源必须与运行配置一致。

## 配置准备与预检

`prepare-inputs` 只初始化不存在的草稿与秘密文件，秘密文件权限为 0600；重复调用保留已填内容，已有正式同名配置拒绝初始化。用户可修改草稿或用自然语言反馈普通输入，由 Agent 写入；秘密只在本地文件填写，不进入聊天、命令参数或日志。草稿中断后继续复用；不增加状态字段或工位 epoch。

用户填写完成后必须运行 `check-inputs --config <草稿绝对路径>`：复用运行配置校验，检查字段、架构、1～3 个节点、至少一个 TM、组件与端口对应、端口范围和重复、Java 参数结构、单份许可证引用、秘密文件受控路径/普通文件/权限/非空，以及 Mongo URI 基本格式、目标数据库和容器地址。失败只纠正返回的缺项或错误，再检查；不覆盖用户秘密文件。产品模式与是否需要许可证由 Agent 对照实际源码核实；不要求用户填写内部 Profile。通过仅表示静态配置合规，Mongo 认证、网络、副本集、宿主端口实际占用和许可证有效性仍由后续预检与启动验证，不能称为环境可用。

用户配置的 schema_version 为 2；节点、镜像、平台、Mongo 秘密路径、ports 和 java 是运行输入。Agent 依据[项目开发指引](tapdata-development.md#docker-开发环境的构建准备)生成独立 preparation，生命周期脚本合并后复用已有执行合同；旧 schema 1 继续兼容。`configure --env <name> --config <已检查的草稿.json> --preparation <preparation.json>` 先验证两份输入，普通配置错误不覆盖已保存配置或停止当前环境。只改运行配置时可省略 --preparation，复用已保存准备文件。

部署前核对 Docker CLI、Compose 与 daemon、秘密文件权限及端口。Mongo URI 的 localhost/127.0.0.1 不能代表宿主机，使用用户提供的容器可达地址；不猜测替换 URI。Mongo 的权限、TLS 与副本集必须由目标应用支持，不自动创建外部数据库或生成 TLS 材料。源码与目标 Linux 制品、模块加载、Java/Node 执行和副本成员可达性由项目准备及只读容器预检验证；准备失败继续不依赖缺项的工作并保留原环境。

部署前只读检查目标库的已有集合（Mongo 的表），输出明确库名与集合数，不读取业务记录。`inspect-mongo --env <name>` 复用当前 Linux 制品；无活动现场时由 Agent 在项目准备完成后指定受管目录 `--bundle builds/<build-id>/bundle`。首次部署或切换非空库未取得保留决定时，deploy 在停止原环境前拒绝继续；用户明确保留后才传 --reuse-mongo。正常 update 保留原库，不把应用更新当作清库授权。预检因权限无法列集合时待核验，不把查询失败当成空库。

发现旧表时先询问保留还是清理，说明历史模式、权限、节点等记录可能影响验证。清理只能由用户审核后复制命令执行，或由用户明确输入对应命令并要求 Agent 代执行；选择“需要清理”本身不授权 Agent 主动执行。Agent 提供的命令必须绑定已回读的具体目标库，并在命令内核对实际数据库名称，用秘密文件引用读取连接，不能展开 URI 或密码、清理整个 Mongo 服务或其它数据库。用户执行后再只读回查，不以其说“执行过”代替空库事实；完成清理后无需 --reuse-mongo。许可证可能绑定现有 sid/机器信息，清库会删除数据库授权与身份记录，须在命令说明中指出并确认现有许可证能用于重新初始化。

启动后核验实际产品模式、Settings、版本、许可证、登录及菜单。已有数据库模式与部署意图不同，先说明影响并取得决定，备份受影响设置；不删除迁移记录、重置权限或关闭许可证检查。容器健康不代表业务验收完成。

## 操作、恢复与卸载

统一入口为 Product Root 的 `projects/tapdata/scripts/test_environment.py`，参数 `--station <station>`。`configure --env <name> --config <json>` 保存运行配置与 Agent 准备但不应用；`list` 回读配置清单和 Docker 实际状态；`deploy` 多配置时需 `--env`，单配置可省略；`update/status/uninstall` 默认针对活动环境。配置或秘密文件变化需 update，不热加载。源码修改后要加载新制品也使用 update；对同名活动环境重复 deploy 只回读现有环境，不重建源码。

不同配置的部署需用户明确切换意图，对应 `deploy --env <name> --switch`。目标构建、架构、Mongo和许可证文件预检成功后才停止旧环境。更新保留同名节点身份；切换清理旧节点现场并生成新身份，两份配置和外部数据库保留。上线失败保留现场和新旧构建记录，不自动回退数据库；用 status 回读并修复后 update，或明确卸载。中断或 Docker 写入结果不明时先回读，归属不明或未登记资源停止自动删除。

所有节点属于同一个 Compose 项目 `ao-tapdata-<station_id>`，定义保存在 `runtime/tapdata-test-env/compose.json`；节点是其中的 services，不按节点分别执行 docker run 或生成独立项目。脚本统一传 `docker compose -p <project> -f <compose-file>`，更新和切换沿用同一项目，工位之间以 station_id 隔离。Docker Desktop 中按该项目查看整组。可用同一组参数执行只读 `ps --all` 核验分组；部署和删除优先使用生命周期入口，避免绕过归属、构建及诊断检查。

卸载先保留私有诊断，再回读确认受管容器和网络已删除，最后清理当前运行目录。不执行 Docker prune、不删除镜像、不删除外部数据库；诊断留在 `runtime/tapdata-test-env-diagnostics/`，配置和源码保留。任务释放前先卸载 Docker 资源，诊断按现有归档规则保全，不能仅删除绑定目录。

## 验收

自动测试核对集中输入准备、单份共享许可证、填写后规范检查、重复初始化保全、空文件和路径/权限拒绝、多配置选择、单环境约束、组件角色与端口生成、失败预检保留原环境、更新身份保留、切换身份更新、资源归属、脱敏和卸载保留配置/外部数据。安装回归确认新增 Skill 按项目机制接线，旧工位初始化、刷新、清理继续可用。

真实验收必须使用实际工位源码、完整 Linux 产物及授权 MongoDB：验证单节点、三节点（含 TM-only/FE-only）、Launcher 注册和持续心跳、停止节点后的离线、更新加载新制品、两份配置顺序切换和卸载。构建成功、模拟测试或端口存活都不能宣称完整应用验收通过；缺少真实环境时列为待核验。
