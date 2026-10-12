# TapData 开发指引

按“定位源码 → 准备配置 → 构建 → 启动 → 验证加载与业务行为”推进；已有有效成果直接复用，只准备本任务需要的组件。Java 测试细节见[构建与测试](build-test-and-local-run.md)，仅在用户选择当前会话处理 TapTest 时读取 [TapTest 开发指引](taptest-development.md)。

## 1. 定位源码与修改范围

从当前任务 repository context 取得各仓 source 路径、工作分支、完整 SHA 和未提交差异。先核对需求是否已有实现、相近模块如何处理，再推荐最小修改范围：项目特有行为优先留在对应模块，确有共同语义才修改公共层。需求预期不清或与源码事实冲突时，说明依据、影响和推荐方案，不直接照做，也不把实现现状当作正确预期。

成功标志：能说明改哪个模块、谁消费它、如何验证；无需为局部修改重新准备整套工程。`full-application` 只表示源码已准备，不表示应用可运行。

## 2. 准备配置与工具链

先复用工位 config，再从目标分支核对以下输入；只集中询问缺失或冲突项。

| 输入 | 到哪里核对 |
|---|---|
| JDK、Maven | 父子 POM、构建脚本和实际版本；使用明确的可执行路径 |
| Node、包管理器 | tapdata-web 的 package.json、packageManager 和锁文件；仅需 API 时可不启动 Web |
| MongoDB | 实际 URI、认证库、副本集和权限，不从端口开放推断可用 |
| TM/Engine | HTTP/WebSocket 端口、后端地址、Access Code、应用模式和许可 |

配置字段与示例复用[本地工位配置输入](build-test-and-local-run.md#本地工位配置输入)及其 TM/FE 模板。持久输入放 `config`，各仓源码在 `source`，生效配置放 `runtime/effective-config`，TM/Engine 工作目录分别用 `runtime/app/tm`、`runtime/app/flow-agent`。秘密仅通过本地受控配置或应用子进程环境传入，不写入 Git、命令行或日志。

成功标志：工具版本符合目标分支，配置指向同一个已授权环境，端口无冲突。模板的 `tapdata.cloud.baseURLs` 与历史 `backend_url`、DAAS 与 OSS/Cloud 模式按实际源码选择，不叠加旧配置试错。缺少运行环境时仍可继续源码分析和用例开发。

## 3. 构建并装配

按[依赖方向与 Maven 命令](build-test-and-local-run.md#依赖构建命令)构建本次消费的公共库、核心、企业模块和连接器；Maven 使用当前工位隔离仓库，保留用户 settings。前端采用目标 package.json 中已有的脚本。

成功标志：得到本次源码构建的可用制品，并能指出实际消费位置。记录制品 SHA-256；同名 Jar 或构建成功不证明应用加载了新代码。临时跳过测试只用于打包，测试结果另行记录。

### Docker 开发环境的构建准备

这是 Agent 内部准备入口，部署用户只确认[运行输入](test-environment.md#首次部署输入与配置命名)。生命周期脚本不提供假定分支的编译命令；根据实际 repository context 和源码脚本生成 [preparation 模板](../templates/station/test-env-preparation.json)，保存在 `config/tapdata-test-env/preparation/<name>.json`，不让用户填写 build/assets、驱动或心跳字段。不把 td2、某个任务编号、绝对临时文件或一次成功的分支配方写成默认值。

1. 核对 Docker、JDK、Maven、Node 与分支支持的包管理器。私有仓凭证和工具缺失在昂贵构建前定位，普通准备在部署授权内完成；需要额外第三方 Python 组件时遵循仓库决策规则。缺少 target/dist/node_modules 是准备工作，不代表源码没有部署能力。先定位真实 source 路径，不假定 application 仓的目录拼写。
2. 通过现役 runtime-path 取得当前任务 Maven 仓库；无活动任务时用 `runtime/tapdata-test-env-maven/<name>`。将核验后的 Maven、JAVA_HOME、隔离仓库路径传给 [Maven 辅助脚本](../scripts/test-environment-maven.py)，每个子调用都使用该脚本或原生等价参数，不依赖顶层参数向下自动传递，不改用户 settings 或任务状态。
3. build 按公共库 → 核心 → 企业消费者和所选连接器准备 cwd/argv，企业版本地 DAAS 同时使用 `enterprise,idaas`，按[依赖构建命令](build-test-and-local-run.md#依赖构建命令)核对有效资源。WebUI 使用分支已有的 DAAS 构建脚本。仅选择 APIServer 时准备其构建与装配。锁定 npm/pnpm 依赖，使用分支本地 pkg；不改锁文件或全局安装。包管理器版本取 package.json/锁文件，不继承历史研发指引的旧 workaround。
4. 核对宿主构建与目标 Linux 执行。macOS Node 用于包管理，包中 Node、Launcher 必须为用户选择的 Linux 架构；pkg target 与 Linux Node 发行文件取当前分支支持的组合，不从 Docker 引擎架构推定。直接构建所需组件时保留整包的装配语义，不盲目执行含 git pull、镜像推送、远端发布的脚本，不改为直接启动 Jar。
5. assets 从本轮输出取得两个 Launcher 入口、TM exec Jar、FE/Engine Jar、WebUI、Connector、应用配置和日志资源、Linux Node。对照原生打包函数核验企业许可证模块注入、idaas TM 配置、OEM 和 `.version` 元数据；完整发行目录可映射到 `.`。当前 Launcher 消费 RC4 的 `.version` 时可用 [版本元数据辅助脚本](../scripts/test-environment-version.js)，传入 Launcher/核心源码/输出文件绝对路径，使用源码 git describe 与目标 Launcher 自有编码，不伪造发布版本。协议不同则使用分支原生入口。Node 常见包内路径 `lib/NDK/node/bin/node`，必须核对实际文件。
6. preparation 的 mongo_driver 指向 Launcher 已声明的 node_modules，构建可先准备依赖；探测将其挂载 /driver 并设置 NODE_PATH=/driver。heartbeat 从当前分支实际监控源码核对，常见为 ClusterState/uuid/systemInfo.time，默认最大间隔 60 秒、启动 300 秒，不扩大阈值掩盖心跳停止。填完 preparation 后由配置和部署入口执行，不先启动已知缺项的候选。

Maven 辅助调用结构（绝对路径由 Agent 核验后替换）：

```sh
python3 <product-root>/projects/tapdata/scripts/test-environment-maven.py \
  --maven <absolute-mvn> --java-home <absolute-jdk-home> \
  --local-repository <verified-maven-runtime> -- install -Penterprise,idaas -DskipTests
```

所有 build 命令使用 argv 数组，不进行 shell 二次解析；项目 shell 入口显式 bash。源码指纹在构建前后核验，变化时保留候选不部署；不使用旧 bundle 或同名缓存 Jar 冒充本轮成果。完整包缺项、架构、Java/Node 和 Mongo 副本成员通过后才替换原环境。许可证文件预检只能证明存在，启动后还需验证其有效性及匹配条件。构建失败日志仅私有保留，用户只看到具体缺项、影响和继续路径。

## 4. 启动并逐层核验

| 顺序 | 操作 | 成功标志 |
|---|---|---|
| MongoDB | 核对本次配置的连接、认证与拓扑 | 应用所需数据库操作可用 |
| TM | 用分支启动脚本、可执行 Jar 或 IDE 入口 `com.tapdata.tm.TMApplication` 启动 | 实际监听地址正确，认证成功，可获取该实例的 FE Access Code |
| Engine（FE） | 用分支启动脚本或入口 `io.tapdata.Application` 启动 | 指向本次 TM，注册与心跳正常，目标连接器已加载 |
| Web（按需） | 使用实际开发脚本，例如分支确有的 `pnpm dev:daas` | 代理指向本次 TM，登录及目标页面请求正常 |

Spring Boot 可执行 Jar 的启动结构如下。先替换占位符，并核对目标分支支持该配置参数；普通依赖 Jar 不能代替可执行包。

```sh
cd <station>/runtime/app/tm
<absolute-java> -jar <absolute-tm-executable-jar> \
  --spring.config.additional-location=file:<station>/runtime/effective-config/tm.yml

cd <station>/runtime/app/flow-agent
<absolute-java> -jar <absolute-engine-executable-jar> \
  --spring.config.additional-location=file:<station>/runtime/effective-config/fe.yml
```

保留原生进程/会话标识和日志路径。健康路由从分支源码查证；进程存活、首页可访问都不能替代认证、插件加载及目标业务场景验证。

### 连接器编译与注册

[连接器 Skill](../skills/tapdata-connectors/SKILL.md)负责收集操作、一个或多个连接器和注册目标；[项目脚本](../scripts/connectors.py)从当前源码 POM/规格发现模块，以原生 Maven 与 PDK CLI 执行。不构建或重启整个开发环境，不修改 `.agenticops/`，不增加工位兼容边界。临时制品和回执放 `runtime/tapdata-connectors/<id>/`，由既有 runtime 清理与归档合同管理。

三个模式共享 `--connector <名称或模块>`，重复传参选择多个。`list --station <station> [--repository tapdata/tapdata-connectors]` 列出当前仓库实际模块；同名歧义需精确模块或仓库。企业连接器使用当前工位已有的对应仓库路径，不自动追加源码或改变分支。Agent 按[依赖构建指引](build-test-and-local-run.md#依赖构建命令)准备公共库、Java、Maven 与当前任务隔离仓库；PDK CLI 采用本分支原生产物，例如 `tapdata-cli/target/pdk.jar`，先核对 `register --help`，缺少时仅构建其模块与依赖。

```sh
# 仅编译；用户输入连接器，技术参数由 Agent 依据当前项目准备
python3 <product-root>/projects/tapdata/scripts/connectors.py build --station <station> \
  --connector Dummy --connector MongoDB --connector MySQL \
  --maven <absolute-mvn> --java-home <java-home> --local-repository <task-maven-repo>

# 仅注册；不触发构建
python3 <product-root>/projects/tapdata/scripts/connectors.py register --station <station> \
  --connector Dummy --connector MongoDB --record <build.json> --env <name> \
  --pdk-cli <absolute-pdk.jar> --java-home <java-home> \
  --latest
```

将操作改为 `build-register` 并提供两组参数即可编译后注册，构建失败不上传。编译使用 `clean package -pl <所选模块逗号列表> -am`，避免旧 target 的时间戳 Jar 冒充本轮制品。记录源码 HEAD/工作树、所选模块及拷贝制品的 SHA-256；注册时核验当前源码与记录、全部待传文件校验值，发现变化先重新编译。`--skip-tests` 只用于明确的临时打包，不代表业务测试通过，后续按项目质量指引验证。

注册必须明确目标：`--env` 使用当前活动命名开发环境的一个 TM，未活动时不自动切换；其它研发环境用 `--tm-url <服务根地址>`，禁止地址中带账号、密码或 token。命名环境默认读取 `config/secrets/tapdata-test-env/<name>/admin-password`，由用户填写纯文本管理员密码，权限 0600，末尾换行不计入密码；不自动获取或生成密码。显式 `--credentials` 可指定 `config/secrets/` 内其它秘密文件；JSON 三选一：`{"password":"…"}`（当前原生 CLI 只支持 admin@admin.com）、`{"access_code":"…"}`（旧版 Access Code）或 `{"access_key":"…","secret_key":"…"}`。access_token 不能当作 access_code。使用权限 0600 的临时 Picocli @file，不在 argv、公开输出或回执保存凭据，结束删除参数文件，原生输出仅留私有日志。

原生 RegisterCli 默认 latest=true；辅助脚本明确传 false，只有用户意图需要更新默认版本时才加 `--latest`。同 group/pdkId/version/build 的 SNAPSHOT 注册仍可能覆盖旧制品，即使 latest=false 也不是“绝不替换”；正式非 SNAPSHOT 覆盖由 TM 原生拒绝。当前企业版管理员注册会暂停关联任务/校验、等待停止并尝试恢复；先核对实际影响，未授权的业务影响待决定，不通过换认证方式绕过。批量按连接器顺序执行，一个失败即停，保留已提交项；未知结果先回读，不自动重复整个批次。

UploadFileService 上传成功后可能原地加密 Jar，辅助脚本为每项建立部署副本，编译制品保持不变。回执保留上传前 MD5、原始 SHA-256、部署副本 SHA-256；退出码为 0 仅标记 submitted，verified 保持 false。Agent 从目标分支 API/源码取得每个规格的 pdkId、group、version 和 PDK API build，使用已授权认证只读查询 `/api/DatabaseTypes`，核对实际 jarFile、pdkHash、latest、版本及 `/api/pdk/checkMd5/v3` 等本分支支持的校验入口与上传前 MD5。不能只凭同名连接器或 Completed 宣称通过；查询权限不足或异常时为待核验。

需要验证 FE 已应用时，对照实际下载/加载日志中的 pdkHash、版本和文件校验值，必要的连接测试或任务验证按原范围执行；注册与 FE 加载是不同结果，不自动重启任务/Engine。原生依赖或脚本异常按已有连续性规则接管，不改注册协议、数据库或本地任务状态来伪造通过。

## 5. 常见卡点与交接

| 现象 | 优先处理 |
|---|---|
| TM 起不来或 Engine 不注册 | 先查脱敏日志、Java/产物类型、Mongo 认证与拓扑，再查 TM 地址、端口、Access Code 和许可 |
| 新代码没有生效 | 对照构建制品、注册/下载结果和实际加载路径；不要反复只重建同名 Jar |
| 插件注册时报只读错误 | 检查注册是否原地处理 Jar；需要写入时使用本任务 runtime 部署副本，保留原始哈希和转换记录 |
| Web 构建失败 | 按当前分支核对 Node、包管理器和脚本，不默认套用旧 OpenSSL workaround |

交接给研发：源码与制品版本、实际命令、环境身份、已验证行为、日志引用和剩余问题。仅在用户选择当前会话执行 TapTest 时继续 [TapTest 执行](taptest-development.md#4-执行与分析结果)。退出时只处理已核实归属的资源；授权、归档与清理统一按[任务指引](../../../docs/usage/task-authorization.md#配置化清理入口)执行，不在此另设流程。
