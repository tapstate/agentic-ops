# gh 一键安装

本方案面向已安装 GitHub CLI 的使用者。需要 Git、Python 3.9+，并且登录账号能读取 `tapstate/agentic-ops`。

先检查登录：

```sh
gh auth status -h github.com
```

未登录时执行：

```sh
gh auth login --hostname github.com --git-protocol ssh --skip-ssh-key --scopes repo
```

登录后执行默认安装；不要附加选项：

```sh
(
  set -euo pipefail
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash
)
```

它从受信的 `main` 分支安装到 `~/.agentic-ops`，默认 Source Pool 为 `~/.agentic-ops-repos`，供给模式为 `auto-clone`。接管任务时会按项目仓库目录受控下载缺失仓库。安装目录已存在时不会覆盖，请使用[更新与回退](update-and-rollback.md)。

`gh api` 被拒绝时，检查账号是否有仓库读取权限及 `repo` scope；没有 `gh` 时使用[Git SSH 安装](git-ssh-install.md)。Git SSH 的配置、验证和撤销见[Git SSH 授权指引](../security/git-ssh-access.md)。
