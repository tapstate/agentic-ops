#!/usr/bin/env bash

lifecycle_work_face() {
  case "$1" in
    source) printf '维护' ;;
    installed) printf '使用' ;;
    *) printf '未知' ;;
  esac
}

lifecycle_acquire_lock() {
  lifecycle_root="$1"
  lifecycle_operation="$2"
  lifecycle_lock_dir="$lifecycle_root/.local/lifecycle.lock"
  mkdir -p "$lifecycle_root/.local"
  chmod 0700 "$lifecycle_root/.local"

  if ! mkdir "$lifecycle_lock_dir" 2>/dev/null; then
    lifecycle_owner="$(sed -n '1p' "$lifecycle_lock_dir/owner" 2>/dev/null || true)"
    case "$lifecycle_owner" in
      ''|*[!0-9]*) lifecycle_owner='' ;;
    esac
    if [ -n "$lifecycle_owner" ] && kill -0 "$lifecycle_owner" 2>/dev/null; then
      printf 'AgenticOps：已有生命周期操作正在执行：pid=%s\n' "$lifecycle_owner" >&2
      return 2
    fi
    rm -f "$lifecycle_lock_dir/owner"
    rmdir "$lifecycle_lock_dir" 2>/dev/null || {
      printf 'AgenticOps：生命周期锁状态异常：%s\n' "$lifecycle_lock_dir" >&2
      return 2
    }
    mkdir "$lifecycle_lock_dir"
  fi
  chmod 0700 "$lifecycle_lock_dir"
  printf '%s\n' "$$" > "$lifecycle_lock_dir/owner"
  printf '%s\n' "$lifecycle_operation" > "$lifecycle_lock_dir/operation"
  chmod 0600 "$lifecycle_lock_dir/owner" "$lifecycle_lock_dir/operation"
  trap 'lifecycle_release_lock' EXIT
}

lifecycle_release_lock() {
  if [ -n "${lifecycle_lock_dir:-}" ] && [ -d "$lifecycle_lock_dir" ]; then
    lifecycle_recorded_owner="$(sed -n '1p' "$lifecycle_lock_dir/owner" 2>/dev/null || true)"
    if [ "$lifecycle_recorded_owner" = "$$" ]; then
      rm -f "$lifecycle_lock_dir/owner" "$lifecycle_lock_dir/operation"
      rmdir "$lifecycle_lock_dir" 2>/dev/null || true
    fi
  fi
}

lifecycle_require_clean_tree() {
  lifecycle_root="$1"
  lifecycle_face="$2"
  test -z "$(git -C "$lifecycle_root" status --porcelain)" || {
    printf 'AgenticOps：%s工作面存在未提交修改，拒绝更新\n' "$lifecycle_face" >&2
    return 2
  }
}

lifecycle_require_recorded_remote() {
  lifecycle_root="$1"
  lifecycle_expected_repository="$2"
  lifecycle_face="$3"
  lifecycle_actual_repository="$(git -C "$lifecycle_root" remote get-url origin)"
  test "$lifecycle_actual_repository" = "$lifecycle_expected_repository" || {
    printf 'AgenticOps：%s工作面 origin 与本地配置不一致，拒绝更新\n' "$lifecycle_face" >&2
    return 2
  }
}
