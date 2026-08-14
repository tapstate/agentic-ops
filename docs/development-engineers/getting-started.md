# 研发工程师上手

本文面向使用 AgenticOps 指导研发员处理业务 Jira 任务的研发工程师。这里使用 developer 工作面；不得进入 `maintainer`、加载 AgenticOps 源头规则或调用 `ao-maint`。

需要一页式初始化入口时，参阅 [初始化 AgenticOps 研发员](agent-init.md)。

## 1. 准备清单

安装前确认：

- 可访问 `tapstate/agentic-ops` 的 GitHub 账户和 `gh` 登录状态。
- 业务项目工作空间目录，例如 `~/agentic-ops-tapdata`。
- Jira 项目空间或 Project Profile，例如 `tapdata` / `TAP`。
- 该研发员唯一的 Jira 邮箱和 API token。
- 默认源码仓库或明确的本地源码目录。

不得从其它工作空间、本机全局 `.env`、个人记忆或旧聊天中自动补齐凭证和项目事实。

## 2. 安装 developer 工作面

```sh
gh auth login -h github.com -p ssh -s repo

gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main' \
  | bash
```

安装目标是 `~/.agentic-ops` 的 developer-only sparse managed clone。Bootstrap 与 `ao-work` 都会校验 origin 必须是 `tapstate/agentic-ops`，普通使用不能用环境变量改写受信仓库；正常文件树只包含 developer 生产资产、只读的 `shared/integration/` JSON 协议及运行所需的根版本元数据，不包含 `maintainer/`、`developer/tests/`、fixture 或 fake producer。

验证入口：

```sh
source "$HOME/.zshrc"
ao-work --help
```

也可使用完整路径：

```sh
~/.agentic-ops/bin/ao-work --help
```

没有 `agentic-cli` 兼容别名；看到旧命令说明正在阅读冻结迁移基线或使用旧版本。

## 3. 初始化业务项目工作空间

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
ao-work workspace init
```

零参数入口进入交互确认。必须确认：

- `agent_id`：默认由纯小写主机名规范化得到，只能包含 `[0-9a-zA-Z_-]`。
- Jira 项目空间 / Project Profile。
- Jira 站点、研发员账户和授权状态。
- 默认仓库和源码目录。
- Git、GitHub、Jira 访问等前置检查。

只有缺失或冲突的项才需要额外参数；Connection 默认由 Project Profile 推导，不要求普通用户传 `--connection-id`。

初始化最后写入 `.agentic-ops/agent.json`、当前工作空间 `AGENTS.md` 和 `.agents/skills/`。该 AI 入口固定进入 developer 工作面；Codex 从标准仓库级 `.agents/skills/` 发现受管 developer Skill，规则正文直接写入 `AGENTS.md`，标准资产由 `ao-work` 从受信安装根解析。业务仓库不需要也不应创建不存在的 `developer/...` 相对路径。

业务项目 AI 工作空间与源码仓库必须使用两个独立目录，不能相同，也不能互相嵌套。默认源码目录会创建在工作空间同级目录。工作空间位于某个 Git 仓库时，初始化会把该工作空间的 `.agentic-ops/` 写入该仓库的 `.git/info/exclude`；Jira token 等身份状态不得出现在 `git status` 中。生成的 `AGENTS.md` 和 `.agents/skills/` 是 AI 直接可发现的受管副本；`workspace preflight` 会检查 Skill 缺失、漂移、额外资产和 maintainer 污染。

不要在下列位置初始化业务工作空间：

- `~/.agentic-ops`。
- `tapstate/agentic-ops` 源头仓库或其 worktree。
- 另一个研发员的业务项目工作空间。
- 业务源码仓库本身或其任意子目录。

## 4. 授权与验证

首次初始化可以直接完成授权，也可以随后运行：

```sh
ao-work auth jira show
ao-work auth jira set
ao-work auth jira verify
ao-work capability list
```

`set` 无参数时进入隐藏输入；token 不通过命令行参数传递。授权只保存在当前业务项目工作空间，`~/.agentic-ops` 和其它工作空间不继承。执行具体业务操作前运行 `ao-work capability show <operation>`；`capability_gap` 表示当前版本没有安全原子操作，需要按中文 `next_action` 转人工，不能尝试旧命令。

## 5. 启动 AIAgent

在已初始化的业务项目工作空间中启动 Codex 或其它受支持 AIAgent。AI 应从当前目录的 `AGENTS.md` 自动进入 developer 工作面，不需要读取 AgenticOps 根 `AGENTS.md`。

初始化后可以发送：

```text
列出我名下可以接管的 Jira 任务。
接管 TAP-123；信息不足时先结合代码形成补卡建议并写回 Jira，接管后先把修复计划写入 Jira等我确认。
确认该设计，并授权在当前 Jira 工作项、仓库、任务分支、目标分支和验证范围内连续推进到拉取请求审查；范围或风险变化时停下。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

这些自然语言需求不代表对应自动化都已实现。AI 必须先查询能力目录；当前任务列表、正式接管与恢复、任务释放、PR / CI、分支对齐、完成审计和 Custom Field 写入等仍可能返回 `capability_gap`，应由研发工程师按目录指引接管，不能用内部 `task init` 冒充 Jira 接管。

## 6. 问题反馈与快速改进

任务处理中出现无法自动完成、人工干预过多或输出质量不足时：

1. 先由研发工程师校对，确保当前业务任务正确完成。
2. 让 AI 形成脱敏的问题总结、期望行为、建议沉淀位置和回归方法。
3. 人工确认改进方案。
4. 在独立 AgenticOps worktree 中切换到 maintainer 工作面完成改进并创建 `develop` PR。

业务工作空间不得直接修改 `~/.agentic-ops` 或调用 `ao-maint`。工作面切换必须通过独立目录和独立 AI 入口完成。

## 7. 更新与回滚

更新使用安装目录中的独立 developer Bootstrap；不要通过重复执行安装命令静默替代更新确认：

```sh
~/.agentic-ops/developer/bootstrap/update.sh
```

需要回滚时显式执行：

```sh
~/.agentic-ops/developer/bootstrap/rollback.sh
```

更新和回滚只改变 developer-only managed clone 与锁定 Python 环境，不修改各业务项目工作空间的 Jira 身份和任务状态。更新目标与当前 ref 必须先展示并由研发工程师确认；非交互模式不得静默接受。

## 8. 常见问题

### 找不到 `ao-work`

```sh
source "$HOME/.zshrc"
~/.agentic-ops/bin/ao-work --help
```

### GitHub 权限不足

```sh
gh auth status
gh auth login -h github.com -p ssh -s repo
```

### 工作面不匹配

如果输出 `workplane_mismatch`，检查当前目录是否为已初始化的业务项目工作空间、AI 是否读取了本目录 `AGENTS.md`、调用入口是否为 `ao-work`。不要用 mode 参数或复制维护规则规避阻断。
