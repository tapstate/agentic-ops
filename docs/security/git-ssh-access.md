# Git SSH 授权指引

本指引用于 GitHub 的 `clone`、`fetch` 和工作分支 `push`。SSH 只认证 Git 传输，
不替代 AgenticOps 任务授权、Gate、Rulesets 或 GitHub MCP / `gh` 的 OAuth 或 PAT。

## 1. 前提

- 每位研发员、每台设备使用独立、带口令的 Ed25519 密钥；私钥绝不离开设备。
- 仓库管理员先为 GitHub 账号授予所需仓库角色。公钥不授予仓库权限。
- 组织启用 SAML SSO 时，在 GitHub 的 SSH key 管理页对组织执行 `Configure SSO` /
  `Authorize`。

## 2. 配置密钥

先确认不会覆盖已有密钥：

```sh
ls -la ~/.ssh
ssh-add -l
```

创建专用密钥。`<key-name>` 需能识别设备和用途，例如
`id_ed25519_github_work_laptop`：

```sh
ssh-keygen -t ed25519 -C "<GitHub 邮箱>" -f ~/.ssh/<key-name>
chmod 700 ~/.ssh
chmod 600 ~/.ssh/<key-name>
chmod 644 ~/.ssh/<key-name>.pub
```

把私钥交给本机 `ssh-agent`：

```sh
# macOS
ssh-add --apple-use-keychain ~/.ssh/<key-name>

# Linux
ssh-add ~/.ssh/<key-name>
```

若 agent 未运行，先执行 `eval "$(ssh-agent -s)"`。macOS 可在 `~/.ssh/config` 加入：

```sshconfig
Host github.com
  AddKeysToAgent yes
  UseKeychain yes
  IdentityFile ~/.ssh/<key-name>
  IdentitiesOnly yes
```

Linux 保留 `IdentityFile` 和 `IdentitiesOnly` 即可。

## 3. 授权与验证

将 `~/.ssh/<key-name>.pub` 的完整单行内容添加到 GitHub：[Settings → SSH and GPG keys → New SSH key](https://github.com/settings/ssh/new)，用途选择 Authentication。只能上传 `.pub`
公钥，绝不可复制私钥。

依次验证身份和目标仓库读取权限：

```sh
ssh -T git@github.com
git -C <仓库目录> remote -v
git -C <仓库目录> ls-remote origin HEAD
```

第一条出现自己的 GitHub 用户名即为认证成功；退出码 `1` 是 GitHub 的预期行为。后两条
确认 remote 使用 SSH URL，且当前账号能读取目标仓库。写权限只在已获任务授权的工作分支
按正常流程验证，不以测试名义写入远端。

## 4. 排障与撤销

- `Permission denied (publickey)`：检查 `ssh-add -l`、`ssh -G github.com` 的
  `identityfile`、GitHub 公钥和 `git remote -v`。必要时用 `ssh -vT git@github.com`
  排障，但不要将输出中的本地路径、用户名或代理信息写入证据。
- 身份认证成功但仓库操作被拒：检查仓库角色、组织 SSO 和分支 Ruleset；不得借用他人密钥。
- 网络超时或代理失败：这是网络路径问题，不是 SSH 授权失败。检查 `ProxyCommand` 与实际
  代理监听状态；不得绕过网络策略或全局启用 `ForwardAgent yes`。
- 设备遗失、离岗或疑似泄露：立即在 GitHub 删除对应公钥，并从设备的 `ssh-agent` 与
  Keychain 移除私钥；生成新密钥后重新验证。

官方说明：[SSH 认证](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/about-ssh)、[生成与加载密钥](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent)、[连接测试](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection)。
