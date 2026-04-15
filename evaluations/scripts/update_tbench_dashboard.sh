#!/usr/bin/env bash
# Regenerate the Terminal Bench slice history JSON and drop it into the
# sahildahiya.me data directory so the /jaca/evaluation page builds
# against the latest runs. One command, one source of truth.
#
# This wrapper lives under evaluations/scripts/ so it stays in the
# evaluation tree, not mixed with general JACA app scripts.
#
# Usage:
#   evaluations/scripts/update_tbench_dashboard.sh
#   SAHIL_SITE=/path/to/sahildahiya.me evaluations/scripts/update_tbench_dashboard.sh
#   DASHBOARD_DATA=/custom/target.json evaluations/scripts/update_tbench_dashboard.sh
set -euo pipefail

# Repo root is two levels up from this script (evaluations/scripts/…).
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
site_root="${SAHIL_SITE:-$HOME/repos/sahildahiya.me}"
target="${DASHBOARD_DATA:-$site_root/src/data/tbench-slice-history.json}"

if [[ -z "${DASHBOARD_DATA:-}" && ! -d "$site_root" ]]; then
  echo "sahildahiya.me not found at $site_root" >&2
  echo "set SAHIL_SITE to override, or DASHBOARD_DATA to write elsewhere" >&2
  exit 2
fi

mkdir -p "$(dirname "$target")"

cd "$repo_root"
uv run python -m evaluations.scripts.analyze_slice_history \
  --quiet \
  --json "$target"

echo "dashboard data: $target"
