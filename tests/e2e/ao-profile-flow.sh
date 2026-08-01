#!/usr/bin/env bash
set -euo pipefail

export GOCACHE="${GOCACHE:-/tmp/agentic-ops-go-cache}"
export GOMODCACHE="${GOMODCACHE:-/tmp/agentic-ops-go-mod-cache}"

cmd=(go run ./packages/agentic-cli/cmd/agentic-cli)

"${cmd[@]}" profile validate --workspace ao | grep '"operation":"profile_validate"'
"${cmd[@]}" contract validate --path install-resources/basic/contracts/operations/feedback-analyze.yaml | grep '"operation":"contract_validate"'
"${cmd[@]}" contract validate --path install-resources/basic/contracts/operations/feedback-propose.yaml | grep '"operation":"contract_validate"'

printf '{"ok":true,"operation":"ao_profile_flow","workspace":"ao","target_repo":"tapstate/agentic-ops"}\n'
