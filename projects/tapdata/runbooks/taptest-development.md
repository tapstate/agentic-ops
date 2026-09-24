# TapTest 开发指引

用于 TapData 任务中的 `tapdata/t-layer3-test`：理解需求与复用用例 → 生成场景 → 开发脚本 → 配置环境 → 执行与分析结果。由 [tapdata-task](../skills/tapdata-task/SKILL.md) 导航，不依赖业务仓的两个用例技能，也不走 Java Maven 测试流程。已完成的阶段直接复用。

在工位中沿用当前任务的源码路径和工作分支，不照搬独立仓库的新建分支、stash 或推送流程。缺少测试仓时使用[同周期方案返工](../../../docs/usage/task-authorization.md#同周期方案返工)判断追加范围，不为补测试仓丢弃已有成果。

## 1. 理解需求并生成用例

先读取需求、确认预期、关联 Xray Test、相关 PR 和现有用例断言。对需求先判断：现有能力是否已满足？预期是否有依据？有无更小的验证方案？给出推荐和理由；只有影响正确性、范围或权限的分歧才交研发决策，不机械增加确认轮次。

已有完整用例优先复用；需求复制出来的占位 Test 补成可执行场景，只为未覆盖行为新增。必要时查测试仓 `.context/kb` 和 `.context/api_docs`，再以任务源码核对接口，不靠猜字段名设计断言。

每个场景写清：覆盖需求、前置条件与数据、操作步骤、可观察断言、超时及资源清理。反向检查它能否区分旧缺陷与正确行为，是否可能被旧数据或旧制品“误通过”。复杂需求拆为能独立执行和判断的用例，不按数量拆分。

需要落 Jira 时，在任务授权内创建/更新 Xray 并关联需求，回读类型、负责人和内容。QA Test Type 与 Xray 原生 Test Type 不同，使用当前项目字段和值；API/数据流、Web、人工场景按实际规则分类。可复用已核验的 `auto_test/ext/jira/issue.py` helper 或原生工具。仅需设计时交付草案，不自动创建工作项。

成功标志：每个预期都有明确断言和可准备的前置条件，研发能审查覆盖与缺口；已有具体用例不必重新生成。创建后的 Xray ID 是脚本主标识，需求 ID 只作追溯。

## 2. 开发脚本

读取相近用例、目录 AGENTS 和公共 helper，沿用现有分组与 `item_<组号>_<Xray数字ID>[_序号].py` 命名；无适用组时使用现有 `g999_ungrouped` 约定。选择器以当前发现器结果为准。

- 顶层元数据：`title`、`desc`、`case_type`、`case_priority`、`required_datasources` 元组；desc 关联 Xray 和需求。按需设置 `case_run_mode`（parallel/serial）与 `case_schedule`（nightly/manual）。
- 从相近用例复用 `item()`、`ItemResults`、`Pipeline`、`Connection`、`S`、`testcase_sn` 和数据库 helper，核对真实签名，使用隔离资源名。
- 断言业务结果与数据内容，不只断言 HTTP 200；等待有界，失败能说明预期与实际差异。成功清理本用例资源，失败保留必要证据并停止持续消耗资源的操作。

先对本次文件做不导入模块的语法检查，避免导入时触发登录或数据源操作。替换占位符后执行：

```sh
<absolute-python> -c 'import ast, pathlib, sys; [ast.parse(pathlib.Path(p).read_text(encoding="utf-8"), filename=p) for p in sys.argv[1:]]' <case-file.py>
```

成功标志：语法通过，发现器能识别目标用例，步骤与断言对应设计。此时只能说明脚本完成，实际通过需下一阶段的运行结果。

## 3. 配置开发环境

先按 [TapData 开发指引](tapdata-development.md#4-启动并逐层核验)核实目标实例与实际加载制品，再准备 Python 和数据源。优先复用已核验虚拟环境，检查根 `requirements.txt`、解释器版本/架构及原生库；共用 helper 可能引入 ODBC 等间接依赖，不能只看用例使用的数据库。

确需新环境时，在依赖安装获准后执行下列结构，保留实际安装版本；不修改全局 Python。依赖增改仍按现有授权与供应链要求处理。

```sh
<approved-python> -m venv <station>/runtime/taptest-venv
<station>/runtime/taptest-venv/bin/python -m pip install -r <test-source>/requirements.txt
```

持久配置放 `config/taptest`。参考仓库 `auto_test/config/taptest.example.yaml` 和 `datasources/ds.example.yaml` 的结构，填写本环境地址、凭据及实际数据源，不复制共享 token。配置示例中的占位符须替换，含秘密文件仅本地当前用户可读：

```yaml
env:
  server: "<已授权的-TM-host:port>"
  access_token: "<该实例实际签发的凭据>"
datasources: "<absolute-station-config>/taptest/datasources.yaml"
runner:
  python: "<absolute-station-runtime>/taptest-venv/bin/python"
  threads: 1
  skip_jira: true
  send_lark_msg: false
  repeat_until_fail: false
```

显式指定配置和数据源文件，避免落到默认共享配置。解释器优先级为 `--python` → `TAPTEST_PYTHON` → `runner.python` → `python3`；检查继承的 server、token、数据源等环境覆盖项，不输出秘密。根 `taptest` 还会加载 shell profile、代理设置和可能存在的 `.venv`，应核对实际使用的解释器与目标地址。

成功标志：目标 TM/Engine 可认证、制品正确，所需数据源名称、权限和连接可用。环境缺项不阻止继续设计和静态开发。`./taptest install`、`./run install`、`./run reset_db` 有安装或删除副作用，不当作环境检查命令。

## 4. 执行与分析结果

先核对目标实例及资源影响：参考版本运行器的预处理会创建数据源、重置共享 CDC/心跳/缓存任务，并停止或清理部分旧 TapTest 任务，选择单用例也不消除这些影响。优先使用隔离测试环境；共享环境的影响范围未获授权时暂停执行。

从测试仓根显式选一个完整用例验证，再按已确认范围扩大。替换全部占位符后执行：

```sh
cd <test-source>
skip_jira=true send_lark_msg=false taptest_repeat_until_fail=false \
  ./taptest run -t=<完整用例选择器> \
  -f=<absolute-station-config>/taptest/local.yaml \
  --python=<absolute-station-runtime>/taptest-venv/bin/python --threads=1
```

成功标志：目标用例确实执行，业务断言通过，报告绑定当前产品制品与测试源码。核对逐用例结果、实测计数、跳过和超时；退出码 0 或空报告不算 PASS。日志采用运行器实际输出位置，脱敏后保留到任务证据；不要发明输出目录参数。

| 常见弯路 | 推荐处理 |
|---|---|
| 选错范围或意外回写 | 不省略 `-t`，不默认加清缓存的 `-n`；默认关闭 Jira/飞书回写，但用例和预处理仍会写环境 |
| 等待指标一直超时 | 核对版本是否支持该指标，再判断业务结果；不要无限加 sleep 或把超时改成通过 |
| 单用例/分组行为不一致 | 分组默认排除 manual，完整选择器可能包含 manual；检查调度与实际副作用 |
| 参数、依赖或路径异常 | 对照当前入口和配置解析器；参考入口含 eval/未完整引用的转发，不传不可信或含 shell 特殊字符的参数 |
| 修完后拿旧报告验收 | 分别记录产品各仓 SHA/加载制品和测试仓 SHA/差异，不能只记录测试 PR 的版本 |

失败先归因产品、用例、环境或运行器，再在已有范围内修复和复测；中断先查仍在运行的进程及远端任务。临时补丁保留差异与影响，不作为所有环境的默认操作。

## 5. 交接结果

给出需求/Xray/选择器、产品与测试源码版本、实际加载制品、环境、命令、结果、日志引用和未覆盖范围。分别说明“用例已设计、脚本已完成、实际执行结果”，避免把阶段成果混成通过。

功能验证、GitHub Ready for Review、AgenticOps PR Ready 与 Jira 状态分开报告；缺少 CI 或 Checks 被跳过不能靠本地 PASS 改写。具体接纳与推进使用[质量检查与证据](../../../docs/usage/quality-checkpoints.md)，包括其中 TapTest 的 Jira 状态依据，本指引不复制门禁规则。

## 源码依据

参数与配置核对参考版本：t-layer3-test `7d8fe414272dfe6ec3f41a3d5993055f97cea88d`。换版本时按需查根 `taptest`、`auto_test/run_tests.sh`、`auto_test/items/run.py`、`auto_test/utils/config.py` 和 `auto_test/config/README.md`；以实际任务分支为准，不把历史 workaround 当成必需步骤。
