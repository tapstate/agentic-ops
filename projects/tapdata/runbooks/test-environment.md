# TapData 工位 Docker 测试环境

本指引负责命名环境配置、完整 Launcher 装配和本地 Docker 生命周期，由 [tapdata-test-env](../skills/tapdata-test-env/SKILL.md) 使用。源码构建方法仍以[构建测试指引](build-test-and-local-run.md)和当前工位分支为准；工位身份与任务退出以[工位合同](../../../docs/architecture/single-task-station.md)为准。本能力不修改 `.agenticops/` 或引入环境池；配置和运行目录都是可选增量，兼容 epoch 26 的原有工位。

## 多配置与组件分布

配置保存于 `config/tapdata-test-env/<name>.json`，名称仅使用小写字母、数字和连字符。工位可保存多份，但唯一活动环境位于 `runtime/tapdata-test-env/active.json`，内容只用于该应用生命周期，不是任务事实源。配置名称不切换源码分支。项目脚本通过现有项目 Skill 分发机制使用，普通 station repair 可刷新接线，无需另改 Adapter。

从 [配置模板](../templates/station/test-env.json) 开始；模板为单节点全组件，platform 留空，必须先明确实例目标架构才能保存有效配置。修改成三个不同角色节点的例子：

```json
{
  "nodes": {
    "node1": ["TM", "FE", "APIServer"],
    "node2": ["TM"],
    "node3": ["FE"]
  },
  "ports": {
    "node1": {"tm": 13030, "api": 13080, "license": "licenses/node1.txt"},
    "node2": {"tm": 23030, "api": null, "license": "licenses/node2.txt"},
    "node3": {"tm": null, "api": null, "license": "licenses/node3.txt"}
  }
}
```

上述仅为替换模板字段的片段。ports 与 nodes 的键必须相同；未部署 TM/API 的端口必须是 null，FE 不额外映射端口。许可证路径相对工位 config，空字符串表示不挂载。全环境至少一个 TM，支持 1～3 个非空节点，组件不能重复。所有节点使用同一元数据库。

TM、FE、APIServer 分别映射 Launcher 的 `start frontend`、`start backend`、`start apiserver`，每次附带独立 `--workDir`。无 TM 节点等待 TM 容器健康后启动。backendUrl 包含同环境所有 TM 的 Compose DNS 地址，当前节点有 TM 时本节点使用回环地址；不使用宿主机映射端口做容器间通信。目标 Launcher 必须支持多地址 backendUrl 和上述组件命令；组件健康仅要求配置中的角色，同时检查未选组件没有意外运行。

## 配置接口与宿主机构建

架构参考 dbforge：其 TapData 声明默认镜像为 eclipse-temurin:17-jre-jammy，可由 DBFORGE_DB_TAPDATA_IMAGE 覆盖，节点由 DBFORGE_DB_TAPDATA_NODE_SELECTOR 约束；请求示例 TAPDATA_ARCH=amd64 仅为元数据，不能代替镜像和发行包架构。Compose 的 platform 明确选择 linux/amd64 或 linux/arm64，TAPDATA_ARCH 随之注入。Docker 引擎报告 aarch64 仅证明宿主架构；amd64 在该宿主上需模拟执行，是否可用以候选 Docker 预检为准。首次配置必须让研发确认目标，之后复用配置；模板不自动采用宿主架构。

| 字段 | 含义与边界 |
|---|---|
| schema_version | 当前为 1 |
| nodes / ports | 节点角色和宿主机端口，仅绑定 127.0.0.1 |
| image / platform | 含 Java 17、bash、ps、tar 的运行镜像；Linux amd64 或 arm64 |
| mongo_uri_file | 相对 config/secrets 的 URI 文件，权限 0600；不在命令参数或环境变量传递 URI |
| mongo_driver | 相对 source 的 Launcher 原有 node_modules 目录，需已有 mongodb 及其依赖，不安装新组件 |
| node_binary | 装配包中可执行 Linux Node 的相对路径 |
| java | TM/FE 的 JVM 参数 |
| build | 按顺序执行的 cwd/argv 数组；cwd 相对 source，沿用当前分支构建入口 |
| assets | source/target 数组；source 相对工位 source，target 相对装配包，`.` 表示复制完整目录 |
| heartbeat | MongoDB 中监控集合、UUID 字段、心跳时间字段、最大心跳间隔及启动等待时间 |

build/assets 不设假定分支的默认命令：Skill 必须读取当前源码脚本与 profile 后填写。所有构建命令使用 argv，不进行 shell 二次解析；确需项目 shell 构建入口时显式调用 bash 和脚本路径。构建失败保留私有日志，部署前后记录所有工位 Git 仓库 HEAD、分支、未提交修改及未跟踪文件摘要；源码变化不继续部署。日志不自动输出或提交。

装配可以由多项 assets 从本轮构建目录复制，也可将本轮源码构建出的完整发行目录映射到 `.`。必须包含 `tapdata`、`tapdata-agent`、Linux Node、`components/tm.jar`、`components/tapdata-agent.jar`、WebUI 与 Connector；仅当任一节点选择 APIServer 时，才要求 API Server（已解包目录或发行包压缩文件）。脚本不自动下载完整应用，不用另一套 Docker start.sh 代替 Launcher。与原版 dbforge 不同，更新只替换应用目录，WORK_DIR 中的身份和日志独立保存，不随重新解包丢失。容器使用宿主机当前 UID/GID，HOME 和 Java user.home 指向节点内可写目录，避免 Linux 上产生宿主用户无法保全或清理的 root 私有文件。

首次部署先在临时 Docker 容器中执行目标 Node、Java 和只读 Mongo 探测。副本集 hello 公布的成员地址也逐一检查可达性；本地 Mongo 使用 `host.docker.internal` 或其他容器可达地址，Linux 增加 host-gateway。URI 若使用 localhost/127.0.0.1 将被拒绝，不猜测替换 URI。MongoDB 必须已经具备该应用所需配置及权限；TLS等额外配置本期不自动生成，应使用目标版本可用的 URI 配置或明确报告缺项。

默认监控读取当前 TM 的 `ClusterState`，以 uuid 匹配节点、`systemInfo.time` 判断心跳。不同目标分支需从实际源码核验字段后调整 heartbeat，不以探测脚本成功代替产品监控事实。startup_timeout_seconds 默认 300、max_age_seconds 默认 60；不能通过扩大阈值掩盖心跳停止。

## 从源码补齐部署准备

缺少 target、dist、node_modules 或配置中的 build/assets，是首次部署的准备工作，不能据此认定分支没有部署能力。先用工位 repository context 定位实际仓库，核对当前分支的构建脚本、package.json、锁文件和打包函数；路径来自实际源码，不假定存在 `source/tapdata/application`。只有所需源码、原生构建能力、凭证或目标平台资源确实无法取得时，才报告具体阻塞。

1. 先回读 `list`，核对 Docker CLI、`docker compose version` 和 `docker info`。`list` 返回状态无法核验时检查工具、daemon 和权限；退出码 0 不表示环境为空。复用已选配置、架构、节点和秘密文件；用户去除 APIServer 时同步删除 nodes 中的角色并把对应 api 端口设为 null。
2. 按[开发指引](tapdata-development.md#3-构建并装配)准备宿主构建工具与目标分支已有依赖。有锁文件时使用该分支支持的锁定安装命令，例如 npm ci 或 pnpm 的 frozen-lockfile 模式，安装在工位独立源码内；不升级依赖、不改锁文件、不全局安装 pkg。Launcher 已声明 pkg 时优先使用其本地可执行文件。私有仓凭证、下载权限或额外第三方组件缺少时只暂停依赖步骤，继续准备不依赖它的配置和构建项。
3. 核验宿主构建与容器执行的区别：macOS 的 Node 可运行包管理器，装配包必须包含目标 Linux Node 和 Launcher；不要在 macOS 执行打包脚本自动选出的 Linux Node。若项目完整构建入口混用了 Linux 专用工具，复用分支中各组件的构建入口与打包逻辑装配，不能改成直接启动 Jar。不要盲目运行带 git pull、镜像推送、远端部署或全局依赖安装的整包脚本。
4. 按依赖顺序填入 build 的 cwd/argv，覆盖所需公共库、TM、FE、WebUI、Connector 和 Launcher；仅选择 APIServer 时准备 API 构建。当前任务 Maven 路径按[构建测试指引](build-test-and-local-run.md#Maven-配置与任务本地仓库)回读；无活动任务时在当前环境 runtime 下使用独立本地仓库，保留用户 settings。构建入口所有 Maven 子调用都要实际消费该路径，不能只给顶层命令一个未传递的参数。
5. 对照本分支打包函数填写 assets，复制本轮输出到脚本要求的包内路径。常见来源是 application 仓的 output、manager/tm 的 exec Jar、iengine 的 ie.jar、Web 仓的 dist 和 Launcher 仓的 dist；这些只是定位线索，必须核验当前分支。Node 常见路径为 `lib/NDK/node/bin/node`，模板的 `lib/NDK/node/node` 也需按实际包修正。核对 Launcher 的两个入口、配置资源、Java 运行选择和工作目录协议；不能用空目录、占位脚本或旧发行包补足检查。
6. 通过 configure 保存完整配置，再由 deploy/update 执行构建、装配和 Docker 预检。脚本在构建之后检查 mongo_driver，因此 build 可以先准备目标 Launcher 已声明的 mongodb 依赖。配置保存成功且 build_configured 为 false 只说明草稿已保存，仍须完成构建配置。产物尚未生成时不要先调用 deploy 获取已知错误，然后停止。

失败时指出失败的具体准备或启动步骤、可回查的私有日志位置、旧环境是否仍运行以及剩余输入。普通构建失败按日志修正后继续原操作；源码变动、权限缺失或写入结果未知时先核实，不能盲目重试或把辅助脚本错误当作应用能力缺失。

## 操作、恢复与卸载

统一入口为 Product Root 的 `projects/tapdata/scripts/test_environment.py`，参数 `--station <station>`。`configure --env <name> --config <json>` 保存完整配置但不应用；`list` 回读配置清单和 Docker 实际状态；`deploy` 多配置时需 `--env`，单配置可省略；`update/status/uninstall` 默认针对活动环境。配置或秘密文件变化需 update，不热加载。源码修改后要加载新制品也使用 update；对同名活动环境重复 deploy 只回读现有环境，不重建源码。

不同配置的部署需用户明确切换意图，对应 `deploy --env <name> --switch`。目标构建、架构、Mongo和许可证预检成功后才停止旧环境。更新保留同名节点身份；切换清理旧节点现场并生成新身份，两份配置和外部数据库保留。上线失败保留现场和新旧构建记录，不自动回退数据库；用 status 回读并修复后 update，或明确卸载。中断或 Docker 写入结果不明时先回读，归属不明或未登记资源停止自动删除。

所有节点属于同一个 Compose 项目 `ao-tapdata-<station_id>`，定义保存在 `runtime/tapdata-test-env/compose.json`；节点是其中的 services，不按节点分别执行 docker run 或生成独立项目。脚本统一传 `docker compose -p <project> -f <compose-file>`，更新和切换沿用同一项目，工位之间以 station_id 隔离。Docker Desktop 中按该项目查看整组。可用同一组参数执行只读 `ps --all` 核验分组；部署和删除优先使用生命周期入口，避免绕过归属、构建及诊断检查。

卸载先保留私有诊断，再回读确认受管容器和网络已删除，最后清理当前运行目录。不执行 Docker prune、不删除镜像、不删除外部数据库；诊断留在 `runtime/tapdata-test-env-diagnostics/`，配置和源码保留。任务释放前先卸载 Docker 资源，诊断按现有归档规则保全，不能仅删除绑定目录。

## 验收

自动测试核对多配置选择、单环境约束、组件角色与端口生成、失败预检保留原环境、更新身份保留、切换身份更新、资源归属、脱敏和卸载保留配置/外部数据。安装回归确认新增 Skill 按项目机制接线，旧工位初始化、刷新、清理继续可用。

真实验收必须使用实际工位源码、完整 Linux 产物及授权 MongoDB：验证单节点、三节点（含 TM-only/FE-only）、Launcher 注册和持续心跳、停止节点后的离线、更新加载新制品、两份配置顺序切换和卸载。构建成功、模拟测试或端口存活都不能宣称完整应用验收通过；缺少真实环境时列为待核验。
