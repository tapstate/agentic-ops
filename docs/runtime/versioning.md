# 版本号设计

## 1. 目标

AgenticOps 的版本号必须让维护者和 AIAgent 快速识别：

- 当前运行的是源码态，还是随仓库分发的已编译安装资源。
- 这个产物属于哪个迭代版本。
- 这是当前迭代起点后的第几次提交。
- 这个产物来自哪个 Git 提交。

版本号只允许在正常发布准备时人工确定一次二段式版本基线，例如创建 annotated `v0.2` tag。后续构建必须自动生成完整版本，减少手工输入造成的错误。

## 2. 版本号格式

版本号使用运行状态、迭代版本、提交序号和提交编号组合：

```text
STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT
```

示例：

```text
INS-v0.1.3-a68372d
```

字段含义：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `STATE` | 运行状态 | `INS` |
| `MAJOR` | 大版本 | `0` |
| `ITERATION` | 迭代编号 | `1` |
| `COMMIT_INDEX` | 从迭代起始 tag 到当前 HEAD 的提交计数 | `3` |
| `COMMIT` | Git short commit | `a68372d` |

## 3. 运行状态

版本号第一段包含运行状态，同时 `agentic-cli --version` 必须输出独立的 `version_state` 字段，方便机器稳定解析。

| `version_state` | 含义 | 生成方式 |
| --- | --- | --- |
| `SRC` | 源码运行 | `go run` 或未注入构建信息的源码态 |
| `INS` | 已编译安装资源 | `scripts/build.sh` 写入 `install-resources/<os-arch>/agentic-cli` |

`agentic-cli --version` 必须输出：

```json
{
  "operation": "version",
  "ok": true,
  "version": "INS-v0.1.3-a68372d",
  "version_state": "INS",
  "iteration_version": "v0.1",
  "commit_index": 3,
  "commit": "a68372d",
  "build_time": "2026-07-22T06:23:11Z"
}
```

## 4. 生成规则

### 源码运行

源码运行默认输出：

```json
{
  "version": "SRC-source",
  "version_state": "SRC",
  "iteration_version": "source",
  "commit_index": 0,
  "commit": "unknown"
}
```

源码态用于说明当前命令不是安装产物。

### 安装资源构建

`scripts/build.sh` 自动生成 `INS` 版本。

脚本调用 `scripts/version.sh INS` 自动生成：

```text
INS-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT
```

构建不接受位置参数作为版本号，也不接受 `AGENTIC_OPS_VERSION` 指定版本号。自动化测试如需固定日期、序号或提交编号，必须使用 `AGENTIC_OPS_BUILD_TEST_MODE=1`；该模式只服务测试，不作为日常构建入口。

脚本自动读取：

- 最近的迭代 tag，例如 `v0.1`。
- 从迭代 tag 到当前 HEAD 的提交计数，作为 `COMMIT_INDEX`。
- 当前 Git short commit 作为 `COMMIT`。

`COMMIT_INDEX` 沿用现有分支无关的提交计数算法。正常开发、合并提交和 Hotfix 可能使编号出现跳跃，允许跳号，不通过修改提交策略来追求连续编号。

如果当前仓库没有 `vMAJOR.ITERATION` 格式的迭代 tag，`scripts/version.sh` 和 `scripts/build.sh` 必须失败，并提示先创建迭代 tag，例如：

```sh
git tag -a v0.1 -m "AgenticOps v0.1 version baseline"
```

## 5. 设计考虑

### 迭代可读性

`v0.1` 表示当前迭代，维护者不需要从日期反推业务阶段。大版本和迭代号只在正常发布时人工确定一次，后续不再手工输入。远端 tag 不得移动或覆盖。

### Hotfix 版本

Hotfix 从最新 `origin/main` 创建修复分支，复用 `main` 历史中最近的二段式 annotated tag。修复构建继续自动生成 `STATE-vX.Y.COMMIT_INDEX-COMMIT`，不创建补丁位、不创建新 tag，也不修改 `STATE` 含义。合并提交导致的 `COMMIT_INDEX` 跳跃属于允许结果。

### 可追溯性

Git short commit 放在最后一段，任何已编译安装资源都能反查源码提交点。

### 防人为错误

构建不允许手工指定完整版本号。维护者只在迭代开始时打 tag，构建时确认脚本生成结果，不能覆盖它。

### 与 latest-only 策略一致

AgenticOps 不维护旧版本补丁线。用户只安装 latest；回滚通过 `~/.agentic-ops/.local/previous-ref` 和 Git commit 控制，不通过多版本安装目录控制。

## 6. 验证

版本号规则由以下命令验证：

```sh
bash scripts/test-build.sh
```

该测试覆盖：

- `scripts/version.sh` 自动生成 `STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT`。
- `scripts/build.sh` 生成当前平台 `INS` 产物。
- `install-resources/checksums.txt` 包含通用资源和平台二进制。
- `agentic-cli --version` 输出 `version_state`、`iteration_version`、`commit_index`、`commit` 和 `build_time`。
- `scripts/build.sh` 拒绝手工指定完整版本。
