# 版本号设计

## 1. 目标

AgenticOps 的版本号必须让维护者和 AIAgent 快速识别：

- 当前运行的是源码、开发编译产物，还是正式 release 包。
- 这个产物属于哪个迭代版本。
- 这是当前迭代起点后的第几次提交。
- 这个产物来自哪个 Git 提交。

版本号只允许在迭代开始时人工确定一次，例如打上 `v0.1` tag。后续编译和发版必须自动生成完整版本，减少发版时手工输入造成的错误。

迭代 tag 只接受 `vMAJOR.ITERATION` 格式，例如 `v0.1`。`v0.1.2` 这类三段 tag 不作为迭代起点。

## 2. 版本号格式

版本号使用运行状态、迭代版本、提交序号和提交编号组合：

```text
STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT
```

示例：

```text
RES-v0.1.3-a68372d
```

字段含义：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `STATE` | 运行状态 | `RES` |
| `MAJOR` | 大版本 | `0` |
| `ITERATION` | 迭代编号 | `1` |
| `COMMIT_INDEX` | 从迭代起始 tag 到当前 HEAD 的提交计数 | `3` |
| `COMMIT` | Git short commit | `a68372d` |

## 3. 运行状态

版本号第一段包含运行状态，同时 `agent-task-ops --version` 仍必须输出独立的 `version_state` 字段，方便机器稳定解析。

| `version_state` | 含义 | 生成方式 |
| --- | --- | --- |
| `SRC` | 源码运行 | `go run` 或未注入构建信息的源码态 |
| `DEV` | 开发编译版 | `scripts/build.sh` |
| `RES` | 正式 release 包 | `scripts/release.sh` |

`agent-task-ops --version` 必须输出：

```json
{
  "operation": "version",
  "ok": true,
  "version": "RES-v0.1.3-a68372d",
  "version_state": "RES",
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

源码态用于说明当前命令不是安装产物，也不是正式 release 包。

### 开发编译

`scripts/build.sh` 自动生成 `DEV` 版本。

脚本调用 `scripts/version.sh DEV` 自动生成：

```text
DEV-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT
```

开发编译不接受位置参数作为版本号，也不接受 `AGENTIC_OPS_VERSION` 指定版本号。自动化测试如需固定日期、序号或提交编号，必须使用 `AGENTIC_OPS_BUILD_TEST_MODE=1`；该模式只服务测试，不作为日常开发入口。

### 正式发版

`scripts/release.sh` 生成 `RES` 版本。

正式发版不允许手工指定版本号：

- 不接受位置参数作为版本号。
- 不接受 `AGENTIC_OPS_VERSION` 指定版本号。
- 不允许在确认提示中覆盖自动生成的版本号。
- 不允许通过普通环境变量覆盖迭代版本、提交序号或提交编号。

脚本自动读取：

- 最近的迭代 tag，例如 `v0.1`。
- 从迭代 tag 到当前 HEAD 的提交计数，作为 `COMMIT_INDEX`。
- 当前 Git short commit 作为 `COMMIT`。

release version 和 asset version 使用同一个值，避免二进制版本和资产版本被手工填错。

自动化测试可以使用 `AGENTIC_OPS_RELEASE_TEST_MODE=1` 固定迭代版本、提交序号和提交编号，保证测试可重复。该模式只服务测试，不作为日常发版入口。

如果当前仓库没有 `vMAJOR.ITERATION` 格式的迭代 tag，`scripts/version.sh`、`scripts/build.sh` 和 `scripts/release.sh` 必须失败，并提示先创建迭代 tag，例如：

```sh
git tag v0.1
```

## 5. 设计考虑

### 迭代可读性

`v0.1` 表示当前迭代，维护者不需要从日期反推业务阶段。大版本和迭代号只在迭代开始时人工确定一次，后续不再手工输入。

### 可追溯性

Git short commit 放在最后一段，任何 release 包都能反查源码提交点。

### 防人为错误

build 和 release 都不允许手工指定完整版本号。维护者只在迭代开始时打 tag，发版时确认脚本生成结果，不能覆盖它。

资产版本号跟随 release version，不再单独输入，避免二进制和资产包版本错配。

### 版本顺序

`COMMIT_INDEX` 表示当前迭代起点后的第几次提交。只要提交向前推进，版本顺序自然递增；同一个 commit 重复打包会得到相同版本号，说明源码点没有变化。

### 状态双写

`SRC`、`DEV`、`RES` 既出现在版本号第一段，也通过 `version_state` 输出。前者方便人读，后者方便机器解析和日志分析。

### 与 latest-only 策略一致

AgenticOps 不维护旧版本补丁线。BUG 只在最新版本修复，有新版本时推荐自动更新应用。版本号用于识别当前产物，不用于维护多个长期修复分支。

## 6. 验证

版本号规则由以下命令验证：

```sh
bash scripts/test-build-release.sh
```

该测试覆盖：

- `scripts/version.sh` 自动生成 `STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT`。
- `scripts/build.sh` 生成 `DEV` 产物。
- `scripts/release.sh` 生成 `RES` 产物。
- 临时 Git 仓库中从 `v0.1` tag 自动计算提交计数。
- 没有迭代 tag 时版本生成失败并提示创建 tag。
- `agent-task-ops --version` 输出 `version_state`、`iteration_version`、`commit_index`、`commit` 和 `build_time`。
- `scripts/build.sh` 和 `scripts/release.sh` 拒绝手工指定完整版本。
- release manifest 写入 `version_state=RES`、`iteration_version` 和 `commit_index`。
