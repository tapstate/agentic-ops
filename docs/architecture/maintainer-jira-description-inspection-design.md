# maintainer Jira Description 完整回读与安全覆盖设计

## 1. 任务绑定

- Jira：AO-54
- 父任务：AO-43
- maintainer run：`maint-AO-54-20260821013446-50c55d8e`
- 仓库：`tapstate/agentic-ops`
- 工作分支与目标分支：`develop`
- 审查通道：形成未推送本地 commit 后，以提交编号执行推送前代码审查

## 2. 目标

保持现有简洁入口：

```sh
ao-maint jira inspect --issue-key AO-43
```

该命令除既有任务摘要、状态、类型和负责人外，还必须返回完整 Jira Description 的原始 ADF、可读 Markdown、纯文本、完整性标志和内容摘要。维护者据此生成完整替换稿；`jira description plan/apply` 必须绑定计划时的原 Description，外部事实漂移时在写入前失败关闭。

本任务最终使用该能力读取 AO-43，形成包含 developer 安装、安装级 `ao-work auth`、简化 `workspace init` 和本地验证安装步骤的完整替换稿。替换稿仍属于独立 Jira 写入门禁，必须向指导员展示完整内容后再 apply。

## 3. 当前事实与根因

1. `JiraClient.get_issue()` 已请求 `description` 并保留原始 ADF，`JiraIssue.description` 也已承载该字段；缺口位于 `execute_jira()` 的 inspect 输出层，它主动丢弃了 Description。
2. 现有 `plain_text()` 只递归拼接文本，会丢失标题、列表、代码块、链接和任务项结构，不能作为“完整可审查内容”。
3. 当前只有 Markdown → ADF 转换，没有 ADF → Markdown 的严格转换；直接声称纯文本等于完整 Description 会掩盖结构损失。
4. `plan_description()` 读取当前 Description，但计划只保存目标 Markdown 和目标正文摘要；`apply_description()` 没有校验旧 Description 是否仍与 plan 时一致，存在确认后被他人修改仍覆盖的竞态。
5. 写后回读只比较纯文本，格式节点丢失但文本相同仍可能被误报为成功。

## 4. 输出合同

`ao-maint jira inspect` 保持原命令和参数不变，在 `issue` 中增加：

```json
{
  "description": {
    "format": "atlassian_adf",
    "adf": {},
    "markdown": "...",
    "plain_text": "...",
    "complete": true,
    "unsupported_node_types": [],
    "sha256": "..."
  }
}
```

- `adf`：Jira 返回的完整原始 ADF；空 Description 固定为 `null`。
- `markdown`：由 Runtime 严格转换的可读内容，不静默猜测未知节点。
- `plain_text`：只作为检索和辅助阅读，不作为完整性证据。
- `complete`：所有 ADF 节点和 marks 均已被转换时为 `true`；存在未知结构时为 `false`。
- `unsupported_node_types`：稳定排序后的未知 node/mark 类型。
- `sha256`：原始 ADF 经过稳定 JSON 规范化后的摘要；空值也有稳定摘要。

若 `complete=false`，inspect 仍返回完整原始 ADF和已知部分的 Markdown，但明确提示不能基于该 Markdown 执行覆盖更新。Runtime 不截断字段；调用侧显示能力不足时必须让用户查阅完整 Runtime 输出，不能把截断片段当作完整 Description。

## 5. ADF 转换边界

在 maintainer Jira ADF 模块增加 ADF → Markdown 的严格转换，至少与现有 Markdown → ADF 支持集合对称：

- doc、paragraph、heading、rule、codeBlock；
- bulletList、orderedList、listItem；
- taskList、taskItem；
- text、hardBreak；
- strong、em、code、strike、underline、subsup、link marks。

转换器返回 Markdown、纯文本、完整性和未知类型集合，不抛弃原始 ADF。未知节点使用明确占位提示并把 `complete` 置为 `false`；未知 mark 保留文本但同样标记不完整。空值和合法空 doc 统一为可读空内容，但原始摘要保持可区分。

## 6. Description 写入并发保护

`jira description plan` 在现有目标字段之外增加：

- `expected_previous_description_sha256`：plan 时原始 ADF 的稳定摘要；
- `target_description_sha256`：目标 Markdown 转换为 ADF 后，移除 Jira 非语义随机属性再计算的稳定语义摘要。

`apply` 固定顺序：

1. 校验计划完整性、AO 项目边界、人工授权与凭据外发边界；
2. 重新 inspect 当前 issue；
3. 当前原始 ADF 摘要与 `expected_previous_description_sha256` 不一致时返回 `jira_description_precondition_changed`，不得 PUT；
4. 当前已等于目标语义摘要时幂等完成；
5. 写入计划中目标 Markdown 对应的 ADF；
6. 回读并比较目标语义摘要，不只比较纯文本；不一致时返回 `jira_description_readback_failed`。

语义摘要只移除由 Runtime/Jira 生成且不表达用户内容的随机属性，例如 task node 的 `localId`；不得忽略 node 类型、层级、顺序、文本、marks、链接地址或代码块属性。

旧 Description 计划文件缺少新字段时失败关闭并要求重新 plan，不提供隐式兼容，以免绕过写前事实绑定。

## 7. 工作面与安全边界

- 只修改 maintainer Runtime、maintainer 测试及必要的人读文档；developer Runtime 不新增 AO Jira 能力。
- 非 AO issue 继续在加载凭据或联网前阻断；远端回读 Project 不是 AO 时阻断。
- inspect 是只读操作，不创建写计划、不记录伪写入证据、不修改 Jira。
- 输出不得包含 Jira token、Authorization header、原始请求日志或安装外凭据。
- AO-43 Description 替换仍使用 `ao-maint jira description plan/apply`；不使用浏览器、Connector、直接 REST 或临时脚本。

## 8. 测试与验收

新增或修订自动测试，至少覆盖：

1. inspect 返回原始 ADF、可读 Markdown、纯文本、完整性和稳定摘要；
2. 空 Description 和合法空 doc 输出稳定；
3. 支持的标题、列表、任务项、代码块、链接和 marks 可完整转换；
4. 未知 node/mark 不丢失原始 ADF，且 `complete=false`；
5. 非 AO key 在 transport 前阻断，远端 Project 漂移阻断；
6. plan 保存旧 Description 摘要和目标语义摘要；
7. plan 后旧 Description 漂移时 apply 在 PUT 前阻断；
8. 写后文本相同但 ADF 结构不同仍判定回读失败；
9. 旧计划字段、篡改摘要和敏感内容失败关闭；
10. AO-43 真实 inspect 返回完整 Description，替换稿经人工确认后 plan/apply/readback 一致。

固定完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

提交前对 staged 候选执行故事影响和固定验收；`develop` 只形成未推送本地 commit，按提交编号完成代码审查后才允许 push。

## 9. 风险与停止条件

- 真实 AO-43 出现当前转换器不支持的 ADF 节点时，不得继续生成替换 plan；先展示未知节点和原始 ADF，再进入范围/风险决策。
- Jira 对写入 ADF 做未建模的语义规范化时，先补充可证明不丢内容的规范化规则和测试；不得放宽为纯文本比较。
- inspect 输出、旧 Description 摘要、目标替换稿或远端事实任一变化，旧确认失效。
- 若实现需要新增命令层级、允许非 AO 项目、写受管输出文件、自动合并旧 Description 或放宽全量替换门禁，视为范围变化并重新进入设计审查。

## 10. 交付结果

- 现有 `ao-maint jira inspect` 可完整、可解释地回读 AO Description。
- Description plan/apply 具备写前事实漂移保护和结构化写后验证。
- AO-43 初始化脚本更新建立在完整现状回读与人工确认之上。
- 后续 maintainer 更新 AO Description 不再依赖浏览器或绕过 Runtime。
