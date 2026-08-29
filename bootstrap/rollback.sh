#!/usr/bin/env bash
set -euo pipefail

install_root="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
previous_ref_file="$install_root/user/previous-ref"
test -f "$previous_ref_file" || { printf 'AgenticOps：没有可回退版本\n' >&2; exit 2; }
previous_ref="$(sed -n '1p' "$previous_ref_file")"
git -C "$install_root" cat-file -e "${previous_ref}^{commit}"
git -C "$install_root" checkout --detach "$previous_ref"
printf '%s\n' "$previous_ref" > "$install_root/user/current-ref"
chmod 0600 "$install_root/user/current-ref"
printf 'AgenticOps 已回退到：%s\n' "$previous_ref"
