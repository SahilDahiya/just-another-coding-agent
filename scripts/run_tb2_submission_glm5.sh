#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ "${SKIP_DOTENV:-0}" != "1" && -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MODEL="${MODEL:-ollama:glm-5:cloud}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-https://ollama.com/v1}"
THINKING="${JUST_ANOTHER_CODING_AGENT_THINKING:-high}"
N_CONCURRENT="${N_CONCURRENT:-5}"
JOBS_DIR="${JOBS_DIR:-$REPO_ROOT/jobs}"
DATASET="terminal-bench@2.0"
TARGET_TRIALS="${TARGET_TRIALS:-5}"
PASSES_PER_RUN="${PASSES_PER_RUN:-1}"
ACTION="${ACTION:-run}"
SUBMISSION_ID="${SUBMISSION_ID:-submission-glm5-high}"
SUBMISSION_BUNDLE_DIR="${SUBMISSION_BUNDLE_DIR:-$JOBS_DIR/submission-bundles/$SUBMISSION_ID}"
COMPLETED_JOBS_PATH="$SUBMISSION_BUNDLE_DIR/completed-jobs.txt"
BUNDLE_CONFIG_PATH="$SUBMISSION_BUNDLE_DIR/bundle-config.env"

mkdir -p "$JOBS_DIR"
mkdir -p "$SUBMISSION_BUNDLE_DIR"

export OLLAMA_BASE_URL
export JUST_ANOTHER_CODING_AGENT_THINKING="$THINKING"

write_bundle_config() {
  printf "BUNDLE_MODEL=%q\n" "$MODEL" >"$BUNDLE_CONFIG_PATH"
  printf "BUNDLE_THINKING=%q\n" "$THINKING" >>"$BUNDLE_CONFIG_PATH"
  printf "BUNDLE_TARGET_TRIALS=%q\n" "$TARGET_TRIALS" >>"$BUNDLE_CONFIG_PATH"
  printf "BUNDLE_DATASET=%q\n" "$DATASET" >>"$BUNDLE_CONFIG_PATH"
}

ensure_bundle_config() {
  if [[ -f "$BUNDLE_CONFIG_PATH" ]]; then
    # shellcheck disable=SC1090
    source "$BUNDLE_CONFIG_PATH"
    if [[ "$MODEL" != "$BUNDLE_MODEL" ]]; then
      echo "Submission bundle model mismatch: expected $BUNDLE_MODEL, got $MODEL." >&2
      exit 1
    fi
    if [[ "$THINKING" != "$BUNDLE_THINKING" ]]; then
      echo "Submission bundle thinking mismatch: expected $BUNDLE_THINKING, got $THINKING." >&2
      exit 1
    fi
    if [[ "$TARGET_TRIALS" != "$BUNDLE_TARGET_TRIALS" ]]; then
      echo "Submission bundle target trial mismatch: expected $BUNDLE_TARGET_TRIALS, got $TARGET_TRIALS." >&2
      exit 1
    fi
    if [[ "$DATASET" != "$BUNDLE_DATASET" ]]; then
      echo "Submission bundle dataset mismatch: expected $BUNDLE_DATASET, got $DATASET." >&2
      exit 1
    fi
    return
  fi

  write_bundle_config
}

count_completed_passes() {
  if [[ ! -f "$COMPLETED_JOBS_PATH" ]]; then
    echo 0
    return
  fi
  grep -cve '^$' "$COMPLETED_JOBS_PATH"
}

show_status() {
  ensure_bundle_config
  local completed_passes remaining_passes
  completed_passes="$(count_completed_passes)"
  remaining_passes=$((TARGET_TRIALS - completed_passes))
  if (( remaining_passes < 0 )); then
    remaining_passes=0
  fi

  echo "Submission bundle status:"
  echo "  submission id: $SUBMISSION_ID"
  echo "  model: $MODEL"
  echo "  thinking: $THINKING"
  echo "  dataset: $DATASET"
  echo "  completed passes: $completed_passes/$TARGET_TRIALS"
  echo "  remaining passes: $remaining_passes"
  echo "  jobs dir: $JOBS_DIR"
  echo "  bundle dir: $SUBMISSION_BUNDLE_DIR"
  if [[ -f "$COMPLETED_JOBS_PATH" ]]; then
    echo "  completed jobs:"
    sed 's/^/    - /' "$COMPLETED_JOBS_PATH"
  fi
}

run_pass() {
  local pass_number="$1"
  local timestamp job_name status
  timestamp="$(date +%Y%m%d-%H%M%S)"
  job_name="${SUBMISSION_ID}-pass-${pass_number}-${timestamp}"

  echo "Launching Terminal Bench 2.0 submission pass:"
  echo "  submission id: $SUBMISSION_ID"
  echo "  pass: $pass_number/$TARGET_TRIALS"
  echo "  model: $MODEL"
  echo "  thinking: $THINKING"
  echo "  attempts in this Harbor job: 1"
  echo "  concurrency: $N_CONCURRENT"
  echo "  jobs dir: $JOBS_DIR"
  echo "  bundle dir: $SUBMISSION_BUNDLE_DIR"
  echo "  job name: $job_name"
  echo "  note: authenticate Docker pulls first to avoid Harbor image pull rate limits"

  set +e
  PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  harbor run \
    --dataset "$DATASET" \
    --agent-import-path just_another_coding_agent_adapters.harbor.agent:JustAnotherCodingAgentHarborAgent \
    --model "$MODEL" \
    --jobs-dir "$JOBS_DIR" \
    --job-name "$job_name" \
    --n-concurrent "$N_CONCURRENT" \
    --n-attempts 1 \
    --artifact /logs/agent/just-another-coding-agent.txt \
    --artifact /tmp/just-another-coding-agent-sessions
  status=$?
  set -e

  if (( status != 0 )); then
    echo "Pass job did not complete cleanly and was not recorded in the submission bundle." >&2
    echo "Rerun the script to launch the same pass number again." >&2
    return "$status"
  fi

  printf "%s\n" "$job_name" >>"$COMPLETED_JOBS_PATH"
}

ensure_bundle_config

if [[ "$ACTION" == "status" ]]; then
  show_status
  exit 0
fi

if [[ "$ACTION" != "run" ]]; then
  echo "Unsupported ACTION: $ACTION. Use ACTION=run or ACTION=status." >&2
  exit 1
fi

if ! command -v harbor >/dev/null 2>&1; then
  echo "harbor is not installed or not on PATH." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed or not on PATH." >&2
  exit 1
fi

: "${OLLAMA_API_KEY:?Set OLLAMA_API_KEY before running this script.}"

completed_passes="$(count_completed_passes)"
if (( completed_passes >= TARGET_TRIALS )); then
  echo "Submission bundle already has $completed_passes completed passes. Nothing left to run."
  exit 0
fi

passes_this_run="$PASSES_PER_RUN"
remaining_passes=$((TARGET_TRIALS - completed_passes))
if (( passes_this_run > remaining_passes )); then
  passes_this_run="$remaining_passes"
fi

for ((i = 0; i < passes_this_run; i += 1)); do
  pass_number=$((completed_passes + i + 1))
  run_pass "$pass_number"
done
