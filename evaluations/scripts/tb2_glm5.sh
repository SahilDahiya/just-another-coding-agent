#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

MODEL="${MODEL:-ollama:glm-5:cloud}"
THINKING="${JUST_ANOTHER_CODING_AGENT_THINKING:-high}"
SUBMISSION_ID="${SUBMISSION_ID:-glm5-high}"
N_CONCURRENT="${N_CONCURRENT:-5}"

export MODEL
export JUST_ANOTHER_CODING_AGENT_THINKING="$THINKING"
export SUBMISSION_ID
export N_CONCURRENT

exec "$REPO_ROOT/evaluations/scripts/tb2_submission.sh" "$@"
