# Git SSH 安装

这是默认安装方式。需要 Git、Python 3.12.x，且账号能读取 `tapstate/agentic-ops`；先按[Git SSH 授权指引](../security/git-ssh-access.md)验证身份与仓库访问。

## 首次安装

通过 Git SSH 获取发布分支的安装脚本，再由脚本完成产品安装、安装状态写入与工位登记初始化。临时目录只用于获取脚本，执行结束后删除：

```sh
(
  set -euo pipefail
  ao_install="$(mktemp -d)"
  trap 'rm -rf "$ao_install"' EXIT
  git clone --depth 1 --filter=blob:none --no-checkout \
    --branch main --single-branch \
    git@github.com:tapstate/agentic-ops.git "$ao_install/source"
  git -C "$ao_install/source" show HEAD:bootstrap/install.sh > "$ao_install/install.sh"
  bash "$ao_install/install.sh"
)
```

脚本默认仓库是 `git@github.com:tapstate/agentic-ops.git`，默认分支是 `main`，默认源码池是 `~/.agentic-ops-repos`；安装目录默认是 `~/.agentic-ops`，设置 `AGENTIC_OPS_HOME` 时使用该值。正常安装无需指定 `--repository`。安装目录已存在时脚本会停止，请使用[更新与回退](update-and-rollback.md)。

## 自定义安装

已有 AgenticOps 产品副本时，可以直接使用其安装入口。以下沿用你已设置的产品副本、安装目录、源码池和分支变量：

```sh
"$ao_install/agenticops" install \
  --install-home "$ao_home" \
  --source-pool "$ao_repos" \
  --branch "$ao_branch"
```

上述 `$ao_install` 是包含 `agenticops` 入口的产品副本路径，与首次安装命令中的临时目录用途不同。查看全部选项：`"$ao_install/agenticops" install --help`。使用上方临时安装脚本时，也可在 `bash "$ao_install/install.sh"` 后追加这些选项。

仅在从其它仓库或本地测试仓库安装时使用 `--repository <Git URL 或本地路径>` 覆盖来源。显式参数优先于默认值，安装时记录的仓库与分支用于后续更新；不要把业务仓库作为产品安装来源。

安装完成后，按[首次使用指引](../usage-guide.md#2-创建项目工位)创建工位。完整工程在任务接管时于工位 source 中准备；源码池仅用于下载加速，持久材料复用见[工位源码与材料](station-materials.md)。

## 安装与初始化输出

安装与工位初始化按阶段报告进度，例如 `[AgenticOps][安装][进行] 下载产品（分支=main）`；完成后只给一次摘要，列出目录、项目或通道、Agent、产品提交及下一步。阶段与警告写 stderr，成功摘要写 stdout；直接在终端运行时会依次看到两者。

Git 的普通克隆与检出明细静默处理，警告和错误保留。失败时会标出失败阶段及退出码，不显示完成；例如下载失败时仍可看到 Git 的权限或网络诊断。初始化缺少 `git_name` 时仍给出需要补充身份的警告。此输出遵循[脚本控制台输出规范](../maintenance-guide.md#脚本控制台输出规范)，不代表已完成 doctor 或真实业务环境验证。
