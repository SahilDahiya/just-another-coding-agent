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
TASK_FILE="${TASK_FILE:?Set TASK_FILE to a newline-delimited task list.}"
SLICE_NAME="${SLICE_NAME:-$(basename "${TASK_FILE%.*}")}"
SLICE_BUNDLE_DIR="${JOBS_DIR}/submission-bundles/${SUBMISSION_ID}/slices/${SLICE_NAME}"
COMPLETED_JOBS_PATH="$SLICE_BUNDLE_DIR/completed-jobs.txt"
SLICE_CONFIG_PATH="$SLICE_BUNDLE_DIR/slice-config.env"

mkdir -p "$JOBS_DIR"
mkdir -p "$SLICE_BUNDLE_DIR"

export OLLAMA_BASE_URL
export JUST_ANOTHER_CODING_AGENT_THINKING="$THINKING"

if [[ ! -f "$TASK_FILE" ]]; then
  echo "Task file does not exist: $TASK_FILE" >&2
  exit 1
fi

mapfile -t SLICE_TASKS < <(grep -v '^[[:space:]]*$' "$TASK_FILE" | grep -v '^[[:space:]]*#')
if (( ${#SLICE_TASKS[@]} == 0 )); then
  echo "Task file is empty after removing blank lines/comments: $TASK_FILE" >&2
  exit 1
fi

write_slice_config() {
  printf "SLICE_MODEL=%q\n" "$MODEL" >"$SLICE_CONFIG_PATH"
  printf "SLICE_THINKING=%q\n" "$THINKING" >>"$SLICE_CONFIG_PATH"
  printf "SLICE_TARGET_TRIALS=%q\n" "$TARGET_TRIALS" >>"$SLICE_CONFIG_PATH"
  printf "SLICE_DATASET=%q\n" "$DATASET" >>"$SLICE_CONFIG_PATH"
  printf "SLICE_TASK_FILE=%q\n" "$TASK_FILE" >>"$SLICE_CONFIG_PATH"
}

ensure_slice_config() {
  if [[ -f "$SLICE_CONFIG_PATH" ]]; then
    # shellcheck disable=SC1090
    source "$SLICE_CONFIG_PATH"
    if [[ "$MODEL" != "$SLICE_MODEL" ]]; then
      echo "Slice bundle model mismatch: expected $SLICE_MODEL, got $MODEL." >&2
      exit 1
    fi
    if [[ "$THINKING" != "$SLICE_THINKING" ]]; then
      echo "Slice bundle thinking mismatch: expected $SLICE_THINKING, got $THINKING." >&2
      exit 1
    fi
    if [[ "$TARGET_TRIALS" != "$SLICE_TARGET_TRIALS" ]]; then
      echo "Slice bundle target trial mismatch: expected $SLICE_TARGET_TRIALS, got $TARGET_TRIALS." >&2
      exit 1
    fi
    if [[ "$DATASET" != "$SLICE_DATASET" ]]; then
      echo "Slice bundle dataset mismatch: expected $SLICE_DATASET, got $DATASET." >&2
      exit 1
    fi
    if [[ "$TASK_FILE" != "$SLICE_TASK_FILE" ]]; then
      echo "Slice bundle task file mismatch: expected $SLICE_TASK_FILE, got $TASK_FILE." >&2
      exit 1
    fi
    return
  fi

  write_slice_config
}

count_completed_passes() {
  if [[ ! -f "$COMPLETED_JOBS_PATH" ]]; then
    echo 0
    return
  fi
  grep -cve '^$' "$COMPLETED_JOBS_PATH"
}

show_status() {
  ensure_slice_config
  local completed_passes remaining_passes
  completed_passes="$(count_completed_passes)"
  remaining_passes=$((TARGET_TRIALS - completed_passes))
  if (( remaining_passes < 0 )); then
    remaining_passes=0
  fi

  echo "Submission slice status:"
  echo "  submission id: $SUBMISSION_ID"
  echo "  slice name: $SLICE_NAME"
  echo "  model: $MODEL"
  echo "  thinking: $THINKING"
  echo "  dataset: $DATASET"
  echo "  task file: $TASK_FILE"
  echo "  task count: ${#SLICE_TASKS[@]}"
  echo "  completed passes: $completed_passes/$TARGET_TRIALS"
  echo "  remaining passes: $remaining_passes"
  echo "  jobs dir: $JOBS_DIR"
  echo "  slice bundle dir: $SLICE_BUNDLE_DIR"
  if [[ -f "$COMPLETED_JOBS_PATH" ]]; then
    echo "  completed jobs:"
    sed 's/^/    - /' "$COMPLETED_JOBS_PATH"
  fi
}

run_pass() {
  local pass_number="$1"
  local timestamp job_name status
  local harbor_args=()
  timestamp="$(date +%Y%m%d-%H%M%S)"
  job_name="${SUBMISSION_ID}-${SLICE_NAME}-pass-${pass_number}-${timestamp}"

  echo "Launching Terminal Bench 2.0 submission slice pass:"
  echo "  submission id: $SUBMISSION_ID"
  echo "  slice name: $SLICE_NAME"
  echo "  pass: $pass_number/$TARGET_TRIALS"
  echo "  model: $MODEL"
  echo "  thinking: $THINKING"
  echo "  task file: $TASK_FILE"
  echo "  task count: ${#SLICE_TASKS[@]}"
  echo "  attempts in this Harbor job: 1"
  echo "  concurrency: $N_CONCURRENT"
  echo "  jobs dir: $JOBS_DIR"
  echo "  slice bundle dir: $SLICE_BUNDLE_DIR"
  echo "  job name: $job_name"
  echo "  note: authenticate Docker pulls first to avoid Harbor image pull rate limits"

  harbor_args=(
    --dataset "$DATASET"
    --agent-import-path just_another_coding_agent_adapters.harbor.agent:JustAnotherCodingAgentHarborAgent
    --model "$MODEL"
    --jobs-dir "$JOBS_DIR"
    --job-name "$job_name"
    --n-concurrent "$N_CONCURRENT"
    --n-attempts 1
    --artifact /logs/agent/just-another-coding-agent.txt
    --artifact /tmp/just-another-coding-agent-sessions
  )
  for task_name in "${SLICE_TASKS[@]}"; do
    harbor_args+=(--task-name "$task_name")
  done

  set +e
  PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  harbor run "${harbor_args[@]}"
  status=$?
  set -e

  if (( status != 0 )); then
    echo "Slice pass job did not complete cleanly and was not recorded in the submission bundle." >&2
    echo "Rerun the script to launch the same slice pass number again." >&2
    return "$status"
  fi

  printf "%s\n" "$job_name" >>"$COMPLETED_JOBS_PATH"
}

ensure_slice_config

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
  echo "Submission slice already has $completed_passes completed passes. Nothing left to run."
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
