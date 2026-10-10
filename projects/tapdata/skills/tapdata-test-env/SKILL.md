---
name: tapdata-test-env
description: 配置、部署、更新、检查、切换或卸载 TapData 工位 Docker 开发环境；复用多份命名配置，每工位一个活动环境，按节点选择 TM、FE、APIServer，使用宿主机构建的完整 Launcher 和指定外部 MongoDB。不管理生产环境、数据库生命周期或源码分支。
metadata:
  product: agenticops
---

# TapData 开发环境

先读取工位绑定和[开发环境指引](../../runbooks/test-environment.md)。当前工位 config 保存多份配置，runtime 只保留一个活动现场；各配置共享当前工位源码，不把配置名称解释为分支或独立源码。使用工位绑定的 Product Root 中的项目脚本，不复制中央脚本到工位，不修改 `.agenticops/` 状态。

## 首次部署：配置名 → 架构 → 模板 → 补齐 → 自动部署

先回读 `list` 和工位已有配置，区分首次部署、新增命名配置与更新活动环境。已有事实和有效授权直接复用；用户只需要说明运行架构与配置，不要求填写编译命令、制品路径或项目准备文件。

1. 取得环境配置名。未命名时建议 `local`；其它用途使用稳定的小写 kebab-case 名称。不要把架构、节点数量、随机 ID 或工位名自动拼进配置名。同一用途跨工位沿用相同名称；用户已有明确名称继续保留。
2. 确认目标架构 `linux/amd64` 或 `linux/arm64`，不从宿主机架构推定。复用已确认值；首次未知时说明两项选择，不重复询问。
3. 输出[运行配置模板](../../templates/station/test-env.json)，按[命名与输入合同](../../runbooks/test-environment.md#首次部署输入与配置命名)填入配置名、架构及已知默认值。展示可编辑的运行配置和文件位置，不展示构建、Maven、Jar 或内部 preparation 内容。用户可直接用自然语言补充，由 Agent 写入模板。
4. 集中引导补齐缺项：节点组件、外部 Mongo 连接文件、产品模式、许可证文件及特殊端口/资源要求。默认先提出单节点 TM+FE；用户选 APIServer 才加入。所有节点至少一个 TM，Mongo 必须从容器可达。企业版本地 DAAS 在构建前取得许可证路径；只报告文件是否可用，不回显内容。产品模式作为本轮部署意图记录，不让用户修改 Maven Profile。已有数据库模式冲突时说明影响并等待具体决定，不能自动改库。
5. 运行配置补齐后，Agent 按[项目开发指引](../../runbooks/tapdata-development.md#docker-开发环境的构建准备)完成工具、依赖、源码构建和完整装配，生成内部 preparation，再 `configure` 和 `deploy`。这部分属于已授权部署的准备工作；普通构建异常自行排查，只有真实缺少凭证、权限或人工决定才询问缺项，不把内部字段交给用户填写。

部署前按 Runbook 只读检查 Mongo 目标库已有集合。发现旧表时展示库名/集合数及保留影响，询问用户保留还是清理；只有用户明确保留才使用 --reuse-mongo。用户选择清理时先提供绑定具体目标库的命令供其审核、复制执行；不能主动运行清理。只有用户明确输入对应命令并要求 Agent 代执行时才按该精确范围执行，回读后再部署。命令不能包含明文 URI/凭据，不删除整个 Mongo 实例或其它数据库。

Mongo URI 和许可证保存在命名环境的受控秘密文件中；生命周期脚本不创建、删除或重置外部 MongoDB。保存配置不等于完成部署，继续至真实验收。新用户首次登录需要其自行输入有效账号；没有登录证据时明确 UI 待验证，不以容器健康代替。

## 生命周期与验收

```sh
python3 <product-root>/projects/tapdata/scripts/test_environment.py list --station <station>
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

卸载仅清理当前环境受管容器、网络与运行现场，保留所有配置、源码、外部 MongoDB。私有诊断留在 runtime 的独立诊断目录；任务归档按已有流程保全必要材料。任务释放/工位清理前先用本 Skill 卸载容器，不能只删除 runtime 目录而遗留 Docker 资源；不新增公共清理器或授权入口。
