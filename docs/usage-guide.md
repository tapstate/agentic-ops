# AgenticOps 首次使用指引

这篇只带你完成一条默认路径：安装 AgenticOps、创建工作空间并接管第一个 Jira 任务。默认值已由产品配置；除示例中的工作空间路径和任务号外，不需要先理解或填写其它选项。

开始前准备：Git、Python 3.9+，并确保 Git SSH 已获得 `tapstate/agentic-ops` 的读取权限。不熟悉术语时查看[术语表](glossary.md)。

## 1. 安装

按[Git SSH 授权指引](security/git-ssh-access.md)确认访问权限后，执行以下命令。它使用默认安装位置、发布分支、Source Pool 和仓库供给方式：

```sh
(
  set -euo pipefail

  ao_home="$HOME/.agentic-ops"
  test ! -e "$ao_home" || {
    printf '安装目录已存在：%s；请使用 agenticops update 更新\n' "$ao_home" >&2
    exit 2
  }

  git clone --filter=blob:none --no-checkout \
    --branch main --single-branch \
    git@github.com:tapstate/agentic-ops.git "$ao_home"

  git -C "$ao_home" sparse-checkout init --cone
  git -C "$ao_home" sparse-checkout set \
    adapters bootstrap contracts gate policies projects workflow
  git -C "$ao_home" checkout main

  ao_ref="$(git -C "$ao_home" rev-parse HEAD)"
  python3 "$ao_home/bootstrap/product_state.py" \
    --product-root "$ao_home" write \
    --mode installed \
    --repository git@github.com:tapstate/agentic-ops.git \
    --branch main \
    --current-ref "$ao_ref"

  python3 "$ao_home/bootstrap/repository_pool.py" \
    --product-root "$ao_home" configure
)
```

成功后，产品安装在 `~/.agentic-ops`。默认 Source Pool 是 `~/.agentic-ops-repos`，使用 `auto-clone` 供给模式；无需把这些默认值写进命令。接管任务时，Agent 会按项目仓库目录受控下载缺失的业务仓库。

## 2. 创建项目工作空间

工作空间放在业务代码之外。以下示例为默认项目创建 `~/agenticops-tapdata`；只需要把路径换成你的实际位置：

```sh
~/.agentic-ops/agenticops init \
  --workspace "$HOME/agenticops-tapdata"
```

不传 `--agent` 时会接入全部可用 Agent，不传 `--repository-pool` 时会继承安装时的默认池。接着检查接线：

```sh
~/.agentic-ops/agenticops doctor --workspace "$HOME/agenticops-tapdata"
```

## 3. 启动 Agent

进入工作空间并启动你要使用的 Agent。使用 Codex：

```sh
cd "$HOME/agenticops-tapdata"
./agenticops start codex
```

首次启动 Codex 时，按 `/hooks` 的提示审核并信任本项目生成的 Hook。使用 Claude Code 时，将最后一行替换为 `./agenticops start claude`。

## 4. 接管第一个任务

在同一 Agent 会话中直接发送下面这句话，把 `TAP-123` 换成实际 Jira 任务号：

```text
接管 TAP-123。
```

Agent 会先读取 Jira 和项目准入规则，登记仓库并准备本地任务 worktree，然后给出方案。方案、风险和实现授权需要你确认；事实、权限或门禁不明确时，它会停下并说明下一步。接管不是自动提交、推送或合并：这些副作用仍受明确授权和 Gate 约束。

## 接下来可能需要

- [Git SSH 安装](usage/git-ssh-install.md)：默认安装命令的独立说明。
- [gh 一键安装](usage/gh-one-click-install.md)：无法使用 Git SSH 时的备用安装方式。
- [自定义 Source Pool](usage/custom-source-pool.md)：复用现有业务仓库或改为手动供给。
- [更新与回退](usage/update-and-rollback.md)：更新安装、修复工作空间接线或回退一次更新。
- [常见问题](usage/faq.md)：安装失败、Hook、任务恢复和本地清理。

维护 AgenticOps 源码本身，请改看[维护指引](maintenance-guide.md)，不要把源码仓库当作业务使用工作空间。
