# TapData 构建、测试与本地运行

> 项目适配：`tapdata`

本文用于帮助 AIAgent 在 Tapdata 任务中选择构建、测试、本地启动和日志检查方式。命令是参数化示例，执行前必须用目标分支配置校验。

来源：《TapData 产品研发指南（v3.5.4+）》，提取日期为 2026-07-27；移植自 tapstate/agentic-ops（2026-08-27）。

## 执行前确认

开始前必须确认：

- Jira 卡片、目标仓库、问题分支和问题版本。
- 目标分支声明的 JDK、Maven profile、包管理器和构建脚本。
- 变更涉及核心工程、公共库、企业版、连接器还是前端。
- 需要运行的最小测试和跨模块验证范围。

目标分支的 `pom.xml`、`package.json`、构建脚本和 CI 是事实源。手册示例与目标分支不一致时，不得猜测或强行套用。

## 用例缺口判断

功能与缺陷共用此步骤。Agent 在方案分析时建立判断，在代码完成、报告返回或来源分支同步后按最新差异更新；不要求研发列出需要补充的用例。依据包括冻结基线到当前代码的差异、已确认验收场景、受影响 Maven 模块及上层 Jar 消费模块、现有用例的实际断言，以及目标分支的测试框架配置。

逐个行为场景记录以下内容，保存到当前 run 的交互材料并引用到现有质量检查项和 Jira 测试总结：

| 内容 | 要求 |
|---|---|
| 变更与预期 | 仓库、代码版本、变更位置、验收来源和预期行为 |
| 已有证据 | 用例路径/方法、实际断言及框架入口；只有名称或执行成功不足以证明覆盖 |
| 判断 | 复用、新增、修改或框架不支持，并说明已有断言为何足够或缺少什么 |
| 范围与缺口 | 应测模块和场景、当前未覆盖内容、补充方式及判断依据 |
| 覆盖报告 | 可回查报告及其源码版本、统计口径；未取得写“未取得”，不得填零或推算百分比 |
| 研发处置 | 仅在需要决策时记录决定来源、原因、接受的未覆盖范围及后续动作 |

复用须证明已有断言覆盖本次行为和相关边界；新增用于尚无目标行为断言；修改用于已有场景的已确认预期发生变化或断言不足。覆盖率只提供线索：执行到某行不证明断言正确，覆盖率高也不免除缺口分析。无覆盖工具或报告时继续静态分析并如实标注，不为统计数字引入新框架。

框架不支持时，Agent 说明缺少的能力、受影响场景和可用替代验证，交研发决定是否创建/关联后续任务或采用其它处理。创建任务必须有明确决定；研发明确暂不处理时记录其原因和接受范围，继续无依赖工作。用户接受的缺口仍是未覆盖，不登记为 PASS；验收预期不清、事实不可信时暂停相应判断，不凭实现反推预期。

## CI 用例开发与质量核对

框架支持且判断需要新增/修改时，Agent 在当前功能或缺陷任务的已授权工作树内完成，不等待研发补充实现细节，不建立独立 CI 用例任务。复用目标模块已有框架、fixture、命名和报告入口；不以任务便利为理由安装另一套测试框架。TapTest 继续使用其已有独立任务闭环。

1. 先从确认预期定义输入、可观察结果、边界与失败条件，再写断言；明确用例验证的行为及其验收来源。新增行为至少覆盖正常路径及与本次变更相关的异常或边界，数量按风险判断，不机械凑数。
2. 用例与代码在同一工作树开发。检查真实调用链、断言、初始化与清理、等待超时和环境隔离；Mock 掉被验证行为的用例不能证明该行为的集成验证。数据库写入使用授权测试资源，失败也应清理本次数据。
3. 优先用同一用例证明旧代码失败、新代码通过。保留旧新源码、用例版本、命令和报告；旧版失败必须落在目标行为断言，编译失败、环境故障或未加载目标 Jar 不能作为缺陷复现。只在隔离目录验证旧版，不回退当前任务或覆盖研发修改。新增 API 无法在旧版编译等情况说明原因，改用边界场景或受控反例检查断言有效性。
4. 定向运行用于调试用例，代码和用例完成后仍执行受影响 Maven 模块全量验证（含上层消费模块），不能用单用例 PASS 替代。记录用例文件版本、代码/Jar 版本及对应报告；用例变更后原结果失效。
5. Agent 再次审查断言是否独立于实现、有无吞异常/空断言/无条件跳过，是否通过放宽比较、扩大超时或修改预期掩盖产品问题。修正用例必须有验收依据；改变已确认验收含义先交研发。报告缺口反馈到上一节重新判断。

通过现有质量 `item` 将场景关联到用例引用和版本，原生执行后以 `execute` 保存实际结果及报告引用；步骤或预期变化按现有选择确认规则处理，不伪造用户确认。开发完成不等于验证通过，旧版不可执行不等于已经证明旧失败/新通过。Jira 讨论总结实际覆盖、失败及未覆盖范围，不能用描述替换测试讨论。

## PR CI 报告回读

`workflow/ci.py watch` 只观察 PR Checks，不下载报告，也不证明目标用例执行。Agent 使用原生 GitHub 工具回读每个任务仓库当前 PR Head、检查链接和实际运行，按检查链接定位运行，不能用“这个分支最新一次成功”替代本 PR 的事实。

原生 CLI 示例（仓库、PR 和运行编号取本次回读，参数不是固定配置）：

```sh
gh pr view <pr> --repo <owner/repo> --json headRefOid,statusCheckRollup
gh run view <run-id> --repo <owner/repo> --json databaseId,headSha,attempt,status,conclusion,url,jobs
gh run view <run-id> --repo <owner/repo> --attempt <attempt> --log
```

下载实际测试报告到当前 run 的受控交互目录，保留运行编号、attempt、artifact 标识、下载来源、报告文件哈希及执行时间。重跑时核对报告属于本次 attempt，不能混入前次残留；只有工作流成功而无报告时明确“缺报告”。原生日志可能含凭据，Jira 仅写必要的脱敏摘要及可访问引用，不复制原始日志。

逐项核对：

1. PR Head、运行关联的 headSha 与实际构建/测试源码。工作流使用合成 Merge 提交、其它仓库 checkout 或缓存 Jar 时，从 checkout 日志及 Git 事实核对实际提交和 PR Head 的对应关系，并记录各仓源码与实际消费 Jar；不把工作流元数据的 Head 直接当实际测试源码。
2. 目标分支 workflow 的触发、路径过滤、矩阵、profile 和测试命令，与本任务应测模块及场景对照。测试报告列出实测用例、成功/失败/错误/跳过计数及缺失模块；没有目标用例或零用例均不能证明通过。
3. 失败时读取具体断言、异常和必要日志，区分当前变更、已有失败、环境与归因不明；比较基线或进行受控对照，有证据才归因。非当前变更或归因不明交研发决定，不能因 Checks 红色就直接修改代码或用例。
4. 回读期间 PR Head 变化则重新获取当前运行与报告。原报告按原提交保留，不改写为新提交；未结束、取消、跳过、未执行、缺报告与失败分别披露。

对照质量检查项导入实际 `execute` 证据：`origin=ci`，实际用例与代码版本，`source_ref` 指向精确运行/报告。无法映射到现有结果枚举的缺报告、缺执行或版本不明记录为 UNKNOWN，并在 observation 保留具体原因；不能填 PASS。报告总结以 Jira 讨论回写并回读，至少包含各仓代码/测试版本、应测/实测/未覆盖范围、运行和报告链接、结果计数、失败归因与后续处理。评论失败保留同步待办，不丢失本地分析。

### 任务级报告分析

一个任务的所有仓库、PR 均须列入本轮报告清单，缺报告也保留对象。先用 `task.py repository context` 取得当前任务仓库登记，再用原生 GitHub 工具逐 PR 回读 Head、工作流、run、Job、矩阵及上传步骤。只在验证目的、配置和范围相同且存在明确规则时判断运行替代关系；不能挑选成功运行，也不能把不同配置的新运行自动当作旧运行的替代。选择无法确定时保留候选，`selection` 写 `unresolved`。

原生下载应保存 ZIP 到 `interaction-path` 提供的当前 run 路径；可使用 `gh api repos/<owner>/<repo>/actions/artifacts/<artifact-id>/zip` 获取精确 artifact，再用下述脚本直接读取 ZIP，无需解压。权限不足、过期、不存在、上传失败、取消、超时、排队和运行中分别记载；可恢复的网络错误有限重试，其它对象继续处理。上传日志等证据才能证明 attempt，不能从名称或时间猜测；部分 Job 重跑按已有 CI 规则关联各自来源，不伪装成单次完整执行。

`maven_reports.py` 只读取已取得的本地目录、单份 XML 或 ZIP，不调用 GitHub、不执行测试、不选择有效运行、不写任务状态。命令输出 JSON 到标准输出，Agent 将其保存在当前 run 的受控交互路径。退出码 0 仅表示处理结束，包含缺报告和解析失败的正常输出；输入契约无效返回 2，不能把退出码当作测试 PASS。

```sh
python3 projects/tapdata/scripts/maven_reports.py --source <downloaded-artifact.zip>
python3 projects/tapdata/scripts/maven_reports.py --source <module>/target/failsafe-reports
python3 projects/tapdata/scripts/maven_reports.py --input <report-inventory.json>
```

任务清单是可再生的分析输入，不是新增任务事实源。每个对象是一组有独立来源的报告，多个 artifact 分别登记；一个 artifact 可包含多个模块，输出按文件路径保留，不凭文件名猜模块。`context` 保存原生回读的精确版本及运行，`provenance` 保存归属判断及证据，`execution` 分别保存测试退出码、Job/run 状态；未知信息不填写虚构值。示例：

```json
{
  "schema_version": 1,
  "snapshot": {"source_ref": "当前 run 仓库登记及 PR 回读文件", "observed_at": "实际读取时间"},
  "objects": [
    {
      "id": "repo-a-pr-101-job-1-artifact-1",
      "repository": "owner/repo-a",
      "context": {"pr": 101, "head_sha": "完整提交", "checkout_sha": "实际测试提交", "run_id": 1, "job_id": 1, "artifact_id": 1, "artifact_url": "原生回读的下载来源"},
      "selection": "unresolved",
      "provenance": {"status": "unknown", "source_ref": "原生回读记录；attempt 尚未核实"},
      "execution": {"exit_code": null, "job_conclusion": "success", "run_status": "completed"},
      "path": "/absolute/path/to/artifact.zip"
    },
    {"id": "repo-b-pr-202", "repository": "owner/repo-b", "context": {"pr": 202}, "reason": "artifact 已过期"}
  ]
}
```

逐对象输出报告文件哈希、套件和用例、计数、耗时、失败/错误/跳过明细及解析异常。Failsafe 汇总只交叉核对，不重复累加；参数化实例保留名称，无法区分的重复身份报冲突，重试异常保留在明细。任务不生成跨运行去重总计，也不输出验收 PASS。目录/ZIP 中非 Maven 明细 XML 不参与计数；仅支持无命名空间的 Maven testsuite/testsuites，不支持的格式显式披露。压缩文件在内存逐文件读取，限制文件数和大小，拒绝危险路径、符号链接和 XML DTD/实体。异常正文只做常见凭据脱敏且截断，写 Jira 前仍需人工可见内容审查，不复制全部原始报告。

Agent 对照已确定应测清单核对报告完整性；没有清单时只能说“已取得报告的分析完成”。分析结束前再次回读任务成员和各 PR Head，变化的对象记待增量核对，保留已完成结果，不无限追赶。原始文件哈希只证明内容，`input_sha256` 只标识本轮输入，均不认证外部来源。`processing_complete` 表示每个清单对象都有结果或缺失原因，不等于报告完整、测试成功或任务完成。

执行状态和报告计数分开解释：XML 无失败但命令非零、超时或取消时不得称为执行通过；CI 成功但缺报告仍是证据缺口。失败归因需有断言、异常和必要对照依据；跳过原因未记载就说明未知，先分析影响再按现有规则提交具体人工处置。用户接受风险不改写原始失败或跳过。通过现有 `quality.py verification`/`execute` 以 `report_ref`/`source_ref` 引用保存的分析文件，填写逐项真实计数和结果；脚本不自动生成 PASS 或用户接受决定。

TapData 保留底层先合并、上层后合并的流程。本阶段跨仓消费版本只读取已有日志、构件或执行证据：有证据记录到 `dependency_observation`，没有则披露“实际消费版本未核实／跨仓组合尚未证明”，不新增门禁，不改现有验证规则。各 PR 分别通过不代表跨仓组合通过；底层合并后按已有流程分析上层新报告。本能力不补跑、不修改 CI、不推导用例缺口、不实现依赖失效传播。

## 后端工具链

Tapdata 4.x 核心工程当前基线为 JDK 17。执行前记录：

```sh
java -version
mvn -version
```

公共库可能同时声明多个 JDK profile，必须根据目标分支和实际消费工程选择，不得因为核心工程使用 JDK 17 就批量修改公共库兼容配置。

Maven profile 必须从目标分支的 POM、构建脚本或 CI 获取。企业版、DAAS、OSS 等构建不能只依据历史文档中的 profile 名称。

## 构建顺序

### Java 变更影响范围

由 Agent 根据 Git 变更定位模块，核对直接及间接消费其 Jar 的模块（包含跨仓、test/provided/optional 依赖），再确定必测范围。目录聚合 `<modules>` 不等于 Jar 依赖。修改父 POM、BOM、profile 或构建配置时还需检查配置传播；动态加载、反射、脚本引用及缺失模型由 Agent 调查或保守扩大范围，不要求研发补依赖图。

优先使用目标代码、JDK、settings 和 profile 下的 Maven `help:effective-pom`，不自行实现 Maven 继承与属性解析。对每个待分析模块用 `-N -f <module>/pom.xml help:effective-pom -Doutput=<absolute-output>` 采集单模块模型；显式 profile 使用目标分支已有配置。失败时保留解析缺口，不以空依赖继续。该 Maven 操作可能更新本地插件缓存，但不执行测试。

多模块或跨仓闭包可使用 `python3 projects/tapdata/scripts/java_impact.py --input <inventory.json>`。输入由 Agent 根据刚采集的模型构造，不进入任务持久状态；每个模块记录模型文件 SHA-256、实际源码完整提交、相对 POM 路径和 profile。工作区有未提交修改时，在任务证据中另记 diff 及模型生成时间；这些字段是采集来源，不是脚本对 Git 实时状态的认证。代码或模型配置变化后须重新采集。

```json
{
  "schema_version": 1,
  "modules": [
    {
      "id": "repository:module",
      "repository": "owner/repository",
      "source_revision": "<完整 Git SHA>",
      "pom": "module/pom.xml",
      "profiles": [],
      "effective_pom": "module-effective.xml",
      "sha256": "<模型文件 SHA-256>"
    }
  ],
  "changed_modules": ["repository:module"],
  "unresolved": ["未取得的仓库或模型、待核实的动态引用"]
}
```

脚本仅对输入模型计算反向消费闭包，分别输出测试候选、构建前置、依赖路径及版本差异；所有 scope 均保守纳入，版本不一致不删除边。Agent 必须核对输入清单覆盖真实消费仓库及适用 profile，将配置传播或动态加载影响补入最终范围。不能把脚本没有列出的模块直接判定为无影响，也不能把候选清单或成功打包作为测试执行证据。

最终报告明确应测、实测和未覆盖模块及原因；选中的 Java 模块内部全量执行集成测试。存在消费版本差异时，执行前证明上层模块使用本次变更构建的 Jar；仅坐标相近或本地已有同名 Jar 不足以证明。

### Maven 模块选择执行与报告

按 Maven 模块处理，不按 FE、TM 或连接器分别建立流程。现有连接器 CI 入口调用 `tapdata/tapdata-it` 的共享 workflow，其自动目录筛选不代替上面的完整影响分析。Agent 将最终确定的本仓模块逐个传入 `maven_tests.py plan`，不重新缩小到直接改动目录。跨仓分别生成清单；同一模块需要运行两类测试时分别生成清单并汇总，不能仅选其中一类后宣称全部验证完成。

核对目标分支有效 POM 和测试内容后选择 `--framework failsafe`（默认，`src/it/java`、`target/failsafe-reports`）或 `--framework surefire`（`src/test/java`、`target/surefire-reports`）。两者都执行所选模块内该框架的全量适用用例。Surefire 结果注明普通或混合模块测试，不能仅因 Maven 成功而宣称集成覆盖。其它布局或缺少对应框架列为覆盖缺口，由 Agent 核实或按研发决定处理；不擅自切换业务分支来寻找测试。

```sh
python3 projects/tapdata/scripts/maven_tests.py plan \
  --repo <connector-repository-root> \
  --module connectors/mysql-connector \
  --module connectors/mongodb-connector > <outside-repository>/plan.json
```

有已核实 profile 时重复提供 `--profile <name>`。清单绑定当前 Git Head、未提交 diff 和未跟踪文件；存放在仓库之外或已忽略的运行目录，避免记录本身改变源码快照。清单不是新的任务状态，也不授权任何外部操作。

Agent 用原生工具按清单中的 `cwd` 和 `argv` 顺序执行：先 `install -pl <selected> -am` 准备依赖（跳过测试），成功后逐模块运行 `clean test-compile failsafe:integration-test failsafe:verify` 或 Surefire 的 `clean test`，显式启用测试。不对测试命令使用 `-am`，避免把构建依赖误当必测范围；应测的上层模块必须已单独列入清单。不使用单类/方法过滤，不照搬 CI 中关闭 TLS 验证、全局代理或写 settings 的环境操作。

跨仓依赖先按依赖方向在原生工具中完成本次源码的 `clean install`，保留构建前后源码快照、实际命令及成功退出记录。核对版本坐标，不为通过而临时修改消费版本。记录本次构建的 Jar 与实际消费路径：普通 Maven 依赖核对有效测试 classpath；动态插件还需核对下载源及实际加载文件，不能仅看 POM。生成清单时通过 `--dependency-repo <producer-root>` 绑定生产仓库快照，通过 `--jar-pair <built-jar> <consumed-jar>` 核对内容并保存 SHA-256；两者均可重复。内容不一致立即报告，测试完成后源码或任一 Jar 变化会使核验失败。哈希一致只证明两个文件内容相同，不能代替本次构建记录或实际加载路径证据。

执行前核对有效 POM、settings、环境参数中没有额外过滤用例、跳过测试或改变报告位置的配置。`clean` 会清除所选模块构建产物，需确认待保留的日志/证据已另存；依赖准备失败时不得继续使用缓存 Jar 冒充本轮准备完成。数据库、凭据和测试资源必须使用已授权环境；环境未就绪不等于测试通过。模块内能力不支持、框架缺失或非本次变更失败，按研发决定处理并记录。

每个模块执行前后用 `time.time_ns()` 记录时间，保留原生命令输出与退出码。执行记录格式如下（时间和退出码必须取实际结果）：

```json
{
  "plan_id": "<清单 plan_id>",
  "modules": [
    {"module": "connectors/mysql-connector", "started_ns": 0, "finished_ns": 0, "exit_code": 0}
  ]
}
```

```sh
python3 projects/tapdata/scripts/maven_tests.py report \
  --plan <outside-repository>/plan.json \
  --execution <outside-repository>/execution.json
```

核验会拒绝变更后的源码快照、已声明依赖变化或不同清单的结果；逐模块列出缺执行、缺报告、旧报告、零用例、跳过和失败。只有本轮用例报告计数完整且退出成功时才标记模块通过，未提供结果的模块保留在报告内。解析后的摘要不含测试日志正文或连接凭据。该结论不证明断言质量或测试配置无过滤，Agent 仍须结合原生执行事实核对；这些整体证据由共同流程接入，不在本脚本复制流程门禁。

执行清单为可再生的临时分析文件，当前格式为 2；旧连接器清单需用当前入口重新生成，不保留旧命令兼容入口。不改变 `.agenticops/` 工作空间持久状态或 epoch。

### 依赖构建命令

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
