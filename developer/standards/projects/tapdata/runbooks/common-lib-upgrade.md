# tapdata-common-lib 升级

> 工作面：`developer`

本文用于 `tapdata-common-lib` 公共 API 或版本变化时的影响分析、同步和验证。它不授权制品发布、release 分支创建或跨仓推送。

来源：《TapData 产品研发指南（v3.5.4+）》，提取日期为 2026-07-27。

## 适用条件

出现以下任一情况时使用本手册：

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

## 同步规则

1. 根据 Jira 卡片和目标分支确认需要发布或引用的版本，不得由 AIAgent 自行决定版本号。
2. 同步公共库自身模块、`plugin-kit` 及核心工程消费属性。
3. 只有连接器实际使用新 API 时，才更新对应连接器依赖，避免无关全仓升级。
4. 企业版和闭源连接器无法读取或验证时，必须明确列为未验证范围并请求对应仓库负责人确认。
5. 不得只更新版本字符串而跳过 API 二进制兼容性、编译和测试分析。

## 构建与验证

按依赖方向执行：

```sh
# tapdata-common-lib
mvn clean install -T1C -U

# tapdata 核心受影响模块
mvn -pl <module> -am test

# tapdata-connectors 中使用新 API 的模块
mvn -pl <connector-module> -am test
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
- 创建 release 分支或决定正式版本号。
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
