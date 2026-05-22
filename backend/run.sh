#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${WIKI_REPO_PATH:-}" ]; then
  echo "error: WIKI_REPO_PATH is not set." >&2
  echo "       Point it at the absolute path of the wiki repo, e.g.:" >&2
  echo "       export WIKI_REPO_PATH=/Users/you/codes/my-wiki-repo" >&2
  exit 1
fi

if [ ! -f ".venv/bin/activate" ]; then
  rm -rf .venv
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "Claude CLI: ${CLAUDE_CLI_PATH:-$(command -v claude || true)}"
echo "API Key: ${ANTHROPIC_API_KEY:+***set***}"
echo "Base URL: ${ANTHROPIC_BASE_URL:-default}"
echo "Claude permission mode: ${CLAUDE_PERMISSION_MODE:-default}"

# Ensure environment variables are inherited
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"

exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
