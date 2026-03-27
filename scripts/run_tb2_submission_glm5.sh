#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if ! command -v harbor >/dev/null 2>&1; then
  echo "harbor is not installed or not on PATH." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed or not on PATH." >&2
  exit 1
fi

MODEL="${MODEL:-ollama:glm-5:cloud}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-https://ollama.com/v1}"
THINKING="${JUST_ANOTHER_CODING_AGENT_THINKING:-high}"
N_ATTEMPTS="${N_ATTEMPTS:-5}"
N_CONCURRENT="${N_CONCURRENT:-5}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
JOBS_DIR="${JOBS_DIR:-$REPO_ROOT/jobs}"
JOB_NAME="${JOB_NAME:-submission-glm5-high-$TIMESTAMP}"

export OLLAMA_BASE_URL
export JUST_ANOTHER_CODING_AGENT_THINKING="$THINKING"

: "${OLLAMA_API_KEY:?Set OLLAMA_API_KEY before running this script.}"

echo "Launching Terminal Bench 2.0 full run:"
echo "  model: $MODEL"
echo "  thinking: $THINKING"
echo "  attempts per task: $N_ATTEMPTS"
echo "  concurrency: $N_CONCURRENT"
echo "  jobs dir: $JOBS_DIR"
echo "  job name: $JOB_NAME"
echo "  note: authenticate Docker pulls first to avoid Harbor image pull rate limits"

PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
harbor run \
  --dataset terminal-bench@2.0 \
  --agent-import-path just_another_coding_agent_adapters.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model "$MODEL" \
  --jobs-dir "$JOBS_DIR" \
  --job-name "$JOB_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  --n-attempts "$N_ATTEMPTS" \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/just-another-coding-agent-sessions
