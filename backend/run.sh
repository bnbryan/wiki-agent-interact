#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${WIKI_REPO_PATH:-}" ]; then
  echo "error: WIKI_REPO_PATH is not set." >&2
  echo "       Point it at the absolute path of the wiki repo, e.g.:" >&2
  echo "       export WIKI_REPO_PATH=/Users/you/codes/my-wiki-repo" >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
