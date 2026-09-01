# Git SSH 安装

这是默认安装方式。已配置 Git SSH 且账号能读取 `tapstate/agentic-ops` 时，先按[Git SSH 授权指引](../security/git-ssh-access.md)验证身份与仓库访问，再执行以下命令。

```sh
(
  set -euo pipefail
  ao_install_root="$HOME/.agentic-ops"
  test ! -e "$ao_install_root" || {
    printf '安装目录已存在：%s；请使用 agenticops update 更新\n' "$ao_install_root" >&2
    exit 2
  }
  git clone --filter=blob:none --no-checkout --branch main --single-branch \
    git@github.com:tapstate/agentic-ops.git "$ao_install_root"
  git -C "$ao_install_root" sparse-checkout init --cone
  git -C "$ao_install_root" sparse-checkout set \
    adapters bootstrap contracts gate policies projects workflow
  git -C "$ao_install_root" checkout main
  ao_current_ref="$(git -C "$ao_install_root" rev-parse HEAD)"
  python3 "$ao_install_root/bootstrap/product_state.py" \
    --product-root "$ao_install_root" write \
    --mode installed --repository git@github.com:tapstate/agentic-ops.git \
    --branch main --current-ref "$ao_current_ref"
  python3 "$ao_install_root/bootstrap/repository_pool.py" \
    --product-root "$ao_install_root" configure
)
```

这会使用同一组默认值：安装到 `~/.agentic-ops`、默认 Source Pool 为 `~/.agentic-ops-repos`、供给模式为 `auto-clone`。需要指定 Pool 或改为手动供给时，先阅读[自定义 Source Pool](custom-source-pool.md)。
