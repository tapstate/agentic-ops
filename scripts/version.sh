#!/usr/bin/env bash
set -euo pipefail

state="${1:-INS}"

case "$state" in
  source|src|SRC) state="SRC" ;;
  install|ins|INS) state="INS" ;;
  dev|DEV) state="DEV" ;;
  *) echo "unsupported version state: $state" >&2; exit 1 ;;
esac

find_iteration_version() {
  local tag=""
  local count=""
  local best_tag=""
  local best_count=""
  for tag in $(git tag --merged HEAD --list 'v*' | grep -E '^v[0-9]+\.[0-9]+$' || true); do
    count="$(git rev-list --count "${tag}..HEAD")"
    if [ -z "$best_count" ] || [ "$count" -lt "$best_count" ]; then
      best_count="$count"
      best_tag="$tag"
    fi
  done
  tag="$best_tag"
  if [ -z "$tag" ]; then
    echo "missing iteration version tag; create one first, for example: git tag v0.1" >&2
    exit 1
  fi
  printf '%s' "$tag"
}

iteration_version="${AGENTIC_OPS_ITERATION_VERSION:-}"
commit_index="${AGENTIC_OPS_COMMIT_INDEX:-}"
commit="${AGENTIC_OPS_COMMIT:-}"

if [ "${AGENTIC_OPS_VERSION_TEST_MODE:-}" != "1" ]; then
  iteration_version="$(find_iteration_version)"
  commit_index="$(git rev-list --count "${iteration_version}..HEAD")"
  commit="$(git rev-parse --short HEAD)"
else
  iteration_version="${iteration_version:-v0.0}"
  commit_index="${commit_index:-0}"
  commit="${commit:-$(git rev-parse --short HEAD)}"
fi

if ! printf '%s\n' "$iteration_version" | grep -Eq '^v[0-9]+\.[0-9]+$'; then
  echo "unsupported iteration version: $iteration_version" >&2
  exit 1
fi
case "$commit_index" in
  ''|*[!0-9]*) echo "unsupported commit index: $commit_index" >&2; exit 1 ;;
esac
if [ -z "$commit" ]; then
  echo "missing commit" >&2
  exit 1
fi

printf '%s-%s.%s-%s\n' "$state" "$iteration_version" "$commit_index" "$commit"
