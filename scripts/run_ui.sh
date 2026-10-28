#!/usr/bin/env bash
# Start the ADK web UI showing ONLY the CycleSite agent (not helper folders like tests/).
#   ./scripts/run_ui.sh                       # uses .env if present
#   CYCLESITE_BACKEND=synthetic ./scripts/run_ui.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
ROOT="$(pwd)"
rm -rf adk_agents && mkdir adk_agents
# A copy, not a symlink: ADK refuses agent folders that resolve outside its base directory.
cp -r "$ROOT/cycle_agent" adk_agents/cycle_agent
find adk_agents -name __pycache__ -prune -exec rm -rf {} +
exec adk web --no-reload "${@}" adk_agents
