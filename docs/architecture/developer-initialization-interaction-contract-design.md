# Developer 初始化参数与交互契约设计

## 背景

`ao-work workspace init --project tapdata` 在交互终端仍会再次询问 Project Profile，`ao-work auth` 也会对已经由参数提供或安装中已有的完整身份逐项提示。现有 `workspace init --confirm` 还把“接受普通输入”与“确认覆盖既有配置”混为同一门禁，造成不必要的重复确认。

本设计服务 AO-57，目标是让 developer 工作面的初始化交互只在真实覆盖风险处暂停，同时保持安装级身份和 token 的安全边界。

## 决策

1. 删除 `workspace init --confirm`，不保留兼容别名。
2. 参数优先级固定为：显式 CLI 参数 > 环境变量默认值 > 现有安装/工作空间可复用值 > 交互输入。
3. 交互模式只提示最终仍缺失的值：显式 CLI 参数和可用默认值不得再次询问。`auth` 对完整已有安装身份不重复提示；token 仍只能通过隐藏输入或 `--token-stdin` 提供。
4. `--confirm-existing-config` 是唯一的初始化覆盖确认：只有本次有效配置会覆盖已有、完整且不同的工作空间配置时才需要它。新工作空间、半初始化修复和配置完全一致均不确认。
5. 交互模式遇到覆盖差异时必须先展示字段级差异，再只询问一次是否覆盖；非交互模式缺少 `--confirm-existing-config` 时以 `existing_config_confirmation_required` 失败关闭。
6. 环境变量只参与候选值解析，不能覆盖显式参数，也不能因为其来源而触发覆盖确认。确认依据只能是“已有完整配置”和“本次有效配置”的字段差异。

## 实现范围

- `developer/runtime/src/ao_work/workspace_init/cli.py`：移除通用确认，按缺失值进行交互提示，并在覆盖风险时展示差异、执行一次确认。
- `developer/runtime/src/ao_work/workspace_init/service.py`：把既有配置比较收敛为可审计的差异结果，供 CLI 展示与 preflight 复用；保持非交互失败关闭。
- `developer/runtime/src/ao_work/authorization/cli.py`：仅对缺失身份字段提示；显式参数优先，保持 token 的隐藏输入/标准输入约束。
- `developer/standards/contracts/operations/workspace-init.yaml`、研发工程师初始化文档和手册：删除 `--confirm` 示例，明确优先级、覆盖差异和非交互行为。
- developer Runtime 测试：覆盖 CLI、环境、已有配置、新配置、半初始化、同配置、冲突覆盖、交互提示与 token 输入边界。

## 不在范围内

- 不改变 maintainer 工作面、`ao-maint`、Jira 写入、任务接管或 Git 审查门禁。
- 不把 token、身份或授权写入业务工作空间。
- 不为环境变量增加独立“接受”门禁，也不因来源不同改变覆盖判定。

## 验收

- 显式 `--project`、身份参数和 `--confirm-existing-config` 在交互终端不重复提示。
- CLI、环境变量、已有安装值及交互输入遵循固定优先级，且环境变量不能覆盖 CLI。
- 新建、半初始化修复和相同配置不确认；完整不同配置显示差异并只确认一次。
- 非交互的覆盖请求未传 `--confirm-existing-config` 时失败关闭。
- 完整 developer 与维护固定验收通过，安装引导文档与操作契约不再出现 `workspace init --confirm`。

## 风险与缓解

- 配置差异展示遗漏字段会使人无法判断覆盖影响：差异由 Runtime 的同一比较函数生成，测试覆盖全部受管字段。
- 删除 `--confirm` 会让旧自动化失败：这是有意的失败关闭；文档同步迁移到不带该参数的新命令，避免静默忽略旧门禁。
- 授权交互误把 token 回显：token 路径保持 `getpass` 或安全标准输入，差异摘要不包含 token。
