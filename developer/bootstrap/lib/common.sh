#!/usr/bin/env bash

set -euo pipefail

agentic_json_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

agentic_bootstrap_json_error() {
  local code="$1"
  local message="$2"
  local action="$3"
  printf '{"ok":false,"operation":"bootstrap","status":"failed","code":"%s","retry_safe":true,"message":"%s","required_human_action":"%s"}\n' \
    "$(agentic_json_escape "$code")" \
    "$(agentic_json_escape "$message")" \
    "$(agentic_json_escape "$action")"
}

agentic_bootstrap_json_success() {
  local operation="$1"
  shift
  printf '{"ok":true,"operation":"%s","status":"completed","retry_safe":true' \
    "$(agentic_json_escape "$operation")"
  while [ "$#" -gt 0 ]; do
    printf ',"%s":"%s"' \
      "$(agentic_json_escape "$1")" \
      "$(agentic_json_escape "$2")"
    shift 2
  done
  printf '}\n'
}

agentic_bootstrap_error() {
  local code="$1"
  local message="$2"
  local action="$3"

  printf 'AgenticOps：%s\n' "$message" >&2
  agentic_bootstrap_json_error "$code" "$message" "$action"
  exit 1
}

agentic_find_uv() {
  if [ -n "${AGENTIC_OPS_UV:-}" ] && [ -x "$AGENTIC_OPS_UV" ]; then
    printf '%s\n' "$AGENTIC_OPS_UV"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi
  agentic_bootstrap_error \
    "uv_not_found" \
    "未找到 uv，无法准备锁定的 Python 3.12 Runtime" \
    "请先安装 uv，或通过 AGENTIC_OPS_UV 指向可信的 uv 可执行文件"
}

agentic_reject_identity_overrides() {
  local variable_name=""
  for variable_name in \
    AGENTIC_OPS_TEST_MODE \
    AGENTIC_OPS_TEST_LAUNCHER \
    AGENTIC_OPS_TEST_EXPECTED_REPOSITORY \
    AGENTIC_OPS_REPO_URL \
    AGENTIC_OPS_GITHUB_REPOSITORY \
    AGENTIC_OPS_BRANCH; do
    if [ -n "${!variable_name:-}" ]; then
      agentic_bootstrap_error \
        "install_identity_override_forbidden" \
        "AgenticOps 安装身份固定为 tapstate/agentic-ops 的 main，不能通过环境变量覆盖：$variable_name" \
        "请移除安装身份覆盖环境变量后重试"
    fi
  done
}

agentic_reject_verification_mode() {
  local install_dir="$1"
  if [ -f "$install_dir/.agentic-ops/verification-only" ]; then
    agentic_bootstrap_error \
      "verification_only_install_forbidden" \
      "检测到 verification-only 安装标记，当前安装目录不得执行生产维护动作" \
      "请在此工作目录运行 verify 模式专用流程，不要在生产命令中指定此目录"
  fi
}

agentic_expected_repository() {
  printf '%s\n' "tapstate/agentic-ops"
}

agentic_repository_matches() {
  local remote="$1"
  local expected="$2"
  local normalized_remote="${remote%/}"
  local normalized_expected="${expected%/}"

  normalized_remote="${normalized_remote%.git}"
  normalized_expected="${normalized_expected%.git}"
  if [ "$normalized_expected" != "tapstate/agentic-ops" ]; then
    return 1
  fi
  case "$normalized_remote" in
    git@github.com:tapstate/agentic-ops|\
    ssh://git@github.com/tapstate/agentic-ops|\
    https://github.com/tapstate/agentic-ops)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

agentic_normalize_repository_url() {
  local value="${1%/}"
  printf '%s\n' "${value%.git}"
}

agentic_require_unrewritten_url() {
  local raw_url="$1"
  local operation="${2:-安装}"
  local effective_url=""
  local normalized_raw=""
  local normalized_effective=""

  effective_url="$(git ls-remote --get-url "$raw_url" 2>/dev/null || true)"
  normalized_raw="$(agentic_normalize_repository_url "$raw_url")"
  normalized_effective="$(agentic_normalize_repository_url "$effective_url")"
  if [ -z "$effective_url" ] || \
    ! agentic_repository_matches "$effective_url" "$(agentic_expected_repository)" || \
    [ "$normalized_raw" != "$normalized_effective" ]; then
    agentic_bootstrap_error \
      "install_transport_rewrite_forbidden" \
      "AgenticOps ${operation}地址被 Git url.*.insteadOf 或其它配置改写：${effective_url:-无法解析}" \
      "请移除 Git URL 重写，确保实际传输地址与官方仓库完全一致"
  fi
}

agentic_require_managed_clone() {
  local install_dir="$1"
  local origin=""
  local effective_origin=""
  local effective_push=""
  local expected=""
  local origin_count="0"
  local effective_count="0"
  local push_count="0"
  agentic_reject_identity_overrides
  if [ ! -e "$install_dir/.git" ] || \
    ! git -C "$install_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    agentic_bootstrap_error \
      "managed_clone_required" \
      "目标目录不是 AgenticOps managed clone：$install_dir" \
      "请重新执行 developer/bootstrap/install.sh 安装"
  fi
  if [ -e "$install_dir/.agentic-ops-source" ] || [ -d "$install_dir/maintainer" ]; then
    agentic_bootstrap_error \
      "source_install_conflict" \
      "不能把 AgenticOps 源头仓库或含 maintainer 资产的目录作为 developer 安装" \
      "请使用独立的 ~/.agentic-ops 安装目录"
  fi
  expected="$(agentic_expected_repository)"
  origin="$(git -C "$install_dir" config --get-all remote.origin.url 2>/dev/null || true)"
  origin_count="$(printf '%s\n' "$origin" | sed '/^$/d' | wc -l | tr -d ' ')"
  effective_origin="$(git -C "$install_dir" remote get-url --all origin 2>/dev/null || true)"
  effective_count="$(printf '%s\n' "$effective_origin" | sed '/^$/d' | wc -l | tr -d ' ')"
  effective_push="$(git -C "$install_dir" remote get-url --push --all origin 2>/dev/null || true)"
  push_count="$(printf '%s\n' "$effective_push" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$origin_count" != "1" ] || [ "$effective_count" != "1" ] || \
    [ "$push_count" != "1" ] || \
    ! agentic_repository_matches "$origin" "$expected"; then
    agentic_bootstrap_error \
      "install_origin_mismatch" \
      "AgenticOps managed clone origin 不是受信仓库：${origin:-未配置}" \
      "请重新安装并确保 origin 指向 $expected"
  fi
  if ! agentic_repository_matches "$effective_origin" "$expected" || \
    ! agentic_repository_matches "$effective_push" "$expected" || \
    [ "$(agentic_normalize_repository_url "$origin")" != \
      "$(agentic_normalize_repository_url "$effective_origin")" ] || \
    [ "$(agentic_normalize_repository_url "$origin")" != \
      "$(agentic_normalize_repository_url "$effective_push")" ]; then
    agentic_bootstrap_error \
      "install_transport_rewrite_forbidden" \
      "AgenticOps managed clone 的实际 fetch 或 push 地址被 Git 配置改写" \
      "请移除 url.*.insteadOf、pushInsteadOf 或 remote pushurl 后重新安装"
  fi
}

agentic_managed_path_error() {
  local label="$1"
  agentic_bootstrap_error \
    "install_managed_path_invalid" \
    "AgenticOps 安装受管路径不是安装根内的安全普通路径：$label" \
    "请停止使用该目录；移除符号链接或特殊文件后重新安装"
}

agentic_ref_path_error() {
  local label="$1"
  agentic_bootstrap_error \
    "install_ref_path_invalid" \
    "AgenticOps 本地 ref 状态不是安装根内的安全普通文件：$label" \
    "请停止使用该目录并重新安装"
}

agentic_require_directory_slot() {
  local path="$1"
  local label="$2"
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
    agentic_managed_path_error "$label"
  fi
}

agentic_require_file_slot() {
  local path="$1"
  local label="$2"
  local kind="${3:-managed}"
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -f "$path" ]; }; then
    if [ "$kind" = "ref" ]; then
      agentic_ref_path_error "$label"
    fi
    agentic_managed_path_error "$label"
  fi
}

agentic_require_managed_paths_safe() {
  local install_dir="$1"
  local ref_name=""

  if [ -L "$install_dir" ] || [ ! -d "$install_dir" ]; then
    agentic_managed_path_error "install_root"
  fi
  agentic_require_directory_slot "$install_dir/.local" ".local"
  agentic_require_directory_slot "$install_dir/bin" "bin"
  agentic_require_directory_slot "$install_dir/developer" "developer"
  agentic_require_directory_slot "$install_dir/developer/.venv" "developer/.venv"
  agentic_require_file_slot "$install_dir/bin/ao-work" "bin/ao-work"
  for ref_name in previous-ref current-ref pending-rollback-ref; do
    agentic_require_file_slot "$install_dir/.local/$ref_name" ".local/$ref_name" ref
  done
  agentic_require_file_slot \
    "$install_dir/.local/installation.json" \
    ".local/installation.json"
}

agentic_validate_ref_value() {
  local value="$1"
  local label="$2"
  if [[ ! "$value" =~ ^[0-9a-f]{40}$ ]] && \
    [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
    agentic_bootstrap_error \
      "install_ref_integrity_invalid" \
      "AgenticOps 本地 ref 不是完整 commit 摘要：$label" \
      "请停止使用该目录，并通过正式 Bootstrap 重新安装"
  fi
}

agentic_write_ref_atomic() {
  local install_dir="$1"
  local ref_name="$2"
  local ref_value="$3"
  local local_dir="$install_dir/.local"
  local target=""
  local temporary=""

  case "$ref_name" in
    previous-ref|current-ref|pending-rollback-ref) ;;
    *) agentic_ref_path_error "$ref_name" ;;
  esac
  agentic_validate_ref_value "$ref_value" "$ref_name"
  agentic_require_managed_paths_safe "$install_dir"
  if [ ! -e "$local_dir" ]; then
    if ! mkdir -m 0700 "$local_dir"; then
      agentic_ref_path_error ".local"
    fi
  fi
  agentic_require_managed_paths_safe "$install_dir"
  target="$local_dir/$ref_name"
  temporary="$(mktemp "$local_dir/.${ref_name}.tmp.XXXXXX")" || \
    agentic_ref_path_error "$ref_name"
  chmod 0600 "$temporary" || {
    rm -f "$temporary"
    agentic_ref_path_error "$ref_name"
  }
  if ! printf '%s\n' "$ref_value" > "$temporary"; then
    rm -f "$temporary"
    agentic_ref_path_error "$ref_name"
  fi
  agentic_require_managed_paths_safe "$install_dir"
  if ! mv -f "$temporary" "$target"; then
    rm -f "$temporary"
    agentic_ref_path_error "$ref_name"
  fi
}

agentic_remove_ref() {
  local install_dir="$1"
  local ref_name="$2"
  case "$ref_name" in
    previous-ref|current-ref|pending-rollback-ref) ;;
    *) agentic_ref_path_error "$ref_name" ;;
  esac
  agentic_require_managed_paths_safe "$install_dir"
  rm -f "$install_dir/.local/$ref_name"
}

agentic_write_installation_metadata() {
  local install_dir="$1"
  local local_dir="$install_dir/.local"
  local target="$local_dir/installation.json"
  local temporary=""
  local installed_at=""

  agentic_require_managed_paths_safe "$install_dir"
  if [ -e "$target" ]; then
    agentic_bootstrap_error \
      "install_metadata_exists" \
      "AgenticOps 安装时间元数据已经存在，Bootstrap 不会覆盖它" \
      "请停止当前安装并保留现有受管元数据"
  fi
  installed_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')" || \
    agentic_bootstrap_error \
      "install_metadata_time_unavailable" \
      "无法读取 UTC 安装时间" \
      "请检查系统时间后重新执行首次安装"
  temporary="$(mktemp "$local_dir/.installation.json.tmp.XXXXXX")" || \
    agentic_bootstrap_error \
      "install_metadata_write_failed" \
      "无法创建安装时间元数据" \
      "请检查安装目录权限后重新执行首次安装"
  chmod 0600 "$temporary" || {
    rm -f "$temporary"
    agentic_bootstrap_error \
      "install_metadata_write_failed" \
      "无法保护安装时间元数据权限" \
      "请检查安装目录权限后重新执行首次安装"
  }
  if ! printf '{"schema_version":1,"installed_at":"%s"}\n' "$installed_at" > "$temporary"; then
    rm -f "$temporary"
    agentic_bootstrap_error \
      "install_metadata_write_failed" \
      "无法写入安装时间元数据" \
      "请检查安装目录权限后重新执行首次安装"
  fi
  agentic_require_managed_paths_safe "$install_dir"
  if ! mv "$temporary" "$target"; then
    rm -f "$temporary"
    agentic_bootstrap_error \
      "install_metadata_write_failed" \
      "无法完成安装时间元数据写入" \
      "请检查安装目录权限后重新执行首次安装"
  fi
}

agentic_validate_shared_source_tree() {
  local install_dir="$1"
  local ref="$2"
  local entry=""
  local metadata=""
  local metadata_rest=""
  local object_mode=""
  local object_type=""
  local object_id=""
  local path=""
  local entry_count=0
  local source_readme=0
  local integration_readme=0
  local event_schema=0
  local manifest_schema=0
  local result_schema=0
  local comment_template=0

  if ! git -C "$install_dir" cat-file -e "$ref^{tree}" 2>/dev/null; then
    agentic_bootstrap_error \
      "developer_shared_source_invalid" \
      "无法读取 AgenticOps 提交中的 shared 资产树：$ref" \
      "请停止使用该版本，并由项目维护者核对正式提交"
  fi

  while IFS= read -r -d '' entry; do
    case "$entry" in
      *$'\t'*) ;;
      *)
        agentic_bootstrap_error \
          "developer_shared_source_invalid" \
          "AgenticOps 提交中的 shared 资产记录无法安全解析" \
          "请停止使用该版本，并由项目维护者移除非准入资产"
        ;;
    esac
    metadata="${entry%%$'\t'*}"
    path="${entry#*$'\t'}"
    object_mode="${metadata%% *}"
    metadata_rest="${metadata#* }"
    object_type="${metadata_rest%% *}"
    object_id="${metadata_rest#* }"
    if [ "$metadata" = "$object_mode" ] || \
      [ "$metadata_rest" = "$object_type" ] || \
      [ -z "$object_id" ] || [[ "$object_id" == *" "* ]] || \
      [ "$object_mode" != "100644" ] || [ "$object_type" != "blob" ]; then
      agentic_bootstrap_error \
        "developer_shared_source_invalid" \
        "AgenticOps 提交中的 shared 资产包含不安全文件类型或可执行权限：$path" \
        "请停止使用该版本，并由项目维护者移除符号链接、特殊文件或可执行位"
    fi
    case "$path" in
      shared/README.md) source_readme=$((source_readme + 1)) ;;
      shared/integration/README.md) integration_readme=$((integration_readme + 1)) ;;
      shared/integration/task-to-pr-event.schema.json) event_schema=$((event_schema + 1)) ;;
      shared/integration/task-to-pr-manifest.schema.json) manifest_schema=$((manifest_schema + 1)) ;;
      shared/integration/task-to-pr-result.schema.json) result_schema=$((result_schema + 1)) ;;
      shared/standards/jira-comment-template.schema.json) comment_template=$((comment_template + 1)) ;;
      *)
        agentic_bootstrap_error \
          "developer_shared_source_invalid" \
          "AgenticOps 提交中的 shared 资产超出固定只读协议白名单：$path" \
          "请停止使用该版本，并由项目维护者移除非准入路径、脚本或 AI 入口"
        ;;
    esac
    entry_count=$((entry_count + 1))
  done < <(git -C "$install_dir" ls-tree -r -z "$ref" -- shared 2>/dev/null)

  if [ "$entry_count" != "6" ] || \
    [ "$source_readme" != "1" ] || [ "$integration_readme" != "1" ] || \
    [ "$event_schema" != "1" ] || [ "$manifest_schema" != "1" ] || \
    [ "$result_schema" != "1" ] || [ "$comment_template" != "1" ]; then
    agentic_bootstrap_error \
      "developer_shared_source_invalid" \
      "AgenticOps 提交中的 shared 资产不等于固定只读协议白名单" \
      "请停止使用该版本，并由项目维护者补齐或移除 shared 资产"
  fi
}

agentic_shared_file_is_safe() {
  local path="$1"
  local permissions=""

  if [ -L "$path" ] || [ ! -f "$path" ]; then
    return 1
  fi
  if permissions="$(stat -f '%Sp' "$path" 2>/dev/null)"; then
    :
  elif permissions="$(stat -c '%A' "$path" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  case "$permissions" in
    *[xXsStT]*) return 1 ;;
    *) return 0 ;;
  esac
}

agentic_validate_shared_distribution() {
  local install_dir="$1"
  local shared_dir="$install_dir/shared"
  local entry=""
  local relative=""
  local entry_count=0
  local integration_dir=0
  local integration_readme=0
  local event_schema=0
  local manifest_schema=0
  local result_schema=0
  local standards_dir=0
  local comment_template=0

  if [ -L "$shared_dir" ] || [ ! -d "$shared_dir" ]; then
    agentic_bootstrap_error \
      "developer_shared_distribution_invalid" \
      "AgenticOps developer 安装缺少安全的 shared/integration 目录" \
      "请停止使用该安装目录并通过 developer/bootstrap/install.sh 重新安装"
  fi

  while IFS= read -r -d '' entry; do
    relative="${entry#"$shared_dir"/}"
    if [ -L "$entry" ]; then
      agentic_bootstrap_error \
        "developer_shared_distribution_invalid" \
        "AgenticOps developer 安装的 shared 可见树包含符号链接：$relative" \
        "请停止使用该安装目录并通过 developer/bootstrap/install.sh 重新安装"
    fi
    case "$relative" in
      integration)
        [ -d "$entry" ] || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared/integration 不是普通目录" \
          "请停止使用该安装目录并重新安装"
        integration_dir=$((integration_dir + 1))
        ;;
      integration/README.md)
        agentic_shared_file_is_safe "$entry" || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared 说明文件类型或权限不安全" \
          "请停止使用该安装目录并重新安装"
        integration_readme=$((integration_readme + 1))
        ;;
      integration/task-to-pr-event.schema.json)
        agentic_shared_file_is_safe "$entry" || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared JSON Schema 类型或权限不安全" \
          "请停止使用该安装目录并重新安装"
        event_schema=$((event_schema + 1))
        ;;
      integration/task-to-pr-manifest.schema.json)
        agentic_shared_file_is_safe "$entry" || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared JSON Schema 类型或权限不安全" \
          "请停止使用该安装目录并重新安装"
        manifest_schema=$((manifest_schema + 1))
        ;;
      integration/task-to-pr-result.schema.json)
        agentic_shared_file_is_safe "$entry" || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared JSON Schema 类型或权限不安全" \
          "请停止使用该安装目录并重新安装"
        result_schema=$((result_schema + 1))
        ;;
      standards)
        [ -d "$entry" ] || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared/standards 不是普通目录" \
          "请停止使用该安装目录并重新安装"
        standards_dir=$((standards_dir + 1))
        ;;
      standards/jira-comment-template.schema.json)
        agentic_shared_file_is_safe "$entry" || agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装中的 shared JSON Schema 类型或权限不安全" \
          "请停止使用该安装目录并重新安装"
        comment_template=$((comment_template + 1))
        ;;
      *)
        agentic_bootstrap_error \
          "developer_shared_distribution_invalid" \
          "AgenticOps developer 安装的 shared 可见树包含非准入路径：$relative" \
          "请停止使用该安装目录并通过 developer/bootstrap/install.sh 重新安装"
        ;;
    esac
    entry_count=$((entry_count + 1))
  done < <(find "$shared_dir" -mindepth 1 -print0 2>/dev/null)

  if [ "$entry_count" != "7" ] || [ "$integration_dir" != "1" ] || \
    [ "$integration_readme" != "1" ] || [ "$event_schema" != "1" ] || \
    [ "$manifest_schema" != "1" ] || [ "$result_schema" != "1" ] || \
    [ "$standards_dir" != "1" ] || [ "$comment_template" != "1" ]; then
    agentic_bootstrap_error \
      "developer_shared_distribution_invalid" \
      "AgenticOps developer 安装的 shared 可见树不等于固定只读协议白名单" \
      "请停止使用该安装目录并通过 developer/bootstrap/install.sh 重新安装"
  fi
}

agentic_expected_developer_sparse_paths() {
  printf '%s\n' \
    '.python-version' \
    'developer/AGENTS.md' \
    'developer/bootstrap' \
    'developer/pyproject.toml' \
    'developer/rules' \
    'developer/runtime' \
    'developer/skills' \
    'developer/standards' \
    'developer/uv.lock' \
    'shared/integration' \
    'shared/standards'
}

agentic_validate_developer_distribution() {
  local install_dir="$1"
  local developer_dir="$install_dir/developer"
  local entry=""
  local relative=""

  if [ -L "$developer_dir" ] || [ ! -d "$developer_dir" ]; then
    agentic_bootstrap_error \
      "developer_distribution_invalid" \
      "AgenticOps developer 安装缺少安全的生产资产目录" \
      "请停止使用该安装目录并通过 developer/bootstrap/install.sh 重新安装"
  fi

  while IFS= read -r -d '' entry; do
    relative="${entry#"$developer_dir"/}"
    case "$relative" in
      AGENTS.md|bootstrap|pyproject.toml|rules|runtime|skills|standards|uv.lock|.venv) ;;
      *)
        agentic_bootstrap_error \
          "developer_distribution_contaminated" \
          "AgenticOps developer 安装包含非生产顶层资产：developer/$relative" \
          "请停止使用该安装目录并重新安装；测试与 fixture 只能保留在源头仓库"
        ;;
    esac
  done < <(find "$developer_dir" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)

  for relative in \
    AGENTS.md bootstrap pyproject.toml rules runtime skills standards uv.lock; do
    if [ ! -e "$developer_dir/$relative" ] || [ -L "$developer_dir/$relative" ]; then
      agentic_bootstrap_error \
        "developer_distribution_invalid" \
        "AgenticOps developer 安装缺少安全的生产资产：developer/$relative" \
        "请停止使用该安装目录并重新安装"
    fi
  done

  entry="$(find "$developer_dir" \
    -path "$developer_dir/.venv" -prune -o \
    \( -type l -o -name tests -o -name test -o -name fixtures -o \
       -name __pycache__ -o -name '*.pyc' -o -name '*.pyo' -o \
       -name task_to_pr_producer.py -o -name '*fake*producer*' \) \
    -print -quit 2>/dev/null || true)"
  if [ -n "$entry" ]; then
    relative="${entry#"$install_dir"/}"
    agentic_bootstrap_error \
      "developer_distribution_contaminated" \
      "AgenticOps developer 安装混入测试、fixture、fake producer、缓存或符号链接：$relative" \
      "请停止使用该安装目录并重新安装；这些资产不得进入 developer 分发"
  fi
}

agentic_require_checkout_integrity() {
  local install_dir="$1"
  local mode="${2:-strict}"
  local head_ref=""
  local recorded_ref=""
  local pending_ref=""
  local previous_ref=""
  local tracked_changes=""
  local required_asset=""

  agentic_require_managed_paths_safe "$install_dir"
  head_ref="$(git -C "$install_dir" rev-parse --verify HEAD 2>/dev/null || true)"
  recorded_ref="$(cat "$install_dir/.local/current-ref" 2>/dev/null || true)"
  pending_ref="$(cat "$install_dir/.local/pending-rollback-ref" 2>/dev/null || true)"
  previous_ref="$(cat "$install_dir/.local/previous-ref" 2>/dev/null || true)"
  agentic_validate_ref_value "$head_ref" HEAD
  agentic_validate_ref_value "$recorded_ref" current-ref
  if [ -n "$pending_ref" ]; then
    agentic_validate_ref_value "$pending_ref" pending-rollback-ref
  fi
  if [ -n "$previous_ref" ]; then
    agentic_validate_ref_value "$previous_ref" previous-ref
  fi
  if { [ "$recorded_ref" != "$head_ref" ] && \
      { [ "$mode" != "allow-pending-rollback" ] || \
        [ -z "$pending_ref" ] || [ "$pending_ref" != "$recorded_ref" ]; }; }; then
    agentic_bootstrap_error \
      "install_ref_integrity_invalid" \
      "AgenticOps checkout 的 HEAD 与 .local/current-ref 不一致" \
      "请停止使用该目录，并通过正式 Bootstrap 重新安装或完成受控回滚"
  fi
  if ! git -C "$install_dir" cat-file -e "refs/remotes/origin/main^{commit}" 2>/dev/null || \
    ! git -C "$install_dir" merge-base --is-ancestor \
      "$head_ref" refs/remotes/origin/main 2>/dev/null || \
    { [ -n "$pending_ref" ] && ! git -C "$install_dir" merge-base --is-ancestor \
      "$pending_ref" refs/remotes/origin/main 2>/dev/null; }; then
    agentic_bootstrap_error \
      "install_ref_integrity_invalid" \
      "AgenticOps checkout 不可达于已获取的 origin/main" \
      "请停止使用该目录，并通过正式 Bootstrap 重新安装"
  fi
  if { [ -n "$previous_ref" ] && \
      { ! git -C "$install_dir" cat-file -e "$previous_ref^{commit}" 2>/dev/null || \
        ! git -C "$install_dir" merge-base --is-ancestor \
          "$previous_ref" refs/remotes/origin/main 2>/dev/null; }; } || \
    { [ -n "$pending_ref" ] && \
      ! git -C "$install_dir" cat-file -e "$pending_ref^{commit}" 2>/dev/null; }; then
    agentic_bootstrap_error \
      "install_ref_integrity_invalid" \
      "AgenticOps 本地 previous/pending ref 不可达于 origin/main" \
      "请停止使用该目录，并通过正式 Bootstrap 重新安装"
  fi
  agentic_validate_shared_source_tree "$install_dir" "$head_ref"
  tracked_changes="$(git -C "$install_dir" status --porcelain=v1 --untracked-files=no 2>/dev/null || true)"
  if [ -n "$tracked_changes" ]; then
    agentic_bootstrap_error \
      "install_tracked_changes_forbidden" \
      "AgenticOps developer 安装中的受管文件存在本地修改" \
      "请不要修改 ~/.agentic-ops；通过业务反馈流程改进后更新稳定 main"
  fi
  for required_asset in \
    developer/AGENTS.md \
    developer/bootstrap/ao-work \
    developer/runtime/src/ao_work/__init__.py \
    shared/integration/README.md \
    shared/integration/task-to-pr-manifest.schema.json \
    shared/integration/task-to-pr-event.schema.json \
    shared/integration/task-to-pr-result.schema.json; do
    if ! git -C "$install_dir" cat-file -e "HEAD:$required_asset" 2>/dev/null; then
      agentic_bootstrap_error \
        "install_asset_integrity_invalid" \
        "AgenticOps checkout 的提交中缺少受管资产：$required_asset" \
        "请通过正式 Bootstrap 重新安装"
    fi
  done
}

agentic_verify_developer_sparse_configuration() {
  local install_dir="$1"
  local sparse_paths=""
  local normalized=""
  local expected_paths=""

  agentic_require_managed_paths_safe "$install_dir"
  if [ "$(git -C "$install_dir" config --bool core.sparseCheckout 2>/dev/null || true)" != "true" ]; then
    agentic_bootstrap_error \
      "developer_sparse_checkout_invalid" \
      "AgenticOps managed clone 未启用 developer-only sparse checkout" \
      "请重新执行 developer/bootstrap/install.sh 安装"
  fi
  sparse_paths="$(git -C "$install_dir" sparse-checkout list 2>/dev/null || true)"
  normalized="$(printf '%s\n' "$sparse_paths" | sed -e 's#^/##' -e 's#/$##' | sed '/^$/d' | LC_ALL=C sort -u)"
  expected_paths="$(agentic_expected_developer_sparse_paths | LC_ALL=C sort -u)"
  if [ "$normalized" != "$expected_paths" ]; then
    agentic_bootstrap_error \
      "developer_sparse_checkout_invalid" \
      "AgenticOps managed clone 的 sparse checkout 不只包含 developer 资产" \
      "请重新执行 developer/bootstrap/install.sh 安装"
  fi
}

agentic_verify_developer_checkout() {
  local install_dir="$1"

  agentic_verify_developer_sparse_configuration "$install_dir"
  if [ -e "$install_dir/maintainer" ]; then
    agentic_bootstrap_error \
      "developer_distribution_contaminated" \
      "developer 安装混入 maintainer 工作面资产" \
      "请停止使用该安装目录并重新安装"
  fi
  agentic_validate_developer_distribution "$install_dir"
  agentic_validate_shared_source_tree "$install_dir" HEAD
}

agentic_verify_developer_checkout_for_verification() {
  local install_dir="$1"
  local expected_branch="$2"
  local current_branch=""
  local head_ref=""

  agentic_require_managed_paths_safe "$install_dir"
  head_ref="$(git -C "$install_dir" rev-parse --verify HEAD 2>/dev/null || true)"
  if [ -z "$head_ref" ]; then
    agentic_bootstrap_error \
      "install_checkout_invalid" \
      "安装工作树无法读取 HEAD：$install_dir" \
      "请重新清理验证安装目录后重试"
  fi
  current_branch="$(git -C "$install_dir" symbolic-ref -q --short HEAD 2>/dev/null || true)"
  if [ -z "$current_branch" ] || [ "$current_branch" != "$expected_branch" ]; then
    agentic_bootstrap_error \
      "install_checkout_invalid" \
      "验证安装未停留在期望分支：$install_dir -> ${current_branch:-(detached)}" \
      "请重新执行安装流程并指定正确源分支"
  fi
  agentic_verify_developer_sparse_configuration "$install_dir"
  if [ -e "$install_dir/maintainer" ]; then
    agentic_bootstrap_error \
      "developer_distribution_contaminated" \
      "developer 安装混入 maintainer 工作面资产" \
      "请重建验证安装目录"
  fi
  agentic_validate_developer_distribution "$install_dir"
  agentic_validate_shared_source_tree "$install_dir" "$head_ref"
}

agentic_sync_runtime_for_verification() {
  local install_dir="$1"
  local uv_bin="$2"
  local expected_branch="${3:-develop}"
  local head_ref=""

  agentic_verify_developer_checkout_for_verification "$install_dir" "$expected_branch"
  agentic_validate_shared_distribution "$install_dir"
  if ! "$uv_bin" sync --locked --project "$install_dir/developer" --python 3.12; then
    return 1
  fi
  if [ ! -e "$install_dir/bin" ]; then
    mkdir -m 0755 "$install_dir/bin"
  fi
  if ! install -m 0755 "$install_dir/developer/bootstrap/ao-work" "$install_dir/bin/ao-work"; then
    return 1
  fi
  head_ref="$(git -C "$install_dir" rev-parse HEAD)"
  agentic_write_refs "$install_dir" "" "$head_ref"
  "$install_dir/bin/ao-work" --help >/dev/null
}

agentic_configure_developer_sparse_checkout() {
  local install_dir="$1"

  agentic_require_managed_paths_safe "$install_dir"
  git -C "$install_dir" sparse-checkout set --no-cone \
    /developer/AGENTS.md \
    /developer/bootstrap/ \
    /developer/pyproject.toml \
    /developer/rules/ \
    /developer/runtime/ \
    /developer/skills/ \
    /developer/standards/ \
    /developer/uv.lock \
    /shared/integration/ \
    /shared/standards/ \
    /.python-version
  agentic_require_managed_paths_safe "$install_dir"
  agentic_verify_developer_sparse_configuration "$install_dir"
}

agentic_configure_developer_checkout() {
  local install_dir="$1"

  agentic_require_managed_clone "$install_dir"
  agentic_configure_developer_sparse_checkout "$install_dir"
}

agentic_sync_runtime() {
  local install_dir="$1"
  local uv_bin="$2"

  agentic_require_managed_paths_safe "$install_dir"
  agentic_verify_developer_checkout "$install_dir"
  agentic_validate_shared_distribution "$install_dir"
  if [ ! -f "$install_dir/developer/AGENTS.md" ] || \
    [ ! -f "$install_dir/developer/runtime/src/ao_work/__init__.py" ] || \
    [ ! -f "$install_dir/shared/integration/README.md" ] || \
    [ ! -f "$install_dir/shared/integration/task-to-pr-manifest.schema.json" ] || \
    [ ! -f "$install_dir/shared/integration/task-to-pr-event.schema.json" ] || \
    [ ! -f "$install_dir/shared/integration/task-to-pr-result.schema.json" ]; then
    agentic_bootstrap_error \
      "developer_distribution_invalid" \
      "developer 安装缺少 AI 入口、Python Runtime 或 shared 集成协议" \
      "请停止使用该安装目录并重新安装"
  fi
  if ! "$uv_bin" sync --locked --project "$install_dir/developer" --python 3.12; then
    return 1
  fi
  agentic_require_managed_paths_safe "$install_dir"
  if [ ! -e "$install_dir/bin" ]; then
    mkdir -m 0755 "$install_dir/bin"
  fi
  agentic_require_managed_paths_safe "$install_dir"
  if ! install -m 0755 \
    "$install_dir/developer/bootstrap/ao-work" "$install_dir/bin/ao-work"; then
    return 1
  fi
  agentic_require_managed_paths_safe "$install_dir"
  "$install_dir/bin/ao-work" --help >/dev/null
}

agentic_write_refs() {
  local install_dir="$1"
  local previous_ref="$2"
  local current_ref="$3"

  agentic_require_managed_paths_safe "$install_dir"
  if [ -n "$previous_ref" ]; then
    agentic_write_ref_atomic "$install_dir" previous-ref "$previous_ref"
  fi
  agentic_write_ref_atomic "$install_dir" current-ref "$current_ref"
}

agentic_require_safe_ref_file() {
  local install_dir="$1"
  local ref_name="$2"
  local path=""
  local ref_value=""

  case "$ref_name" in
    previous-ref|current-ref|pending-rollback-ref) ;;
    *) agentic_ref_path_error "$ref_name" ;;
  esac
  agentic_require_managed_paths_safe "$install_dir"
  path="$install_dir/.local/$ref_name"
  if [ -L "$path" ] || [ ! -f "$path" ] || [ ! -s "$path" ]; then
    agentic_bootstrap_error \
      "rollback_ref_invalid" \
      "回滚引用文件无效：$ref_name" \
      "请检查安装状态或重新安装"
  fi
  ref_value="$(cat "$path")"
  agentic_validate_ref_value "$ref_value" "$ref_name"
}

agentic_require_rollback_commit() {
  local install_dir="$1"
  local rollback_ref="$2"
  local required_asset=""
  if { [[ ! "$rollback_ref" =~ ^[0-9a-f]{40}$ ]] && \
      [[ ! "$rollback_ref" =~ ^[0-9a-f]{64}$ ]]; } || \
    ! git -C "$install_dir" cat-file -e "$rollback_ref^{commit}" 2>/dev/null || \
    ! git -C "$install_dir" merge-base --is-ancestor \
      "$rollback_ref" refs/remotes/origin/main 2>/dev/null; then
    agentic_bootstrap_error \
      "rollback_ref_invalid" \
      "回滚引用不是已获取 origin/main 可达的完整 commit" \
      "请停止回滚并重新安装或核对正式版本"
  fi
  agentic_validate_shared_source_tree "$install_dir" "$rollback_ref"
  for required_asset in \
    developer/AGENTS.md \
    developer/bootstrap/ao-work \
    developer/runtime/src/ao_work/__init__.py \
    shared/integration/README.md \
    shared/integration/task-to-pr-manifest.schema.json \
    shared/integration/task-to-pr-event.schema.json \
    shared/integration/task-to-pr-result.schema.json; do
    if ! git -C "$install_dir" cat-file -e "$rollback_ref:$required_asset" 2>/dev/null; then
      agentic_bootstrap_error \
        "rollback_ref_invalid" \
        "回滚提交缺少受管资产：$required_asset" \
        "请停止回滚并重新安装"
    fi
  done
}

agentic_configure_path() {
  local install_dir="$1"
  local default_install_dir="$HOME/.agentic-ops"
  local profile=""
  local path_line='export PATH="$HOME/.agentic-ops/bin:$PATH"'

  if [ "$install_dir" != "$default_install_dir" ]; then
    printf 'AgenticOps：自定义安装目录不会修改 shell profile；当前会话可执行 export PATH="%s/bin:$PATH"\n' "$install_dir" >&2
    return
  fi

  case "${SHELL:-}" in
    */zsh) profile="$HOME/.zshrc" ;;
    */bash)
      if [ "$(uname -s)" = "Darwin" ]; then
        profile="$HOME/.bash_profile"
      else
        profile="$HOME/.bashrc"
      fi
      ;;
    *)
      printf 'AgenticOps：未识别当前 shell，未修改 profile；当前会话可执行 export PATH="%s/bin:$PATH"\n' "$install_dir" >&2
      return
      ;;
  esac

  if [ -f "$profile" ] && grep -Fqx "$path_line" "$profile"; then
    return
  fi
  mkdir -p "$(dirname "$profile")"
  printf '\n%s\n' "$path_line" >> "$profile"
}

agentic_confirm_update() {
  local current_ref="$1"
  local target_ref="$2"
  local answer=""

  printf 'AgenticOps developer 更新确认\n' >&2
  printf '当前 ref：%s\n' "$current_ref" >&2
  printf '目标 ref：%s\n' "$target_ref" >&2
  if [ "$current_ref" = "$target_ref" ]; then
    return
  fi
  if [ "${AGENTIC_OPS_ASSUME_YES:-0}" = "1" ]; then
    return
  fi
  if [ ! -t 0 ]; then
    agentic_bootstrap_error \
      "update_confirmation_required" \
      "非交互更新缺少目标 ref 确认" \
      "核对当前与目标 ref 后显式设置 AGENTIC_OPS_ASSUME_YES=1"
  fi
  printf '确认更新 developer 安装？[y/N] ' >&2
  IFS= read -r answer || answer=""
  case "$answer" in
    y|Y|yes|YES) ;;
    *)
      agentic_bootstrap_error \
        "update_confirmation_rejected" \
        "研发工程师取消了 developer 更新" \
        "确认目标 ref 后重新执行 developer/bootstrap/update.sh"
      ;;
  esac
}
