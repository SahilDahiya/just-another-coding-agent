#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

FULL_LAUNCHER="$REPO_ROOT/evaluations/scripts/run_tb2_submission.sh"
SLICE_LAUNCHER="$REPO_ROOT/evaluations/scripts/run_tb2_submission_slice.sh"

usage() {
  cat <<'EOF'
Usage:
  evaluations/scripts/tb2_submission.sh run <submission-id> [--passes N] [task-file ...]
  evaluations/scripts/tb2_submission.sh status <submission-id> [task-file ...]

Examples:
  MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh run chatgpt-high
  MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh status chatgpt-high
  MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh run chatgpt-high tasks/a.txt tasks/b.txt
  MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh status chatgpt-high tasks/a.txt
  MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh run chatgpt-high --passes 2 tasks/a.txt
EOF
}

if (( $# < 2 )); then
  usage >&2
  exit 1
fi

ACTION="$1"
SUBMISSION_ID="$2"
shift 2

PASSES_PER_RUN="1"
if [[ "${1:-}" == "--passes" ]]; then
  if [[ $# -lt 2 ]]; then
    echo "--passes requires a numeric value." >&2
    exit 1
  fi
  PASSES_PER_RUN="$2"
  shift 2
fi

case "$ACTION" in
  run|status)
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

if [[ ! -x "$FULL_LAUNCHER" ]]; then
  echo "Missing full launcher: $FULL_LAUNCHER" >&2
  exit 1
fi

if [[ ! -x "$SLICE_LAUNCHER" ]]; then
  echo "Missing slice launcher: $SLICE_LAUNCHER" >&2
  exit 1
fi

export SUBMISSION_ID
if [[ "$ACTION" == "run" ]]; then
  export PASSES_PER_RUN
  ACTION_ENV="run"
else
  ACTION_ENV="status"
fi

if (( $# == 0 )); then
  ACTION="$ACTION_ENV" "$FULL_LAUNCHER"
  exit 0
fi

for task_file in "$@"; do
  ACTION="$ACTION_ENV" TASK_FILE="$task_file" "$SLICE_LAUNCHER"
done
