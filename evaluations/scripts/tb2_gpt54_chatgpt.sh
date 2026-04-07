#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

MODEL="${MODEL:-openai-responses:gpt-5.4-chatgpt}"
THINKING="${JUST_ANOTHER_CODING_AGENT_THINKING:-high}"
SUBMISSION_ID="${SUBMISSION_ID:-gpt54-chatgpt-high}"
N_CONCURRENT="${N_CONCURRENT:-5}"

export MODEL
export JUST_ANOTHER_CODING_AGENT_THINKING="$THINKING"
export SUBMISSION_ID
export N_CONCURRENT

exec "$REPO_ROOT/evaluations/scripts/tb2_glm5.sh" "$@"
