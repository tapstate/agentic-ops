# Tapdata 构建、测试与本地运行

> 工作面：`developer`

本文用于帮助 AIAgent 在 Tapdata 任务中选择构建、测试、本地启动和日志检查方式。命令是参数化示例，执行前必须用目标分支配置校验。

来源：《TapData 产品研发指南（v3.5.4+）》，提取日期为 2026-07-27。

## 执行前确认

开始前必须确认：

- Jira 卡片、目标仓库、问题分支和修复分支。
- 目标分支声明的 JDK、Maven profile、包管理器和构建脚本。
- 变更涉及核心工程、公共库、企业版、连接器还是前端。
- 需要运行的最小测试和跨模块验证范围。

目标分支的 `pom.xml`、`package.json`、构建脚本和 CI 是事实源。手册示例与目标分支不一致时，不得猜测或强行套用。

## 后端工具链

Tapdata 4.x 核心工程当前基线为 JDK 17。执行前记录：

```sh
java -version
mvn -version
```

公共库可能同时声明多个 JDK profile，必须根据目标分支和实际消费工程选择，不得因为核心工程使用 JDK 17 就批量修改公共库兼容配置。

Maven profile 必须从目标分支的 POM、构建脚本或 CI 获取。企业版、DAAS、OSS 等构建不能只依据历史文档中的 profile 名称。

## 构建顺序

多仓任务按依赖方向执行：

```text
tapdata-common-lib
-> tapdata
-> tapdata-enterprise 或其它消费仓库

tapdata-common-lib
-> tapdata-connectors 的公共模块
-> 具体 connector
```

常用命令：

```sh
# 公共库安装到本地 Maven 仓库
mvn clean install -T1C -U

# 核心工程测试
mvn clean test -T1C <target-branch-profiles>

# 指定连接器及其依赖模块
mvn clean package -T1C -pl <connector-module> -am
```

`-pl` 指定模块，`-am` 同时构建该模块依赖的模块。只有确有反向消费验证需要时才使用 `-amd`。

`-DskipTests` 可以用于临时打包或定位编译问题，但不能作为验证结果。使用该参数后，必须单独执行与变更范围匹配的测试。

## 单元测试与质量检查

先运行受影响模块的最小测试，再根据依赖影响扩大范围：

```sh
mvn -pl <module> -Dtest=<TestClass> test
mvn -pl <module> -am test
```

Sonar 等质量检查必须通过环境变量读取服务地址和凭据：

```sh
mvn sonar:sonar \
  -Dsonar.projectKey=<project-key> \
  -Dsonar.host.url="$SONAR_HOST" \
  -Dsonar.login="$SONAR_TOKEN" \
  -Dsonar.branch.name="$(git branch --show-current)"
```

不得把服务地址、token、密码或研发工程师本机路径写入项目资产、提交信息或运行证据。

## 前端构建与运行

前端必须使用目标分支 `package.json` 声明的包管理器和脚本。当前仓库使用 pnpm 时，可按仓库脚本执行：

```sh
pnpm install
pnpm dev:daas
```

如果任务面向 OSS、Cloud 或其它应用，使用目标分支已经声明的对应脚本，不得自行发明命令。

只有目标分支脚本明确要求时才使用 `--openssl-legacy-provider`；不得把旧 Node.js workaround 设为所有分支的默认配置。

## FE 与 TM 本地运行

建议在项目工作空间中为 FE 和 TM 使用独立工作目录：

```text
<workspace>/workdir/flow-agent
<workspace>/workdir/tm
```

FE 当前入口类：

```text
io.tapdata.Application
```

FE 和 TM 的本地运行均必须包含：

```text
app_type=DAAS
```

通过 TapData 启动器启动时，启动器会默认注入该环境变量；手动或自定义启动方式必须显式设置。

常见配置名：

```text
app_type=DAAS
TAPDATA_MONGO_URI=<mongo-uri>
TAPDATA_WORK_DIR=.
backend_url=<tm-api-url>
```

以下配置仅为结构和值的参考样例，不代表当前用户、工作空间或任务的真实运行配置。真实启动前，AIAgent 必须向用户展示拟使用的 FE 与 TM 配置，并要求用户修改或逐项确认；未完成确认时不得启动。FE 与 TM 必须连接同一个已确认的 MongoDB 环境，FE 的 `backend_url` 必须指向本次使用的 TM API。

FE 参考配置：

```text
app_type=DAAS
backend_url=http://localhost:3000/api/
TAPDATA_MONGO_URI=mongodb://mongo/tapdata
TAPDATA_WORK_DIR=.
```

TM 当前入口类：

```text
com.tapdata.tm.TMApplication
```

常见配置名：

```text
app_type=DAAS
TAPDATA_MONGO_URI=<mongo-uri>
tapdata_websocket_port=<unique-port>
```

TM 参考配置：

```text
app_type=DAAS
spring.data.mongodb.default.uri=mongodb://mongo/tapdata
spring.data.mongodb.log.uri=mongodb://mongo/tapdata
spring.data.mongodb.obs.uri=mongodb://mongo/tapdata
```

用户确认时至少需要核对 MongoDB URI、FE `backend_url`、FE 与 TM 工作目录，以及 TM 的 HTTP 和 WebSocket 端口。目标分支如果改用 Spring 配置项，应按目标分支配置执行。相同主机运行多个 TM 时，必须为每个实例分配不同的 HTTP 和 WebSocket 端口。

常见日志位置：

```text
<workdir>/logs/agent/tapdata-agent.log
<workdir>/logs/manager/tm-<hostname>.log
```

路径与目标分支实际启动脚本不一致时，以启动脚本和日志配置为准。

## 验证记录

任务证据必须包含：

- 实际 JDK、Maven、Node.js 和包管理器版本。
- 使用的 Maven profile 或前端脚本。
- 构建和测试命令及退出结果。
- 已验证仓库、模块和场景。
- 未验证范围、阻塞原因和风险。
- 需要人工执行的发布、部署或环境操作。
