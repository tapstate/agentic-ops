# AgenticOps 首次使用指引

这篇只带你完成一条使用路径：安装 AgenticOps、明确选择产品项目、创建工位并接管第一个 Jira 任务。项目、工位路径和任务号须按实际情况填写；其它选项可沿用产品默认值。

开始前准备：Git、Python 3.9+，并确保 Git SSH 已获得 `tapstate/agentic-ops` 的读取权限。Jira/Atlassian 插件在首次实际使用时按[必需 MCP 配置](usage/mcp-setup.md)引导安装和登录，不是首次启动的前置条件；GitHub 工具由 Agent 按任务自行选择。不熟悉术语时查看[术语表](glossary.md)。

## 1. 安装

按[Git SSH 安装](usage/git-ssh-install.md#首次安装)执行默认安装命令。该入口获取安装脚本并调用脚本完成安装，无需手工克隆产品目录、写安装状态或重复指定 `--repository`；自定义目录、源码池、分支及仓库来源也由该页说明。已有 GitHub CLI 时可使用 [gh 一键安装](usage/gh-one-click-install.md)。

默认产品安装在 `~/.agentic-ops`，跟随 `main` 发布分支；设置 `AGENTIC_OPS_HOME` 时安装位置使用该值。源码池默认是 `~/.agentic-ops-repos`，仅加速任务接管时的独立源码下载。以下命令以默认安装位置为例，自定义安装时替换产品入口路径。

## 2. 创建项目工位

工位放在业务代码之外。首次初始化必须显式指定 `--project`，不默认选择 TapData。以下示例为 TapData 创建 `~/agenticops-tapdata`；其它项目需使用安装中已有的项目 ID，并调整工位路径：

```sh
~/.agentic-ops/agenticops station init \
  --station "$HOME/agenticops-tapdata" --project tapdata
```

已有工位再次初始化时可以省略 `--project`，沿用原绑定；显式传入同一项目也可。指定不同项目会停止，不能用 init 或 repair 在线切换。确需更换项目时，先由原版本结束任务、归档释放或清理，再显式 purge，之后重新初始化；保留材料的采用见[工位源码与材料](usage/station-materials.md)。缺少或无效项目不会创建新工位或写入登记。

不传 `--agent` 时会接入全部可用 Agent。接着检查接线：

```sh
~/.agentic-ops/agenticops station doctor --station "$HOME/agenticops-tapdata"
```

## 3. 启动 Agent

进入工位并启动你要使用的 Agent。使用 Codex：

```sh
cd "$HOME/agenticops-tapdata"
./agenticops station start codex
```

新工位不生成通用 Agent Hook；旧工位若提示迁移，按[常见问题](usage/faq.md)显式核对和迁移。首次使用 Jira 事实时，Agent 会检查必需插件并在缺失时引导安装和登录；GitHub 工具由 Agent 按任务自行选择。Claude Code 会读取工位生成的 `.mcp.json`；使用 Claude Code 时，将最后一行替换为 `./agenticops station start claude`。

初始化会读取全局 Git `user.name` 并在其可作为分支前缀时一次性保存为工位 `git_name`。未设置或值不合法时，先用 `git config --global user.name <合法 Git 提交用户名>` 补充，再在工位内执行 `./agenticops station identity`；它用于稳定生成和恢复任务分支，不会读取或保存 Git 凭证。

在已初始化工位内，所有需要单个工位目标的 `agenticops station` 操作默认使用当前目录；传入 `--station <目录>` 时以传入目录为准。`--all` 始终是显式批量操作，不会被当前目录替代。

## 4. 接管第一个任务

在同一 Agent 会话中直接发送下面这句话，把 `TAP-123` 换成实际 Jira 任务号：

```text
接管 TAP-123。
```

Agent 会先读取 Jira 和项目准入规则，确认产品版本并接管完整 source 工程，随后登记修改仓，然后给出方案。方案、风险和实现授权需要你确认；事实、权限或门禁不明确时，它会停下并说明下一步。接管不是自动提交、推送或合并：这些操作仍需用户授权并受平台与服务端权限约束；Workflow 只在检查点核对确认和证据。

## 接下来可能需要

- [Git SSH 安装](usage/git-ssh-install.md)：默认安装命令的独立说明。
- [gh 一键安装](usage/gh-one-click-install.md)：无法使用 Git SSH 时的备用安装方式。
- [工位源码与材料](usage/station-materials.md)：独立源码及清理后明确复用持久材料。
- [任务授权指引](usage/task-authorization.md)：用脚本从空闲工位接管 Jira 任务，再完成准入、基线和实施授权。
- [更新与回退](usage/update-and-rollback.md)：更新安装、修复工位接线或回退一次更新。
- [常见问题](usage/faq.md)：安装失败、Hook、任务恢复和本地清理。
- [问题反馈流程](usage/problem-feedback.md)：所有项目研发在工位接管和执行任务中，通过统一入口总结问题、整理必要复现资料并提交 AO，由维护者从问题列表统一处理；技能可用性以实际交付为准。

维护 AgenticOps 源码本身，请改看[维护指引](maintenance-guide.md)，不要把源码仓库当作业务使用工位。
