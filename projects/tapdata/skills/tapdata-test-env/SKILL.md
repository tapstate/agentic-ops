---
name: tapdata-test-env
description: 配置、部署、更新、检查、切换或卸载 TapData 工位 Docker 测试环境；复用多份命名配置，每工位一个活动环境，按节点选择 TM、FE、APIServer，使用宿主机构建的完整 Launcher 和指定外部 MongoDB。不管理生产环境、数据库生命周期或源码分支。
metadata:
  product: agenticops
---

# TapData 测试环境

先读取工位绑定和[测试环境指引](../../runbooks/test-environment.md)。当前工位 config 保存多份配置，runtime 只保留一个活动现场；各配置共享当前工位源码，不把配置名称解释为分支或独立源码。使用工位绑定的 Product Root 中的项目脚本，不复制中央脚本到工位，不修改 `.agenticops/` 状态。

## 配置与构建

复用用户已选环境及操作范围。通过 `list` 查看配置和实际运行环境，多配置且未指定时请用户选择；只有一份配置可自动选择。`configure --env <name> --config <json>` 保存配置，不启动或更新环境。需要 Docker 测试环境时可独立调用，不要求存在 current run；有活动任务时沿用其授权和范围。

构建遵循[构建测试指引](../../runbooks/build-test-and-local-run.md)，核验当前源码分支、有效 profile 和依赖后填写 argv 构建步骤及装配来源。模板的 platform/build/assets 未指定，先确认实例目标架构，再补齐构建与装配。dbforge 的 TAPDATA_ARCH 是元数据，实际运行取决于镜像与发行产物；不能从 Docker 引擎架构自动认定实例架构。用户已指定或已有已确认配置则复用，不重复询问；首次未知时让用户选择 linux/amd64 或 linux/arm64。不能以旧包或缓存 Jar 冒充本轮源码构建。核验包含目标 Linux Launcher、Node、TM、FE、API Server、WebUI 和 Connector，源码 Launcher 可经本分支构建入口装配，不临时发明启动器。若本分支不具备完整产物，报告具体缺项，保留当前环境，不降级为直接启动 Jar。

每节点 `nodes` 选择 TM/FE/APIServer，至少一个 TM。Launcher 原生组件名分别是 frontend/backend/apiserver；脚本按选择调用并连接当前环境 TM。目标分支若不支持这些命令、工作目录或监控字段，先调整已核验配置或报告能力缺口，不绕过 Launcher。Mongo URI 放在仅当前用户可读的秘密文件，必须从容器可达；不创建、删除或重置外部 MongoDB。

## 生命周期与验收

```sh
python3 <product-root>/projects/tapdata/scripts/test_environment.py list --station <station>
python3 <product-root>/projects/tapdata/scripts/test_environment.py deploy --station <station> --env <name>
python3 <product-root>/projects/tapdata/scripts/test_environment.py update --station <station>
python3 <product-root>/projects/tapdata/scripts/test_environment.py status --station <station>
python3 <product-root>/projects/tapdata/scripts/test_environment.py uninstall --station <station>
```

用户明确要求换环境时使用 `deploy --env <name> --switch`。脚本先构建预检再停止旧环境；不同时运行两份。切换生成新节点身份，update 按同名节点保留身份，减少节点时清理退役节点现场。更新和切换可能影响任务验证，沿用当前授权，范围或风险变化时补齐缺失决定，不逐命令重复询问。

容器就绪、所选组件、Launcher 注册及持续心跳分别核验；状态未就绪或外部结果未知时回读原现场，不反复部署、不手改记录、不缩减验收。输出配置名称、源码/制品依据、实际节点组件、健康与监控结果，区分模拟验证和真实应用验收，不展示 URI、原始错误、展开凭据或敏感日志。

卸载仅清理当前环境受管容器、网络与运行现场，保留所有配置、源码、外部 MongoDB。私有诊断留在 runtime 的独立诊断目录；任务归档按已有流程保全必要材料。任务释放/工位清理前先用本 Skill 卸载容器，不能只删除 runtime 目录而遗留 Docker 资源；不新增公共清理器或授权入口。
