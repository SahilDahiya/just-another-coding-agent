#!/usr/bin/env bash
# Resolve a Logfire write token from the project-local credentials
# directory and export it as LOGFIRE_TOKEN so every Harbor run in this
# shell lands in the configured Logfire project.
#
# Every harbor wrapper under evaluations/scripts/ sources this file at
# the top so you never have to think about it — running any of the
# tb2_*.sh or run_tb2_*.sh scripts will automatically pick up the
# project's write token without a pre-export.
#
# Usage (from a shell):
#   . evaluations/scripts/logfire_env.sh
#
# Usage (from another script):
#   . "$(dirname "$0")/logfire_env.sh"
#
# Precedence:
#   1. If LOGFIRE_TOKEN is already set in the environment, do nothing.
#   2. Otherwise read ${REPO_ROOT}/.logfire/logfire_credentials.json and
#      export its `write-token` (or `token`) field.
#   3. If neither works, print a one-line hint and continue without
#      exporting anything — downstream resolvers will still try their
#      own fallbacks, so this never breaks a working setup.

if [[ -n "${LOGFIRE_TOKEN:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi

_lf_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_lf_env_creds="$_lf_env_dir/.logfire/logfire_credentials.json"

if [[ ! -f "$_lf_env_creds" ]]; then
  echo "logfire_env.sh: $_lf_env_creds not found — run 'logfire auth' inside the repo, or set LOGFIRE_TOKEN manually" >&2
  unset _lf_env_dir _lf_env_creds
  return 0 2>/dev/null || exit 0
fi

_lf_env_token="$(python3 - <<'PYTHON' "$_lf_env_creds"
import json, sys
try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except Exception as exc:
    print("", end="")
    sys.stderr.write(f"logfire_env.sh: could not read credentials: {exc}\n")
    sys.exit(0)
token = ""
if isinstance(data, dict):
    token = data.get("write-token") or data.get("token") or ""
    if not token:
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            for value in tokens.values():
                if isinstance(value, str) and value:
                    token = value
                    break
                if isinstance(value, dict):
                    candidate = value.get("token")
                    if isinstance(candidate, str) and candidate:
                        token = candidate
                        break
print(token, end="")
PYTHON
)"

if [[ -z "$_lf_env_token" ]]; then
  echo "logfire_env.sh: no write token found in $_lf_env_creds — re-run 'logfire auth' inside the repo" >&2
  unset _lf_env_dir _lf_env_creds _lf_env_token
  return 0 2>/dev/null || exit 0
fi

export LOGFIRE_TOKEN="$_lf_env_token"
export LOGFIRE_SERVICE_NAME="${LOGFIRE_SERVICE_NAME:-jaca-harbor}"

unset _lf_env_dir _lf_env_creds _lf_env_token
