# AgenticOps Skill 维护规范

本文定义 AgenticOps 仓库内不同 Skill 的事实源、可见范围、发现接线、生命周期和验证合同。产品方向与分层分别以[项目目标](strategy/project-goals.md)和 [v1 工程架构](architecture/agenticops-v1-architecture.md)为准；本文不替代 Policy、Gate、Workflow 或项目规则，也不记录工作项、进度和验收状态。

本文定义实现必须满足的稳定合同；文档或 Skill 目录存在本身不代表接线已经生效，只有第 7 节的本地合同与真实 Agent 范围验证完成后，才能声明对应能力可用。

## 1. 核心原则

- Skill 本体是 Agent 无关的通用资产；同一份定义必须能被不同 Agent Adapter 接入使用。
- Git 中的 Skill 目录是唯一事实源；原生发现目录只保存 Bootstrap 通过薄 Adapter 生成的受控链接或视图。
- AgenticOps Skill 不注册到用户级、管理员级或系统级 Skill 目录，不复制到多个位置。
- 维护 Skill 只在源码产品根目录可见；项目 Skill 只在绑定对应产品项目的工作空间可见。
- Skill 只指导 Agent 协作。可确定执行或必须强制的规则仍进入 Contract、Policy、Gate、Workflow 或 Project，不能依赖 Skill 散文代替门禁。
- Agent 原生发现目录、展示元数据和调用协议属于平台差异，必须由 Agent Manifest 与薄 Adapter 声明；Skill 源目录和公共 Bootstrap 不出现 Agent 类型分支。
- 所有生成、检查、刷新和清理都必须失败关闭：不覆盖未登记文件，不跟随异常符号链接，不删除目标已漂移的链接。

## 2. Skill 分类与事实源

| 类型 | 唯一事实源 | 使用范围 | 不得进入 |
|---|---|---|---|
| 维护 Skill | `skills/<skill-name>/` | 源码产品根目录的维护工作面 | 安装产品根目录、业务工作空间、用户级 Skill 目录 |
| 项目 Skill | `projects/<project>/skills/<skill-name>/` | 绑定该产品项目且接入对应 Agent 的业务工作空间 | 其它产品项目工作空间、用户级 Skill 目录 |
| 个人或外部 Skill | Agent 平台自己的个人或插件目录 | 由用户或平台独立管理 | AgenticOps Git、安装包和受管接线清单 |

个人或外部 Skill 不属于 AgenticOps 产品资产。它不得复制 AgenticOps 已管理的 Skill，也不得使用相同名称形成第二事实源。平台自带 Skill 和插件 Skill 同样不由 AgenticOps 安装、更新、回退或清理。

每个受管 Skill 至少包含：

```text
<skill-name>/
├── SKILL.md                  # 必需：name、description 和协作指引
├── references/               # 可选：稳定引用资料
├── scripts/                  # 可选：必须确定执行的轻量辅助检查
└── assets/                   # 可选：模板或静态资源
```

`SKILL.md` 的 `name` 必须与目录名一致。Skill 不保存任务状态、授权、凭证、客户数据或原始敏感日志；脚本不能复制 Runtime、Policy、项目状态机或 Agent Adapter。Skill 源目录不得包含 `agents/openai.yaml`、Claude 专用提示或其它 Agent 私有配置；平台确需额外展示元数据时，由对应 Adapter 从通用 `name`、`description` 和资源生成可再生视图。

## 3. 可见范围与发现接线

```text
通用 Skill 事实源
        │
        ├── Bootstrap 选择业务作用域：maintenance 或 project
        │
        └── Agent Manifest + 薄 Adapter 选择平台发现方式
                    ├── Codex  → .agents/skills/<skill-name>
                    └── Claude → .claude/skills/<skill-name>
```

源码产品根目录的维护 Skill 链接解析到 `skills/<skill-name>/`；项目工作空间的项目 Skill 链接解析到 `<product-root>/projects/<project>/skills/<skill-name>/`。两者都使用相对路径，最终解析到当前产品根目录内的同一份通用 Git 事实源。

生成链接或平台视图不是新的 Skill 定义，也不是规则事实源。Adapter 不得修改 Skill 的名称、触发语义、工作流步骤、安全边界或资源内容。

| 启动位置 | 维护 Skill | 当前项目 Skill |
|---|---:|---:|
| 源码产品根目录 | 可见 | 不自动接入 |
| 安装产品根目录 | 不可见 | 不自动接入 |
| 绑定后的业务工作空间 | 不可见 | 可见 |
| 无关仓库或个人会话 | 不可见 | 不可见 |

## 4. 薄 Adapter 边界

每个 Agent Manifest 只声明一个通用 `skill_target`，表示该 Agent 的原生 Skill 发现根目录。Codex 当前值是 `.agents/skills`，Claude 当前值是 `.claude/skills`。Bootstrap 根据当前工作面决定接入维护 Skill 还是项目 Skill，再把同一通用 Skill 映射到 Manifest 声明的目标；Manifest 不决定 Skill 的业务作用域。

薄 Adapter 只允许承担：

- 原生发现目录映射；
- 平台要求的可再生展示元数据或协议包装；
- 不支持能力的显式声明和保守降级。

薄 Adapter 不得复制或改写 Skill 指令，不得读取或保存任务状态，不得引入 Project、Policy、Workflow 依赖。通用 Skill 即使没有任何平台展示扩展，也必须能仅凭 `SKILL.md`、脚本和引用资料被支持该标准的 Agent 使用。

## 5. 生命周期归属

### 5.1 维护 Skill

维护 Skill 的接线属于源码产品根目录生命周期：

1. `agenticops setup` 首次建立维护工作面后，枚举 `skills/` 中的通用 Skill，再通过各 Agent Manifest 的 `skill_target` 生成原生发现链接或视图。
2. `agenticops update` 同步源码、维护依赖和受信 Hook 后，按当前 Git 内容刷新链接；新增 Skill 自动接入，已删除 Skill 的旧链接只在所有权和目标均匹配时移除。
3. `agenticops doctor` 在源码产品根目录执行时只读检查维护 Skill 清单、链接目标、越界和同名冲突，不把产品根目录当成业务工作空间。
4. Bootstrap 在 `.local/maintenance-skill-wiring.json` 记录生成产物的路径、类型和链接目标，用于检查所有权和安全清理；该文件不提交，也不是 Skill 事实源。

维护接线只允许在以下条件全部满足时生成：当前目录含源码标识、产品状态为 `mode=source`、目录是该状态记录对应的源码产品根目录、Skill 来源和链接目标都没有越出产品根目录。安装产品根目录即使包含同名路径也必须拒绝生成。

### 5.2 项目 Skill

项目 Skill 继续复用现有工作空间接线：

1. `agenticops init` 根据所选 Project 确定通用项目 Skill，再通过 Agent Manifest 的 `skill_target` 链接到工作空间的原生发现目录。
2. `.agenticops/init.json` 记录受管链接；`doctor` 只读检查，`repair` 幂等刷新。
3. Project 或 Agent 集合变化不能静默覆盖既有绑定，必须遵守工作空间清理与重建边界。
4. 删除项目 Skill 后，`repair` 只能删除清单中登记且目标未漂移的旧链接。

维护 Skill 和项目 Skill 可以在各自目录使用相同的 Agent 原生目标，但来源、产品根目录和产物清单必须隔离，不能用项目工作空间的 `init.json` 管理维护面接线。

## 6. 禁止用户级注册

AgenticOps 的 setup、update、install、init、doctor 和 repair 均不得写入：

```text
~/.codex/skills/
~/.agents/skills/
~/.claude/skills/
/etc/codex/skills/
```

也不得通过复制、安装个人 Skill、修改用户级 Codex 配置或创建指向产品根目录的用户级符号链接来补偿仓库接线缺失。用户级位置会跨仓库生效，无法表达维护面、安装面和业务工作空间边界，并会造成 Git 事实源与个人副本漂移。

若发现已有用户级 AgenticOps Skill，必须按以下顺序迁移：

1. 只读比较用户级副本与 Git 事实源，列出 `SKILL.md`、元数据、脚本和引用资料差异。
2. 将仍需保留的内容作为正常仓库变更合入对应 `skills/` 或 `projects/<project>/skills/`，完成固定验证。
3. 通过源码维护面或项目工作空间的原生链接验证 Skill 已在正确范围可见。
4. 回读待删除的精确用户级目录和差异；取得单独删除授权后才清理副本。
5. 重启或刷新 Agent，再验证无关仓库不再发现该 Skill。

不得先删除用户级副本再补内容；删除、仓库修改、提交和推送仍是独立授权。

## 7. 变更与验证合同

新增、更新或删除 Skill 时，至少验证以下回归：

- 资源合同登记 Skill 的 `SKILL.md` 及必需脚本、引用资料，目录名与 `name` 一致；Skill 源目录不包含任何 Agent 专用配置。
- 源码维护面 setup/update 能新增、刷新和安全移除维护 Skill 链接；doctor 能报告缺失、越界、目标漂移和未受管同名文件。
- 安装产品根目录不包含 `skills/` 和维护面发现链接。
- 业务工作空间只生成当前 Project Skill，不出现任何维护 Skill。
- 使用隔离的假 `HOME` 执行安装和接线测试，断言所有用户级 Skill 目录保持未创建、未修改。
- Codex、Claude 的原生目录均来自各自 Manifest 的同一 `skill_target` 契约；测试 Agent 可用 `null` 明确表示不支持 Skill，公共代码不得猜测默认目录或以平台名分支选择目录。
- 不同 Agent 生成视图中的 `SKILL.md`、脚本和引用资料必须解析到同一通用事实源；可选平台展示元数据不得改变隐式调用边界或 Skill 语义。
- 固定执行 `bash internal/tests/test_runtime.sh`、`bash internal/tests/test_resources.sh`、`bash tests/test_install.sh` 和 `bash internal/tests/test_release.sh`。

本地固定测试只证明代码和接线合同。至少还要分别从源码产品根目录、一个真实业务工作空间和一个无关仓库启动 Agent，验证可见 Skill 集合符合上表；该结果不能替代 Jira、PR/CI 或发布验收。

Codex 支持仓库级原生 Skill 目录和符号链接，并会自动检测变更；若接线正确但界面尚未刷新，再重启 Codex。具体发现规则见 [OpenAI Skill 文档](https://learn.chatgpt.com/docs/build-skills)。
