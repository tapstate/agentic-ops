# tapdata-common-lib 升级

> 项目适配：`tapdata`

本文用于所有 `tapdata-common-lib` 代码修改前的兼容性判断，以及公共 API 或版本变化时的影响分析、同步和验证。它不授权制品发布、release 分支创建或跨仓推送。

来源：《TapData 产品研发指南（v3.5.4+）》，提取日期为 2026-07-27；移植自 tapstate/agentic-ops（2026-08-27）。

## 适用条件

修改 `tapdata-common-lib` 下任何工程的代码前，先判断是否影响 API、二进制或消费行为兼容性；无法确认时保留待核验结论，不按兼容变更继续。以下情况尤其需要分析消费方：

- 修改 `tapdata-api` 或 `tapdata-pdk-api` 的公共接口。
- 修改 `plugin-kit` 使用的 API 版本。
- Tapdata 核心工程或连接器开始依赖新的公共能力。
- Jira 卡片要求升级公共库版本。

仅修改公共库内部实现且不影响消费契约时，仍需验证受影响模块，但不得无理由扩大到全仓版本升级。

## 影响分析

先在目标分支确认实际属性名和引用位置：

```sh
rg -n \
  'tapdata-api.version|tapdata\.api\.verison|tapdata\.pdk\.api\.verison|tapdata-api|tapdata-pdk-api' \
  .
```

重点检查：

```text
tapdata-common-lib/tapdata-api/pom.xml
tapdata-common-lib/tapdata-pdk-api/pom.xml
tapdata-common-lib/plugin-kit/pom.xml

tapdata/manager/pom.xml
tapdata/iengine/pom.xml
tapdata/iengine/iengine-app/src/main/resources/pluginKit.properties

tapdata-connectors 中实际消费新 API 的公共模块和 connector
tapdata-enterprise、tapdata-connectors-enterprise 等实际消费新 API 的模块
```

列表用于提示搜索范围，不能替代目标分支搜索结果。属性名中已有的 `verison` 拼写属于现存协议字段，不得在无独立 Jira 范围时顺手更名。

## 不兼容变更与分支

不兼容变更前，先通知 jarad 或 sam，由其从当前 `main` 创建 `release-v*` 分支保留升级前代码；核验远端分支和对应的 main 提交后，再推进不兼容修改与依赖版本升级。release 名称及版本由负责人确认，不自行猜测或创建；等待期间可继续影响分析和测试准备。通知需明确仓库、main 提交、兼容性影响及待确认版本，不能把待通知写成已通知。

TapData 主仓为 `develop` 时，公共库使用 `main`；完整分支关系以[分支矩阵](../version-branch-alignments.json)为准。

## 同步规则

1. 根据 Jira 卡片和目标分支确认需要发布或引用的版本，不得由 AIAgent 自行决定版本号。
2. 同步公共库自身模块、`plugin-kit` 及核心工程消费属性。
3. 只有连接器实际使用新 API 时，才更新对应连接器依赖，避免无关全仓升级。
4. 企业版和闭源连接器无法读取或验证时，必须明确列为未验证范围并请求对应仓库负责人确认。
5. 不得只更新版本字符串而跳过 API 二进制兼容性、编译和测试分析。

## 构建与验证

先按[构建与本地运行](build-test-and-local-run.md#maven-配置与任务本地仓库)读取当前 run 的 `local_repository` 和绝对 Maven 可执行文件。保留用户 Maven settings 中的镜像、认证和代理，但所有命令必须显式使用该任务本地仓库。

以下命令用于本地验证。CI 中公共库各工程通过 `deploy` 发布到 Nexus，TapData 的 CI 打包从 Nexus 获取相应版本的依赖；本地 `install` 成功不能证明 CI 可获取制品。核验公共库部署结果、Nexus 中对应坐标和版本及消费 CI 的依赖解析和打包结果；制品未就绪时保留 CI 待验证，不以本地制品替代。

本地按依赖方向执行：

```sh
# tapdata-common-lib
<absolute-mvn> -Dmaven.repo.local=<local_repository> clean install -T1C -U

# tapdata 核心受影响模块
<absolute-mvn> -Dmaven.repo.local=<local_repository> -pl <module> -am test

# tapdata-connectors 中使用新 API 的模块
<absolute-mvn> -Dmaven.repo.local=<local_repository> -pl <connector-module> -am test
```

跨仓验证至少覆盖：

- 公共库新增或修改 API 的自动化测试。
- 核心工程受影响模块的编译和测试。
- 实际消费新 API 的 connector 测试。
- 目标 Jira 验收场景。

无法运行某个消费仓库时，不得声称公共库升级已完成全链路验证。

## 人工门禁

以下动作必须由研发工程师确认：

- 向内部 Maven 仓库发布或部署制品。
- release 分支通常通知 jarad 或 sam 创建，正式版本号由负责人决定。
- 推送多个业务仓库。
- 修改未包含在 Jira 范围内的消费仓库。

## 输出证据

完成分析和验证后输出：

- 公共库变更模块和版本。
- 受影响消费仓库、文件和属性。
- 已执行命令及结果。
- 已验证和未验证的仓库、模块与场景。
- 兼容性风险。
- 等待人工确认的发布或跨仓动作。
