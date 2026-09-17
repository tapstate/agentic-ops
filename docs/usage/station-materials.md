# 工位源码与持久材料

源码池自动位于产品根 `.local/source-pool/`，只用于工位下载加速，无需配置。完整工程由 Project Profile 选择，接管时先下载缺失的池内仓库或刷新已有缓存，再通过本地传输在 `source/<owner>/<repo>` 创建或更新独立 Git 仓库并冻结完整基线；活动修改仓另行登记。

初始化只生成空工位接线，接管时才准备选定的源码。各工位拥有完整对象和独立 Git 元数据，origin 保留真实远端，不使用 linked worktree、硬链接或 alternates。缓存丢失不影响已准备工位的本地开发；下次需要刷新时重新下载入池。下载失败保留任务操作供原请求重试，不把旧缓存视为最新远端事实。

共享同一产品根的工位复用缓存；不同来源通过规范 origin 摘要隔离，每个池内仓库在刷新和传输期间加锁。工位 purge 保留产品缓存；缓存不保存任务状态或验收证据。

## 中央共享 Wiki

`bootstrap/shared-repositories.json` 登记共享仓库来源与分支；TapData 的 Profile 通过 `wiki_repository: tapstate/wiki` 引用 `git@github.com:tapstate/wiki.git` 的 main，不另设 wiki.json。完整共享副本位于 Product Root 的 `.local/shared-repositories/tapstate/wiki`，与 bare 下载缓存分离；同一产品根的多个工位共用，不同产品根分别维护。

研发按需显式运行以下命令（脚本默认使用自身所在 Product Root，也可通过 `--product-root` 指定）：

```sh
python3 <product-root>/bootstrap/shared_repositories.py ensure --repository tapstate/wiki
python3 <product-root>/bootstrap/shared_repositories.py update --repository tapstate/wiki
python3 <product-root>/bootstrap/shared_repositories.py status --repository tapstate/wiki
```

ensure 只在缺失时借助源码池准备独立副本，已有副本仅核验；update 才联网刷新并仅快进 main；status 只检查本地状态。初始化、接管、任务开始和技能查询均不自动准备或更新。已有未知目录不会自动采用；本地修改、身份错误、分叉或未完成 Git 操作需要研发处理，不自动 reset、stash 或强制覆盖。管理并发时拒绝忙碌仓库；查询一次固定一个临时提交读取，避免混读更新前后内容，不保存任务 Wiki SHA。

准备使用临时目录核验后发布。更新失败不等于工作树仍可用：fetch 失败通常保留原 HEAD，checkout 中断必须先经 status 核验并由研发修复。管理器不自动清理共享材料、不运行 Wiki init、脚本或 Hook；不提供操作系统级只读隔离。

Wiki 不是任务工程，不进入 engineering_baseline、源码仓库版本配套或发布验收。技能需要业务源码时只读项目 source 中的任务对应工程。共享材料不属于工位 purge 范围；本次不改变 `.agenticops` 状态协议，共享材料不改变代际；现役任务重置使用 epoch 5。

## 已有源码

已有目录必须是独立真实仓库、origin 与项目目录一致、没有共享对象依赖，且接管前源码洁净。来源不符、符号链接或未知材料不会被覆盖。不能把另一个工位的 .agenticops 复制过来当新工位。

## 清理后复用

station purge 只处理空闲工位，保留 source/config/archive。原路径重新生成时，对这些非空真实目录明确使用 init --reuse-materials；runtime 必须为空。参数不授权覆盖源码、导入秘密有效配置或复用旧证据。新任务仍按自己的版本解析与验证，不把保留分支自动视作已授权任务。

旧版源码池作为非托管材料保留，不由新版本迁移或删除。旧工位的退出顺序见[更新与回退](update-and-rollback.md)。
