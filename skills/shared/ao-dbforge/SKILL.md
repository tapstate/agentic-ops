---
name: ao-dbforge
description: 使用下载的 DBForge CLI 申请、查询、续期和释放临时数据库或预置 TapData 实例，并为显式选择的 IT 注入租户配置。
metadata:
  product: agenticops
---

# DBForge

用于研发和测试的临时实例。使用当前或工位绑定 Product Root 的 `skills/shared/ao-dbforge/scripts/dbforge.py`，只使用租户 token。默认服务为 `http://192.168.1.200:30880`，默认配置为 `~/.dbforge`；原配置及其中的 admin_key 保留，调用不发送管理凭据。用户配置不随任务归档或工位清理删除。

## 准备工具与凭据

首次使用执行 `python3 <helper> prepare`，按宿主平台下载 macOS/Linux arm64/amd64 CLI，验签后安装到 Product Root 的 `.local/tools/dbforge/`。首次验签使用已有 OpenSSL 3，不自动安装依赖。缺少配置时先生成权限 0600 的模板，给出绝对路径，引导用户在本机填写 token；不要在聊天索取或回显凭据。已有配置不覆盖，填好后执行 `cli -- capabilities` 验证真实鉴权与能力。

已安装工具用 `update --if-installed` 复用原生更新，失败保留旧版。TapData 新任务入口自动尝试一次，原操作恢复不重复调用。其它项目需要时显式更新。显式 `--config <path>`、`--url <root-url>` 放在子命令之前，不改写默认配置。来自另一服务的凭据先核实使用范围。

## 申请、使用与释放

先 `cli -- capabilities` 查询实际类型、模式和拓扑；按场景明确选择，不把所有数据库都当成支持 CDC，不把 shared 当独享。部署请求优先使用 DBForge；无法满足时由项目沿已有部署方式接续。创建结果未知时先回读，不直接换环境重建。

调用沿原生 CLI 参数，服务、凭据、输出及恢复身份由 helper 管理。示例：

```sh
python3 <helper> cli --record <private-runtime>/mysql.json -- create -db-type mysql -mode dedicated -topology single -endpoint-scope external -ttl 60 -owner <任务或用途>
python3 <helper> cli --record <private-runtime>/mysql.json -- describe
python3 <helper> cli --record <private-runtime>/mysql.json -- renewal -extend 30
python3 <helper> cli --record <private-runtime>/mysql.json -- destroy
```

record 是权限 0600 的本机恢复文件，创建前保存幂等请求，成功后包含租约和完整连接信息。同一 record 的调用互斥，忙碌时等待原调用结束后回读；旁边的 `.lock` 文件随恢复材料保留，不单独删除。Agent 只按需要读取，普通输出已隐藏密码与连接串；不要把原文件写入 Jira、Git 或普通档案。重复 create 只回读原租约，不能用旧请求复活已过期实例。服务地址漂移时先核实原实例，不能在新服务上按旧 ID 释放。

在授权范围内从实际消费者验证网络、认证、数据库功能及所需权限，再报告可用。申请时显式传 `-ttl 60` 并按服务限制校验；省略参数会采用服务端默认值，不保证 60 分钟。需要延长时显式续期，不承诺后台自动续期。临时实例不保存唯一成果；必要测试证据在租约过期或释放前独立保全。

绑定任务时，复用现有 `station_resources.py` 外部资源登记入口记录 `kind=external`、服务地址/lease_id、producer、处置及回读依据；不手改任务状态。使用后先核验测试进程停止，再沿原 record 释放；`reclaim_pending` 保持待收尾。仅 `released` 或已确认不存在才可回报完成，并通过所属资源入口记录回读。任务清理先完成这些外部资源处置，运行目录丢失不证明远端已释放；空闲工位使用也要在删除 record 前核实收尾。

## 项目与 IT

项目负责所需部署组件、制品来源和测试入口。DBForge `tapdata` 只用于服务侧已准备且来源可核验的发行包；当前任务构建包使用项目现有 Docker 流程。完整组件用 tar 模式；TM+engine JAR 模式不能当成包含 WebUI、API Server 和 connector 的完整应用。许可证或服务侧包缺项时明确说明，沿原部署授权回退，不申请管理权限修集群。

IT 默认沿原配置执行。只有用户运行前选择 DBForge，且实际源码与加载依赖支持 `CONNECTOR_IT_CONFIG_SOURCE=dbforge` 时，用 `it --source dbforge --cwd <测试仓> --log <private-runtime>/it.log -- <原生测试命令>`。该入口仅给子进程注入 token、URL、external 端点、60 分钟 TTL 与独立审计 owner，实例仍由原框架申请、释放，不另建重复租约。先核对测试类预算小于租约期限；长测试先选择其它环境。

`it` 缺省 `--source json`；Maven 的 `-Dconnector.it.*` 优先于环境变量，执行前由 helper 拒绝会覆盖本次选择的参数。日志可能含业务凭据，保存权限 0600，分析和分享前脱敏。测试失败保留原结果，不自动回退或重跑；退出码 0 不代替真实用例、版本和报告核验。异常退出按审计 owner、时间与租约回读核实残留，不能把 TTL 兜底当成已释放。
